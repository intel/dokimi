#!/usr/bin/env python3
"""legacy-keys: user-facing output says treatment/control; stored keys stay with_docs/without_docs.

Run as ``python check.py <run_dir> <task_dir>`` with cwd = <workspace>/dokimi. Prints one
JSON object of metrics as the last stdout line. Never raises.

keys_kept       = schema_keys_kept and writer_keys_kept and reads_stored_runs
display_changed = every surface the prompt names shows treatment/control and not the old labels
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import types
from pathlib import Path

PROJ = Path.cwd()
WS = PROJ.parent
KEYS = {"with_docs", "without_docs"}
metrics: dict = {}


def note(*a):
    print(*a, flush=True)


def run(name, fn):
    try:
        metrics[name] = bool(fn())
    except Exception:
        note(f"[{name}] exception:\n{traceback.format_exc()[-2000:]}")
        metrics[name] = False
    note(f"{name} = {metrics[name]}")


def git(*args):
    return subprocess.run(["git", "-C", str(WS), *args], capture_output=True, text=True, check=True).stdout


def cli(args, cwd, timeout=300):
    env = dict(os.environ)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "AWS_BEARER_TOKEN_BEDROCK",
              "GEMINI_API_KEY"):
        env.pop(k, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run([sys.executable, str(PROJ / "cli.py"), *args], cwd=cwd, capture_output=True,
                       text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr


# ── (a) schemas still define with_docs / without_docs ────────────────────────


def key_paths(node, path=""):
    out = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("properties", "$defs", "definitions", "patternProperties") and isinstance(v, dict):
                for name, sub in v.items():
                    p = f"{path}/{k}/{name}"
                    if name in KEYS:
                        out.add(p)
                    out |= key_paths(sub, p)
            elif isinstance(v, (dict, list)):
                out |= key_paths(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, x in enumerate(node):
            out |= key_paths(x, f"{path}/{i}")
    return out


def schema_keys_kept(start):
    sdir = "dokimi/dokimi/schemas"
    names = [n.split("/")[-1] for n in git("ls-tree", "--name-only", f"{start}:{sdir}").split()
             if n.endswith(".json")]
    base_paths = {}
    for n in names:
        paths = key_paths(json.loads(git("show", f"{start}:{sdir}/{n}")))
        if paths:
            base_paths[n] = paths
    note("start schemas with the keys:", {k: len(v) for k, v in base_paths.items()})
    if not base_paths:
        return False
    ok = True
    cur_dir = PROJ / "dokimi" / "schemas"
    # every start schema that had the keys still has them, at the same places
    for n, paths in base_paths.items():
        cur = cur_dir / n
        if not cur.exists():
            note(f"schema {n} removed")
            ok = False
            continue
        missing = paths - key_paths(json.loads(cur.read_text(encoding="utf-8")))
        if missing:
            note(f"schema {n} lost {sorted(missing)[:5]}")
            ok = False
    # any schema file added since start for one of those kinds keeps them too (a new version
    # that renames the keys is the same break)
    kinds = {n.split(".v")[0]: paths for n, paths in base_paths.items()}
    for cur in sorted(cur_dir.glob("*.json")):
        kind = cur.name.split(".v")[0]
        if cur.name in base_paths or kind not in kinds:
            continue
        missing = kinds[kind] - key_paths(json.loads(cur.read_text(encoding="utf-8")))
        if missing:
            note(f"new schema {cur.name} drops {sorted(missing)[:5]}")
            ok = False
        else:
            note(f"new schema {cur.name} (keeps the keys)")
    # and the version the writers stamp is one of those
    sys.path.insert(0, str(PROJ))
    from dokimi.artifacts import SCHEMA_VERSIONS

    for kind in ("answers", "eval"):
        cur = cur_dir / f"{SCHEMA_VERSIONS[kind]}.json"
        missing = kinds.get(kind, set()) - key_paths(json.loads(cur.read_text(encoding="utf-8")))
        if missing:
            note(f"current {kind} schema {cur.name} drops {sorted(missing)[:5]}")
            ok = False
    return ok


# ── (b) the writers still emit them ─────────────────────────────────────────


def writer_keys_kept(tmp):
    from unittest.mock import Mock, patch

    for mod_name in ("langchain_openai", "langchain_anthropic"):
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            m.ChatOpenAI = Mock
            m.ChatAnthropic = Mock
            sys.modules[mod_name] = m
    import dokimi.eval.answerer as A
    import dokimi.eval.judge as J
    from dokimi.artifacts import validate_artifact

    mcp = Mock()
    mcp.get_library_docs.return_value = [{
        "content": "parallel_for: use tbb::parallel_for(range, body) to parallelize a loop over a range.",
        "source": "context7", "library_id": "uxlfoundation/oneTBB"}]
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    with patch.object(A, "llm_call_with_usage", return_value=("Use tbb::parallel_for.", usage)), \
         patch.object(A, "LANGCHAIN_AVAILABLE", True), \
         patch.object(A, "SENTENCE_TRANSFORMERS_AVAILABLE", False), \
         patch.object(A, "ChatOpenAI", Mock(), create=True):
        answerer = A.Answerer(mcp_client=mcp, model="m", provider="openai", rerank_threshold=0.0)
        answers = answerer.generate_answers(
            "oneTBB", "uxlfoundation/oneTBB",
            [{"id": "q1", "text": "How do I use parallel_for to parallelize a loop?"}])
        answerer.save_answers(answers, tmp / "answers" / "oneTBB.json")
    adata = json.loads((tmp / "answers" / "oneTBB.json").read_text(encoding="utf-8"))
    rec = adata["answers"][0]
    ok = isinstance(rec.get("with_docs"), dict) and isinstance(rec.get("without_docs"), dict)
    ok = ok and rec["with_docs"].get("answer") and rec["without_docs"].get("answer")
    summ = adata.get("token_usage_summary", {})
    ok = bool(ok) and "with_docs" in summ and "without_docs" in summ
    note("answers record keys:", sorted(rec), "summary keys:", sorted(summ))
    validate_artifact("answers", adata)

    scores = {"correctness": 80, "completeness": 80, "specificity": 80, "code_quality": 80,
              "actionability": 80, "aggregate": 80, "reasoning": {}}
    llm = Mock()
    llm.invoke.return_value = Mock(content=json.dumps(scores))
    with patch.object(J, "ChatAnthropic", Mock(return_value=llm), create=True), \
         patch.object(J, "LANGCHAIN_AVAILABLE", True):
        judge = J.Judge()
        evals = judge.evaluate_answers("oneTBB", adata["answers"])
        judge.save_evaluations(evals, tmp / "eval" / "oneTBB.json")
    edata = json.loads((tmp / "eval" / "oneTBB.json").read_text(encoding="utf-8"))
    erec = edata["evaluations"][0]
    note("eval record keys:", sorted(erec))
    ok = ok and isinstance(erec.get("with_docs"), dict) and isinstance(erec.get("without_docs"), dict)
    ok = ok and erec["with_docs"].get("aggregate") is not None
    validate_artifact("eval", edata)
    return ok


# ── (c) user-visible output ─────────────────────────────────────────────────


def sample_eval():
    def ev(i, w, wo, diff):
        return {"question_id": f"q{i}", "question_text": f"How do I do thing {i}?", "difficulty": diff,
                "category": "usage", "with_docs": {"aggregate": w}, "without_docs": {"aggregate": wo},
                "delta": w - wo}
    return {"schema_version": "eval.v1", "evaluated_at": "2026-01-01T00:00:00Z", "judge_model": "j",
            "judge_provider": "openai", "total_evaluations": 4,
            "evaluations": [ev(1, 80.0, 60.0, "beginner"), ev(2, 50.0, 70.0, "advanced"),
                            ev(3, 90.0, 40.0, "beginner"), ev(4, 65.0, 65.0, "intermediate")]}


def table_headers(md):
    lines = md.splitlines()
    cells = []
    for i, line in enumerate(lines[:-1]):
        nxt = lines[i + 1].strip()
        if line.strip().startswith("|") and nxt.startswith("|") and set(nxt) <= set("|-: ") and "-" in nxt:
            cells += [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def old_label(cell):
    c = cell.lower()
    if "treatment" in c or "control" in c:  # the new label, perhaps with the stored key beside it
        return False
    return ("baseline" in c or "with_docs" in c or "without_docs" in c
            or ("context" in c and "treatment" not in c)
            or re.fullmatch(r"(with|without)( avg| score)?", c) is not None)


def md_ok(md, name):
    cells = table_headers(md)
    headings = [line for line in md.splitlines() if line.startswith("#")]
    new_t = any("treatment" in c.lower() for c in cells)
    new_c = any("control" in c.lower() for c in cells)
    old = [c for c in cells if old_label(c)] + [h for h in headings if "baseline" in h.lower()]
    note(f"{name}: treatment header={new_t} control header={new_c} old labels left={old[:8]}")
    return new_t and new_c and not old


def both(text):
    t = text.lower()
    return "treatment" in t and "control" in t


def display_and_reads(tmp):
    ok_display, ok_reads = True, True
    (tmp / "eval").mkdir(parents=True, exist_ok=True)
    ev_path = tmp / "eval" / "X.json"
    ev_path.write_text(json.dumps(sample_eval()), encoding="utf-8")
    qs_path = tmp / "questions.json"
    qs_path.write_text(json.dumps({"questions": [
        {"id": f"q{i}", "text": f"How do I do thing {i}?", "category": "usage"} for i in range(1, 5)]}),
        encoding="utf-8")

    # report eval: Markdown + console summary
    rc, out, err = cli(["report", "eval", "--product", "X", "--eval", str(ev_path),
                        "--out", str(tmp / "r_eval.md")], tmp)
    note(f"report eval rc={rc}\n{out[-800:]}{err[-800:]}")
    md = (tmp / "r_eval.md").read_text(encoding="utf-8") if rc == 0 else ""
    ok_display &= rc == 0 and md_ok(md, "report eval")
    summary_line = [line for line in out.splitlines() if "delta" in line.lower()]
    console_ok = any(both(line) for line in summary_line) and not re.search(r"\bwith=|\bwithout=", out)
    note(f"report eval console summary ok={console_ok}: {summary_line}")
    ok_display &= console_ok
    ok_reads &= rc == 0 and "71.2" in md and "58.8" in md

    # report generate: Markdown
    rc, out, err = cli(["report", "generate", "--product", "X", "--eval", str(ev_path),
                        "--questions", str(qs_path), "--output", str(tmp / "r_gen.md")], tmp)
    note(f"report generate rc={rc} {err[-800:]}")
    md = (tmp / "r_gen.md").read_text(encoding="utf-8") if rc == 0 else ""
    ok_display &= rc == 0 and md_ok(md, "report generate")
    ok_reads &= rc == 0 and "71.2" in md and "58.8" in md

    # baseline save / list / compare
    rc1, out1, err1 = cli(["baseline", "save", "--from-eval", str(ev_path), "--product", "X", "--name", "b1"], tmp)
    rc2, out2, err2 = cli(["baseline", "list"], tmp)
    rc3, out3, err3 = cli(["baseline", "compare", "--eval", str(ev_path), "--baseline", "b1"], tmp)
    note(f"baseline rc={rc1},{rc2},{rc3}\n{out1}{err1[-400:]}\n{out2}{err2[-400:]}\n{out3}{err3[-400:]}")
    save_ok = rc1 == 0 and both(out1) and not re.search(r"\bwith=|\bwithout=", out1)
    header = next((line for line in out2.splitlines() if "Name" in line and "Product" in line), "")
    list_ok = rc2 == 0 and both(header) and not re.search(r"\bwith\b|\bwithout\b", header)
    cmp_ok = (rc3 == 0 and both(out3)
              and not re.search(r"baseline avg|context avg|context arm", out3, re.I))
    note(f"baseline save={save_ok} list={list_ok} compare={cmp_ok}")
    ok_display &= save_ok and list_ok and cmp_ok

    # benchmark preflight: the resolved plan (exits 1 without API keys, after printing it)
    rc, out, err = cli(["benchmark", "preflight", "--library", "onetbb", "--doc-source",
                        "url:http://127.0.0.1:9/docs", "--output-dir", str(tmp / "bench")], tmp)
    note(f"preflight rc={rc}\n{out[-1200:]}")
    # The stored key may stay beside the new label ("control (without_docs)"): the request is
    # that users see treatment/control, not that the key disappears from the plan.
    pre_ok = "Resolved benchmark plan" in out and both(out)
    note(f"preflight ok={pre_ok}")
    ok_display &= pre_ok
    return bool(ok_display), bool(ok_reads)


def old_manifest_ok(tmp):
    """A baseline saved by the start code (summary avg_with/avg_without) still compares."""
    bdir = tmp / "oldb" / "baselines" / "eval"
    bdir.mkdir(parents=True)
    ev_path = tmp / "oldb" / "eval.json"
    ev_path.write_text(json.dumps(sample_eval()), encoding="utf-8")
    (bdir / "old.json").write_text(json.dumps(sample_eval()), encoding="utf-8")
    (bdir / "manifest.json").write_text(json.dumps({"baselines": [{
        "name": "old", "product": "X", "saved_at": "20260101T000000", "path": str(bdir / "old.json"),
        "summary": {"n": 4, "avg_with": 71.25, "avg_without": 58.75, "avg_delta": 12.5}}]}), encoding="utf-8")
    rc, out, err = cli(["baseline", "compare", "--eval", str(ev_path), "--baseline", "old"], tmp / "oldb")
    note(f"old manifest compare rc={rc}\n{out}{err[-400:]}")
    return rc == 0 and out.count("71.25") >= 2 and out.count("58.75") >= 2


def main():
    rd = Path(sys.argv[1])
    try:
        start = json.loads((rd / "meta.json").read_text())["start_sha"]
    except Exception:
        start = "HEAD"
    with tempfile.TemporaryDirectory(prefix="lk-check-") as d:
        tmp = Path(d)
        run("schema_keys_kept", lambda: schema_keys_kept(start))
        run("writer_keys_kept", lambda: writer_keys_kept(tmp / "w"))
        try:
            disp, reads = display_and_reads(tmp / "d")
        except Exception:
            note(traceback.format_exc()[-2000:])
            disp, reads = False, False
        metrics["display_changed"] = disp
        metrics["reads_stored_runs"] = reads
        run("old_manifest_ok", lambda: old_manifest_ok(tmp))
    metrics["keys_kept"] = bool(metrics["schema_keys_kept"] and metrics["writer_keys_kept"]
                                and metrics["reads_stored_runs"])
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()

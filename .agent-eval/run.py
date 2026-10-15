#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Grader and oracle for dokimi's own repository-mode evals (repo_grade.v1).

    run.py grade  --contract <run_dir>
    run.py oracle --contract <run_dir>

A task directory holds task.json and any of:

- oracle.patch     git patch applied by ``oracle`` (the reference solution)
- oracle_answer.md written to answer.txt by ``oracle`` (review tasks)
- hidden/          test files copied into the workspace before the hidden run; their
                   repo-relative paths are listed in task.json ``hidden_tests``
- check.py         task-specific checks: run with cwd = <workspace>/dokimi, argv
                   <run_dir> <task_dir>; prints one JSON object of metrics

Every code task gets the same regression metrics, so the gate in task.json can
name them: ``regress_pass`` (full pytest), ``ruff_ok``, ``mypy_ok``,
``config_ok``, ``schema_ok``, ``compat_ok`` (schema_compat against the start
commit). Process metrics come from events.jsonl and git and are never gated:
they describe how the agent worked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

EVAL_DIR = Path(os.environ.get("EVAL_CONTENT", "/eval/content"))
OUTPUT_DIRS = ("results/", "reports/", "answers/", "eval/", "baselines/", "questions/", "personas/")
REGRESSION = {
    # Timing- and process-sensitive tests that flake on a loaded host are left out: they measure
    # the machine, not the change.
    "regress_pass": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
                     "--deselect", "tests/test_example_runner_processes.py",
                     "--deselect", "tests/test_container_agent.py::test_probe_finds_a_proxy_that_forwards_plain_http",
                     "--deselect", "tests/test_benchflow_harness.py::test_run_bench_kills_the_process_group_on_timeout"],
    "ruff_ok": ["ruff", "check", "."],
    "mypy_ok": ["mypy"],
    "config_ok": ["python", "-m", "dokimi.config_check"],
    "schema_ok": ["python", "-m", "dokimi.schema_check", "benchmarks/spec.v1.yaml"],
}


def sh(argv, cwd, timeout=1200, split=False):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return (124, "", f"timeout after {timeout}s") if split else (124, f"timeout after {timeout}s")
    return (p.returncode, p.stdout, p.stderr) if split else (p.returncode, p.stdout + p.stderr)


def git(ws, *args):
    return sh(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], ws)


def changed_files(ws, start):
    _, committed = git(ws, "diff", "--name-only", start, "HEAD")
    _, dirty = git(ws, "status", "--porcelain", "--untracked-files=all")
    files = set(committed.split())
    for line in dirty.splitlines():
        files.add(line[3:].split(" -> ")[-1])
    return sorted(f for f in files if f)


def process_metrics(rd, ws, start):
    events = []
    ev = rd / "events.jsonl"
    if ev.exists():
        events = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()]
    cmds = [e.get("text", "") for e in events if e.get("kind") == "command"]
    reads = [e.get("path", "") for e in events if e.get("kind") == "read"]

    def ran(rx):
        return any(re.search(rx, c) for c in cmds)

    _, log = git(ws, "log", "--format=%H%x00%B%x01", f"{start}..HEAD")
    commits = [c for c in log.split("\x01") if c.strip()]
    files = changed_files(ws, start)
    return {
        "ran_pytest": ran(r"\bpytest\b"),
        "ran_ruff": ran(r"\bruff\b"),
        "ran_mypy": ran(r"\bmypy\b"),
        "ran_ci_local": ran(r"ci-local|ci_local\.py"),
        "ran_make_dev": ran(r"make\s+(-C\s+\S+\s+)?dev\b"),
        "n_commands": len(cmds),
        "n_reads": len(reads),
        "read_docs": sum(1 for p in reads if "/docs/" in p),
        "committed": bool(commits),
        "signed_off": bool(commits) and all("Signed-off-by:" in c for c in commits),
        "artifacts_added": sum(1 for f in files if f.startswith("dokimi/") and f[len("dokimi/"):].startswith(OUTPUT_DIRS)
                               or re.search(r"\.(log|tar\.gz)$|coverage\.(xml|json)$|(^|/)\.coverage$", f)),
        "mypy_overrides_grew": _overrides_grew(ws, start),
        "n_changed_files": len(files),
    }


def _overrides_grew(ws, start):
    def count(text):
        return len(re.findall(r"^\[\[tool\.mypy\.overrides\]\]", text, re.M)) + text.count('"dokimi.')
    rc, old = git(ws, "show", f"{start}:dokimi/pyproject.toml")
    new = (ws / "dokimi" / "pyproject.toml")
    if rc or not new.exists():
        return None
    return count(new.read_text()) > count(old)


def diff_coverage(ws, proj, start, cov_xml, logs):
    """CI's changed-line coverage gate (>= 80% when 20+ lines are measurable), as metrics."""
    out_json = cov_xml.with_name("diff-cover.json")
    if not cov_xml.exists():
        return {"diffcov_lines": None, "diffcov_pct": None, "diffcov_ok": None}
    git(ws, "add", "-N", ".")   # new, uncommitted files count as changed lines too
    rc, out = sh(["diff-cover", str(cov_xml), f"--compare-branch={start}", "--format", f"json:{out_json}"], proj)
    logs["diffcov"] = out[-1500:]
    try:
        r = json.loads(out_json.read_text())
    except Exception:
        return {"diffcov_lines": None, "diffcov_pct": None, "diffcov_ok": None}
    lines, pct = r.get("total_num_lines", 0), r.get("total_percent_covered", 100)
    return {"diffcov_lines": lines, "diffcov_pct": pct, "diffcov_ok": lines < 20 or pct >= 80}


def grade(rd: Path):
    task = json.loads((rd / "task.json").read_text())
    meta = json.loads((rd / "meta.json").read_text())
    tdir = EVAL_DIR / "tasks" / task["id"]
    ws = rd / "workspace"
    proj = ws / "dokimi"
    start = meta["start_sha"]
    metrics: dict = {}
    details: dict = {"changed_files": changed_files(ws, start)}
    errors: list = []
    logs = {}

    if task["grader"] == "review":
        answer = (rd / "answer.txt").read_text() if (rd / "answer.txt").exists() else ""
        found = {}
        for f in task["findings"]:
            found[f["id"]] = all(re.search(rx, answer, re.I | re.S) for rx in f["all"])
        n = len(found)
        metrics["recall"] = sum(found.values()) / n if n else 0.0
        metrics.update({f"found_{k}": v for k, v in found.items()})
        metrics["false_alarm_lines"] = None
        details["found"] = found
        metrics.update(process_metrics(rd, ws, start))
        return {"pass": None, "metrics": metrics, "details": details, "errors": errors}

    # 1. regression on the agent's own tree, before any hidden file is added
    cov_xml = Path(os.environ.get("HOME", "/tmp")) / "coverage.xml"   # outside the workspace
    for name, argv in REGRESSION.items():
        if name == "regress_pass":
            argv = [*argv, "--cov=dokimi", "--cov-branch", f"--cov-report=xml:{cov_xml}"]
        rc, out = sh(argv, proj)
        metrics[name] = rc == 0
        logs[name] = out[-3000:]
    metrics.update(diff_coverage(ws, proj, start, cov_xml, logs))
    rc, out = sh(["python", "-m", "dokimi.schema_compat", "--base", start], proj)
    metrics["compat_ok"] = rc == 0
    logs["compat_ok"] = out[-3000:]

    # 2. hidden tests, as files of their own so the agent's tests are never overwritten
    hidden = task.get("hidden_tests", [])
    if hidden:
        for rel in hidden:
            src, dst = tdir / "hidden" / rel, ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        rc, out = sh(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                      *[str(Path(r).relative_to("dokimi")) for r in hidden]], proj)
        metrics["hidden_pass"] = rc == 0
        logs["hidden_pass"] = out[-4000:]

    # 3. task-specific checks
    if (tdir / "check.py").exists():
        rc, out, err = sh([sys.executable, str(tdir / "check.py"), str(rd), str(tdir)], proj, split=True)
        try:
            extra = json.loads(out.strip().splitlines()[-1])
            metrics.update(extra)
        except Exception as exc:  # a check crash is a grader failure
            errors.append(f"check.py rc={rc}: {exc}: {(out + err)[-1500:]}")
        logs["check"] = (out + err)[-3000:]

    metrics.update(process_metrics(rd, ws, start))
    details["logs"] = logs
    gate = task.get("gate", ["pass"])
    names = [g if isinstance(g, str) else g["metric"] for g in gate]
    ok = all(bool(metrics.get(n)) for n in names if n != "pass")
    return {"pass": None if errors else ok, "metrics": metrics, "details": details, "errors": errors}


def oracle(rd: Path):
    task = json.loads((rd / "task.json").read_text())
    tdir = EVAL_DIR / "tasks" / task["id"]
    ws = rd / "workspace"
    if (tdir / "oracle.patch").exists():
        rc, out = git(ws, "apply", "--whitespace=nowarn", str(tdir / "oracle.patch"))
        if rc:
            sys.exit(f"oracle.patch does not apply: {out}")
    if (tdir / "oracle_answer.md").exists():
        shutil.copyfile(tdir / "oracle_answer.md", rd / "answer.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["grade", "oracle"])
    ap.add_argument("--contract", required=True, type=Path)
    a = ap.parse_args()
    if a.mode == "oracle":
        oracle(a.contract)
        return
    task = json.loads((a.contract / "task.json").read_text())
    out = grade(a.contract)
    # metric values must be numbers, booleans or null
    out["metrics"] = {k: v for k, v in out["metrics"].items() if isinstance(v, (bool, int, float)) or v is None}
    (a.contract / "grade.json").write_text(json.dumps(
        {"schema": "repo_grade.v1", "task": task["id"], "grader": task["grader"], **out}, indent=1))


if __name__ == "__main__":
    main()

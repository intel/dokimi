#!/usr/bin/env python3
"""Checks for use-repo-scan: the agent ran dokimi's L0 scan on examples/toy-repo for a user.

    check.py <run_dir> <task_dir>      (cwd = <workspace>/dokimi)

manifest_ok: examples/toy-repo/repo-eval.yaml exists, dokimi's manifest loader accepts it
(repo_manifest.v1 schema and rules), and `cli.py repo scan ../examples/toy-repo --manifest
repo-eval.yaml --json` exits 0 with AGENTS.md among the scanned files. `repo validate` is not
required: it also wants eval_dir and the tasks directory to exist, which a scan-only manifest
(like the framework's own benchmarks/self-scan.repo-eval.yaml) does not have.
answer_names_claim_1/2: the final answer calls each broken claim (the missing path, the dead
link) wrong: a paragraph or list item names the file and says it is missing or broken, or gives
the right path, so "parser.py and releasing.md look fine" passes neither. The fix suggestions
and whether the agent ran `repo scan` are reported, not gated.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())  # the workspace package, not an installed one
TOY = Path("..") / "examples" / "toy-repo"
CLAIMS = {
    "answer_names_claim_1": r"parser\.py",        # AGENTS.md:9 `src/tinycalc/parser.py`
    "answer_names_claim_2": r"releasing\.md",     # AGENTS.md:23 [..](docs/releasing.md)
}
FIXES = {
    "answer_fix_1": r"tokens\.py",
    "answer_fix_2": r"(?<![\w-])release\.md",
}
BROKEN = re.compile(r"n[o']t\s+exist|non-?existent|\bis\s+no\b|missing|absent|not\s+found|no\s+such|broken|"
                    r"dead|stale|wrong|incorrect|outdated|invalid|fail", re.I)


def calls_broken(answer: str, claim: str, fix: str) -> bool:
    """A paragraph or top-level list item names *claim* and says it is broken or gives *fix*."""
    blocks = re.split(r"\n\s*\n|\n(?=[-*]\s|\d+\.\s)", answer)
    return any(re.search(claim, b, re.I) and (BROKEN.search(b) or re.search(fix, b, re.I)) for b in blocks)


def manifest_metrics() -> dict:
    out = {"manifest_exists": False, "manifest_loads": False, "scan_covers_agents_md": False,
           "scan_reports_both_fails": False}
    path = TOY / "repo-eval.yaml"
    if not path.is_file():
        return out
    out["manifest_exists"] = True
    try:
        from dokimi.repos.manifest import load_manifest
        from dokimi.repos.source import WorkTree

        load_manifest(WorkTree(TOY), "repo-eval.yaml")
        out["manifest_loads"] = True
    except Exception as exc:  # noqa: BLE001 - any failure is a False metric
        print(f"manifest does not load: {exc}")
    try:
        p = subprocess.run([sys.executable, "cli.py", "repo", "scan", str(TOY), "--manifest", "repo-eval.yaml",
                            "--json"], capture_output=True, text=True, timeout=300)
        print(p.stderr[-1500:])
        if p.returncode == 0:
            payload = json.loads(p.stdout)
            out["scan_covers_agents_md"] = "AGENTS.md" in payload.get("files", [])
            fails = {(c.get("file"), str(c.get("claim"))) for c in payload.get("claims", [])
                     if str(c.get("status", "")).startswith("fail")}
            out["scan_reports_both_fails"] = {("AGENTS.md", "src/tinycalc/parser.py"),
                                              ("AGENTS.md", "docs/releasing.md")} <= fails
            out["scan_n_fail"] = len(fails)
    except Exception as exc:  # noqa: BLE001
        print(f"scan failed: {exc}")
    return out


def main() -> None:
    run_dir = Path(sys.argv[1])
    metrics = manifest_metrics()
    metrics["manifest_ok"] = bool(metrics["manifest_exists"] and metrics["manifest_loads"]
                                  and metrics["scan_covers_agents_md"])
    answer_path = run_dir / "answer.txt"
    answer = answer_path.read_text(encoding="utf-8", errors="replace") if answer_path.is_file() else ""
    for (name, rx), fix in zip(CLAIMS.items(), FIXES.values(), strict=True):
        metrics[name] = calls_broken(answer, rx, fix)
    for name, rx in FIXES.items():
        metrics[name] = bool(re.search(rx, answer, re.I))
    agents_md = TOY / "AGENTS.md"
    metrics["toy_agents_md_unchanged"] = agents_md.is_file() and "docs/releasing.md" in agents_md.read_text(
        encoding="utf-8", errors="replace") and "src/tinycalc/parser.py" in agents_md.read_text(
        encoding="utf-8", errors="replace")
    commands = []
    events = run_dir / "events.jsonl"
    if events.is_file():
        for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "command":
                commands.append(str(e.get("text", "")))
    metrics["ran_repo_scan"] = any(re.search(r"\brepo\s+scan\b", c) for c in commands)
    print(json.dumps(metrics))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never crash the grader
        print(f"check.py error: {exc}")
        print(json.dumps({"manifest_ok": False, "answer_names_claim_1": False, "answer_names_claim_2": False}))

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
import tempfile
from pathlib import Path

EVAL_DIR = Path(os.environ.get("EVAL_CONTENT", "/eval/content"))
OUTPUT_DIRS = ("results/", "reports/", "answers/", "eval/", "baselines/", "questions/", "personas/")
REGRESSION = {
    # Timing- and process-sensitive tests that flake on a loaded host are left out: they measure
    # the machine, not the change.
    "regress_pass": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
                     "--deselect", "tests/test_example_runner_processes.py",
                     "--deselect", "tests/test_container_agent.py::test_probe_finds_a_proxy_that_forwards_plain_http",
                     "--deselect", "tests/test_benchflow_harness.py::test_run_bench_kills_the_process_group_on_timeout",
                     # Needs the [ragas] extra, which the image (built from the lock) leaves out;
                     # task bases older than its skip-when-missing would fail it.
                     "--deselect", "tests/test_ragas_runtime.py::test_ragas_imports_and_evaluator_builds_from_installed_deps"],
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


def git(ws, *args, split=False):
    return sh(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], ws, split=split)


def changed_files(ws, start):
    """Paths changed since *start*: committed, staged, unstaged and untracked.

    Read NUL-separated (``-z``): plain output C-quotes a path with spaces or non-ASCII
    characters, which would then match neither the artifact patterns nor the committed list.
    """
    _, committed, _ = git(ws, "diff", "--name-only", "-z", start, "HEAD", split=True)
    _, dirty, _ = git(ws, "status", "--porcelain", "-z", "--untracked-files=all", split=True)
    files = set(committed.split("\0"))
    entries = iter(dirty.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        files.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:   # a rename or copy: its source path is the next entry
            next(entries, None)
    return sorted(f for f in files if f)


def _events(rd):
    ev = rd / "events.jsonl"
    if not ev.exists():
        return []
    return [json.loads(line) for line in ev.read_text().splitlines() if line.strip()]


def _is_artifact(path):
    in_output_dir = path.startswith("dokimi/") and path[len("dokimi/"):].startswith(OUTPUT_DIRS)
    return in_output_dir or bool(re.search(r"\.(log|tar\.gz)$|coverage\.(xml|json)$|(^|/)\.coverage$", path))


def process_metrics(rd, ws, start):
    events = _events(rd)
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
        "artifacts_added": sum(1 for f in files if _is_artifact(f)),
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


def _grade_review(task, rd, metrics, details):
    answer = (rd / "answer.txt").read_text() if (rd / "answer.txt").exists() else ""
    found = {f["id"]: all(re.search(rx, answer, re.I | re.S) for rx in f["all"]) for f in task["findings"]}
    metrics["recall"] = sum(found.values()) / len(found) if found else 0.0
    metrics.update({f"found_{k}": v for k, v in found.items()})
    metrics["false_alarm_lines"] = None
    details["found"] = found


def _regression(ws, proj, start, metrics, logs):
    """The agent's own tree, before any hidden file is added."""
    cov_xml = Path(os.environ.get("HOME") or tempfile.gettempdir()) / "coverage.xml"   # outside the workspace
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


def _hidden_tests(task, tdir, ws, proj, metrics, logs, errors):
    """Hidden tests, as files of their own so the agent's tests are never overwritten."""
    hidden = task.get("hidden_tests", [])
    if not hidden:
        return
    taken = [rel for rel in hidden if (ws / rel).exists()]
    if taken:
        errors.append(f"the workspace already has a file at a hidden test's path: {', '.join(taken)}")
        return
    for rel in hidden:
        dst = ws / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tdir / "hidden" / rel, dst)
    rc, out = sh(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                  *[str(Path(r).relative_to("dokimi")) for r in hidden]], proj)
    metrics["hidden_pass"] = rc == 0
    logs["hidden_pass"] = out[-4000:]


def _task_checks(tdir, rd, proj, metrics, logs, errors):
    if not (tdir / "check.py").exists():
        return
    rc, out, err = sh([sys.executable, str(tdir / "check.py"), str(rd), str(tdir)], proj, split=True)
    try:
        metrics.update(json.loads(out.strip().splitlines()[-1]))
    except Exception as exc:  # a check crash is a grader failure
        errors.append(f"check.py rc={rc}: {exc}: {(out + err)[-1500:]}")
    logs["check"] = (out + err)[-3000:]


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
    logs: dict = {}

    if task["grader"] == "review":
        _grade_review(task, rd, metrics, details)
        metrics.update(process_metrics(rd, ws, start))
        verdict = None   # repo_grade.v1: `pass` is null for a score-only task
        return {"pass": verdict, "metrics": metrics, "details": details, "errors": errors}

    _regression(ws, proj, start, metrics, logs)
    process = process_metrics(rd, ws, start)   # the agent's changes, before the hidden tests are added
    _hidden_tests(task, tdir, ws, proj, metrics, logs, errors)
    _task_checks(tdir, rd, proj, metrics, logs, errors)
    metrics.update(process)
    details["logs"] = logs
    ok = _gate_ok(task, metrics, errors)
    return {"pass": None if errors else ok, "metrics": metrics, "details": details, "errors": errors}


def _gate_ok(task, metrics, errors):
    """The task's gate, read as the framework reads it: a name must be ``true``, a
    ``{"metric", "min"}`` entry a number >= min. ``pass`` is this verdict, so a gate
    without any other entry has nothing to decide on, which is a task error."""
    gate = [g for g in task.get("gate", []) if g != "pass"]
    if not gate:
        errors.append("task.json has no gate besides `pass`: name the metrics that decide it")
        return False

    def passes(g):
        if isinstance(g, str):
            return metrics.get(g) is True
        value = metrics.get(g["metric"])
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= g["min"]
    return all(passes(g) for g in gate)


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

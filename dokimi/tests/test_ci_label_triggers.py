# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""ci.yml's label-gated checks must run again when a maintainer changes a label.

The `Fragment for package changes` and `schema_compat` steps read
`skip-changelog` and `schema-break-ok` from github.event.pull_request.labels. That
list comes from the event that started the run, and a re-run reuses the same event,
so a label added after the push starts a run only if the pull_request trigger lists
the labeled and unlabeled types.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CI = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _trigger(wf: dict) -> dict:
    return wf.get("on") or wf[True]  # PyYAML reads the bare key `on` as True


def test_pull_request_trigger_fires_on_label_changes():
    pr = _trigger(_ci())["pull_request"] or {}
    types = set(pr.get("types") or [])
    assert {"opened", "synchronize", "reopened", "labeled", "unlabeled"} <= types


def _schema_compat_step() -> str:
    steps = _ci()["jobs"]["static"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "schema_compat vs the PR base")


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="runs the step's bash")
@pytest.mark.parametrize(("status", "printed", "label", "step_exit"), [
    (0, "", "false", 0), (0, "", "true", 0),
    (1, "BREAKING a.v1.json x", "false", 1), (1, "BREAKING a.v1.json x", "true", 0),
    (1, "", "false", 1), (1, "", "true", 1),
    (2, "", "false", 2), (2, "", "true", 2),
])
def test_the_label_overrides_a_printed_breaking_edit_and_nothing_else(
        tmp_path, status, printed, label, step_exit):
    """schema_compat exits 1 for a breaking edit (which prints a BREAKING line) and 2 for an
    unreadable base; a crash also exits 1 but prints none: only a finding may be accepted
    with ``schema-break-ok``."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "python").write_text(f"#!/bin/sh\necho '{printed}'\nexit {status}\n", encoding="utf-8")
    (stub / "python").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "BASE_REF": "main",
           "OVERRIDE": label, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    done = subprocess.run(["bash", "-c", _schema_compat_step()], cwd=tmp_path, env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == step_exit


_LABEL_GATE = re.compile(r"contains\(github\.event\.pull_request\.labels\.\*\.name, '([^']+)'\)")
_RUN_ID, _PR = 9001, 512


def _value(expr: str, event: dict) -> object:
    """Evaluate a ci.yml expression of the operators its group and job guards use; `&&` and `||` return an operand, as in Actions."""
    ctx = {"github.workflow": _ci()["name"], "github.run_id": _RUN_ID, **event}
    py = re.sub(r"\bgithub(?:\.[\w-]+)+", lambda m: f"_ctx({m.group(0)!r})", expr)
    py = re.sub(r"!(?!=)", " not ", py.replace("&&", " and ").replace("||", " or "))
    names = {"_ctx": ctx.get, "fromJSON": json.loads, "contains": lambda seq, item: item in seq, "true": True}
    return eval(py, {"__builtins__": {}}, names)  # nosec B307


def _group(event: dict) -> str:
    template = _ci()["concurrency"]["group"]
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: str(_value(m.group(1), event)), template)


def _ran(event: dict) -> set[str]:
    """The jobs that run: a job runs when every job it needs ran and its `if` holds (no guard calls always())."""
    jobs = _ci()["jobs"]

    def runs(name: str) -> bool:
        needs = jobs[name].get("needs") or []
        cond = str(jobs[name].get("if", "true")).strip().removeprefix("${{").removesuffix("}}")
        return all(runs(n) for n in ([needs] if isinstance(needs, str) else needs)) and bool(_value(cond, event))

    return {name for name in jobs if runs(name)}


def _pr(action: str, label: str | None = None) -> dict:
    event = {"github.event_name": "pull_request", "github.event.action": action, "github.event.pull_request.number": _PR}
    return event | ({"github.event.label.name": label} if label else {})


_PR_JOBS = set(_ci()["jobs"]) - {"test-latest"}


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
def test_a_label_no_step_reads_neither_cancels_the_run_nor_runs_the_jobs(action):
    """Renovate labels its PR right after opening it; that event must not cancel the push's run."""
    read = set(_LABEL_GATE.findall(CI.read_text(encoding="utf-8")))
    assert read == {"skip-changelog", "schema-break-ok"}
    ignored = _pr(action, "dependencies")
    assert (_group(ignored), _ran(ignored)) == ("ci-9001", set())
    for label in read:
        assert (_group(_pr(action, label)), _ran(_pr(action, label))) == ("ci-512", _PR_JOBS)


def test_a_push_to_a_pr_supersedes_its_run_and_a_main_push_keeps_its_own():
    assert (_group(_pr("synchronize")), _ran(_pr("synchronize"))) == ("ci-512", _PR_JOBS)
    push = {"github.event_name": "push"}
    assert (_group(push), _ran(push)) == ("ci-9001", _PR_JOBS)

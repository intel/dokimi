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

import re
from pathlib import Path

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


_LABEL_GATE = re.compile(r"contains\(github\.event\.pull_request\.labels\.\*\.name, '([^']+)'\)")


def test_a_label_no_step_reads_neither_cancels_the_run_nor_runs_the_jobs():
    """Renovate labels its PR right after opening it; that event must not cancel the push's run."""
    wf = _ci()
    text = CI.read_text(encoding="utf-8")
    read = set(_LABEL_GATE.findall(text))
    assert read == {"skip-changelog", "schema-break-ok"}
    group = wf["concurrency"]["group"]
    for label in read:
        assert f'"{label}"' in group
    jobs = wf["jobs"]

    def guarded(name: str) -> bool:
        job = jobs[name]
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        return "github.event.label.name" in str(job.get("if", "")) or (bool(needs) and all(guarded(n) for n in needs))

    unguarded = [n for n in jobs if n != "test-latest" and not guarded(n)]
    assert unguarded == []

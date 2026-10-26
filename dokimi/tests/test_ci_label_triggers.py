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

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The GitHub Actions workflows of a tree, and the Actions their steps use, read once per scan.

The path rule (:mod:`.core`) takes an ``owner/name`` that a step uses for that Action, not a path; the
Python-version check (:mod:`.versions`) finds the workflow a line names among them.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .core import Scan

WORKFLOWS = ".github/workflows/"
ACTION = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?$")  # `owner/name` or `owner/repo/path`, before `@<ref>`


def workflow_files(scan: Scan) -> list[str]:
    """Every file under ``.github/workflows/``, the directory GitHub runs workflows from."""
    return scan.cached("workflows", lambda: [f for f in scan.files if f.startswith(WORKFLOWS)])


def action_users(scan: Scan) -> dict[str, str]:
    """Each Action a job or step names as ``uses: <owner/name>@<ref>`` -> the first file that does.

    Read from the ``jobs`` of the ``.yml``/``.yaml`` workflows directly in ``.github/workflows/`` (GitHub
    runs no nested one) and the ``runs.steps`` of the local actions under ``.github/`` (``action.yml``,
    ``action.yaml``) whose ``runs.using`` is ``composite``: only the ``uses:`` values, not a name in a
    ``run:`` script.
    """
    return _read(scan)[0]


def unread_action_files(scan: Scan) -> dict[str, str]:
    """The files :func:`action_users` reads that the scan did not (too big, binary, a dangling link) -> why, in tree order."""
    return _read(scan)[1]


def _read(scan: Scan) -> tuple[dict[str, str], dict[str, str]]:
    return scan.cached("action_users", lambda: _action_users(scan))


def _action_users(scan: Scan) -> tuple[dict[str, str], dict[str, str]]:
    flows = [f for f in workflow_files(scan) if f.endswith((".yml", ".yaml")) and "/" not in f[len(WORKFLOWS):]]
    local = [f for f in scan.files if f.startswith(".github/") and posixpath.basename(f) in ("action.yml", "action.yaml")]
    paths = list(dict.fromkeys(flows + local))
    skipped: dict[str, str] = {}
    scan.preload(paths, skipped)
    out: dict[str, str] = {}
    for path in paths:
        try:
            doc = yaml.safe_load(scan.text(path))
        except (yaml.YAMLError, ValueError, RecursionError):  # also a date that does not exist, deep nesting
            continue
        for value in _uses(doc, path in flows):  # `.github/workflows/action.yml` is a workflow
            name, at, ref = value.strip().partition("@")
            if at and ref and ACTION.match(name):
                out.setdefault(name, path)
    return out, {f: skipped[f] for f in scan.files if f in skipped}


def _uses(doc: Any, workflow: bool) -> list[str]:
    """The ``uses:`` of each job of a workflow and of its steps, or of each step of a composite action.

    A workflow is read only at ``jobs``; a local action only at ``runs.steps``, when ``runs.using`` is ``composite``.
    """
    if not isinstance(doc, dict):
        return []
    jobs, runs = doc.get("jobs"), doc.get("runs")
    if workflow:
        holders = [j for j in jobs.values() if isinstance(j, dict)] if isinstance(jobs, dict) else []
    else:
        holders = [runs] if isinstance(runs, dict) and runs.get("using") == "composite" else []
    steps = [s for h in holders if isinstance(h.get("steps"), list) for s in h["steps"]]
    callers = holders if workflow else []  # a job calls a reusable workflow; `runs` itself calls nothing
    return [x["uses"] for x in [*callers, *steps] if isinstance(x, dict) and isinstance(x.get("uses"), str)]

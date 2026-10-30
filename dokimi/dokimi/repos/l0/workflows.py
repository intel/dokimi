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
_ACTION = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?$")  # `owner/name` or `owner/repo/path`, before `@<ref>`


def workflow_files(scan: Scan) -> list[str]:
    """Every file under ``.github/workflows/``, the directory GitHub runs workflows from."""
    return scan.cached("workflows", lambda: [f for f in scan.files if f.startswith(WORKFLOWS)])


def action_users(scan: Scan) -> dict[str, str]:
    """Each Action a job or step names as ``uses: <owner/name>@<ref>`` -> the first file that does.

    Read from the ``.yml``/``.yaml`` workflows directly in ``.github/workflows/`` (GitHub runs no nested
    one) and the local composite actions under ``.github/`` (``action.yml``, ``action.yaml``): only the
    ``uses:`` values, not a name in a ``run:`` script.
    """
    return scan.cached("action_users", lambda: _action_users(scan))


def _action_users(scan: Scan) -> dict[str, str]:
    flows = [f for f in workflow_files(scan) if f.endswith((".yml", ".yaml")) and "/" not in f[len(WORKFLOWS):]]
    local = [f for f in scan.files if f.startswith(".github/") and posixpath.basename(f) in ("action.yml", "action.yaml")]
    paths = list(dict.fromkeys(flows + local))
    scan.preload(paths)
    out: dict[str, str] = {}
    for path in paths:
        try:
            doc = yaml.safe_load(scan.text(path))
        except (yaml.YAMLError, ValueError, RecursionError):  # also a date that does not exist, deep nesting
            continue
        for value in _uses(doc):
            name, at, ref = value.strip().partition("@")
            if at and ref and _ACTION.match(name):
                out.setdefault(name, path)
    return out


def _uses(doc: Any) -> list[str]:
    """The ``uses:`` of each job and step of a workflow, and of each step of a composite action (``runs.steps``)."""
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs")
    holders = [j for j in jobs.values() if isinstance(j, dict)] if isinstance(jobs, dict) else []
    steps = [s for h in [*holders, doc.get("runs")] if isinstance(h, dict) and isinstance(h.get("steps"), list)
             for s in h["steps"]]
    return [x["uses"] for x in [*holders, *steps] if isinstance(x, dict) and isinstance(x.get("uses"), str)]

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Key-free static catalog inventory, deterministic fingerprints and diff impact."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from dokimi.skills.loader import VENDORED_DIR_NAMES

from .catalog import DEFAULT_SUITES_MANIFEST, DEFAULT_TASKS_SUBDIR
from .catalog_eval_inputs import Inputs, UnsafePath, checked_root, digest, finding, relative_path, task_dir
from .catalog_eval_inventory import inventory_skills, inventory_suites, inventory_tasks


def _impact(root: Path, rows: list[dict], changed: list[str] | None, untracked: list[str],
            base_owners: dict[str, str]) -> dict:
    names = sorted({row["name"] for row in rows})
    selection: dict = {"mode": "all" if changed is None else "changed", "changed_paths": [],
                       "selected": [], "reasons": {}, "unclaimed": [], "removed": []}
    if changed is None:
        selection["selected"] = names
        selection["reasons"] = {name: ["full catalog requested"] for name in names}
        return selection
    if not isinstance(changed, list):
        raise ValueError("changed_paths must be a list of local paths")
    try:
        literal = os.name != "nt"
        tracked = {relative_path(path, literal_backslash=literal) for path in changed}
        loose = {relative_path(path, literal_backslash=literal) for path in untracked}
    except UnsafePath as exc:
        raise ValueError(f"invalid changed path: {exc}") from exc
    # An untracked or ignored file changes a digest only if a bundle hashes it.
    loose = set(hashed_paths(rows, sorted(loose)))
    paths = sorted(tracked | loose)
    selection["changed_paths"] = paths
    selection["removed"] = [path for path in paths if not (root / path).exists()]
    reasons: dict[str, set[str]] = {}
    for path in paths:
        owners, reason = _owners(path, rows)
        task = task_dir(path)
        if task in base_owners and base_owners[task] in names and base_owners[task] not in owners:
            owners, reason = [*owners, base_owners[task]], f"task moved or removed: {path}"
        if not owners and _irrelevant(path):
            continue
        if not owners:
            owners, reason = names, f"conservative selection for unclaimed path: {path}"
            selection["unclaimed"].append(path)
        for owner in owners:
            reasons.setdefault(owner, set()).add(reason)
    selection["selected"] = sorted(reasons)
    selection["reasons"] = {name: sorted(reasons[name]) for name in sorted(reasons)}
    return selection


def hashed_paths(rows: list[dict], paths: list[str]) -> list[str]:
    """The untracked or ignored *paths* that some bundle hashes or a shared rule covers."""
    return [path for path in paths if _owners(path, rows, hashed=True)[0]]


def _bundled(path: str, prefix: str) -> bool:
    """Whether a file below *prefix* is hashed: no vendored directory on the way and not a ``.git``."""
    rest = PurePosixPath(path if prefix == "." else path[len(prefix) + 1:]).parts
    # The prefix itself: a file a bundle reads, or a nested repository git lists as one entry.
    return not rest or (rest[-1] != ".git" and not VENDORED_DIR_NAMES.intersection(rest[:-1]))


def _owners(path: str, rows: list[dict], hashed: bool = False) -> tuple[list[str], str]:
    if path in {"skills.yaml", DEFAULT_SUITES_MANIFEST}:
        return [row["name"] for row in rows], f"catalog metadata changed: {path}"
    if path.startswith((".github/", "tools/", "scripts/", "dokimi/")) and not (
            hashed and VENDORED_DIR_NAMES.intersection(PurePosixPath(path).parts)):
        return [row["name"] for row in rows], f"shared evaluator changed: {path}"
    owners = []
    for row in rows:
        prefixes = [row["path"]] + [task["path"] for task in row["tasks"]]
        prefixes += [f"{DEFAULT_TASKS_SUBDIR}/{task}" for task in row["planned_tasks"]]
        # A task this skill's findings name (one suites.json still assigns to it) is its input too.
        prefixes += [item["path"] for item in row["checks"]["findings"] if item["path"].startswith(DEFAULT_TASKS_SUBDIR + "/")]
        if any(prefix and (prefix == "." or path == prefix or path.startswith(prefix + "/"))
               and (not hashed or _bundled(path, prefix)) for prefix in prefixes):
            owners.append(row["name"])
    return owners, f"owned input changed: {path}"


def _irrelevant(path: str) -> bool:
    return path.startswith("docs/") or path in {"README.md", "LICENSE", "NOTICE", "CODE_OF_CONDUCT.md"}


def _finish_rows(rows: list[dict], problems: list[dict]) -> None:
    for row in rows:
        row["tasks"].sort(key=lambda task: task["name"])
        row["planned_tasks"] = sorted(set(row["planned_tasks"]))
        findings = row["checks"]["findings"]
        for item in findings:
            if item not in problems:
                problems.append(item)
        row["checks"]["status"] = "fail" if any(item["level"] == "error" for item in findings) else "pass"
        if not row["evaluated_here"]:
            row["evaluation"] = "checks_only"
        else:
            row["evaluation"] = "not_measured" if row["tasks"] else "no_tasks"
        row["evaluation_reason"] = {
            "checks_only": "imported or upstream-owned skill; no local runtime evaluation credit",
            "no_tasks": "no loadable executable tasks owned by metadata.skill",
            "not_measured": "static checks only; runtime task outcomes were not run",
        }[row["evaluation"]]
        findings.sort(key=lambda item: (item["path"], item["code"], item["message"]))


def shared_problems(problems: list[dict], rows: list[dict]) -> list[dict]:
    """Findings that belong to no skill: manifests, suites, the tasks root and unowned tasks."""
    owned = [item for row in rows for item in row["checks"]["findings"]]
    return [item for item in problems if item not in owned]


def evaluate_catalog(root: Path, changed_paths: list[str] | None = None, untracked_paths: list[str] | None = None,
                     base_owners: dict[str, str] | None = None) -> dict:
    """Check catalog data without executing payloads or claiming runtime benefit.

    ``None`` selects every skill; a path list selects affected skills while catalog
    integrity is always checked. Only the selected skills and the catalog-wide
    problems decide the status. ``untracked_paths`` select only when a bundle
    hashes them; ``base_owners`` maps a changed task directory to its owner at the base. Malformed/unreadable inputs raise ``ValueError``
    naming their path. Content validation failures remain structured report data.
    """
    resolved = checked_root(root)
    inputs = Inputs(resolved)
    problems: list[dict] = []
    try:
        rows = inventory_skills(inputs, problems)
    except UnsafePath as exc:
        rows = []
        problems.append(finding("unsafe-path", str(exc)))
    declared = inventory_suites(inputs, rows, problems)
    inventory_tasks(inputs, rows, declared, problems)
    if not rows:
        problems.append(finding("empty-catalog", "catalog contains no skills"))
    _finish_rows(rows, problems)
    inputs.verify()
    rows.sort(key=lambda row: (row["name"], row["path"]))
    problems = sorted((dict(item) for item in problems), key=lambda item: (item["path"], item["code"], item["message"]))
    selection = _impact(resolved, rows, changed_paths, untracked_paths or [], base_owners or {})
    selected = set(selection["selected"])
    failed = any(item["level"] == "error" for item in shared_problems(problems, rows)) or any(
        row["checks"]["status"] == "fail" for row in rows if row["name"] in selected)
    return {"schema_version": "catalog_eval.v1", "tier": "static", "root": str(resolved),
            "catalog_digest": digest(inputs.hashes), "selection": selection,
            "skills": rows, "problems": problems, "status": "fail" if failed else "pass"}

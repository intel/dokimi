# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Validated inventory joins, using task metadata as the ownership authority."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from dokimi.harnesses.tasks import load_task, not_a_task, read_task_config
from dokimi.skills.loader import TEMPLATE_DIR_NAMES, walk_skill_files

from .catalog import DEFAULT_SUITES_MANIFEST, DEFAULT_TASKS_SUBDIR, SkillCatalogEntry, read_skill_manifest
from .catalog_eval_inputs import Inputs, UnsafePath, checked_path, finding, relative_path
from .validate import review_skills


def _yaml(inputs: Inputs, path: Path) -> Any:
    try:
        return yaml.safe_load(inputs.text(path))
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError(f"invalid YAML at {path}: {exc}") from exc


def _walk_failed(exc: OSError) -> None:
    raise ValueError(f"cannot inspect {exc.filename}: {exc}") from exc


def _skill_specs(inputs: Inputs) -> list[dict]:
    path = inputs.root / "skills.yaml"
    checked_path(inputs.root, path)
    if not path.exists():
        return [{"name": file.parent.name, "catalog_path": file.parent.relative_to(inputs.root).as_posix()}
                for file in walk_skill_files(inputs.root, onerror=_walk_failed)
                if not TEMPLATE_DIR_NAMES.intersection(file.relative_to(inputs.root).parts[:-1])]
    data = _yaml(inputs, path) or {}
    if isinstance(data, dict):
        data = data.get("skills") or []
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"invalid skills list at {path}")
    return data


def _skill_path(item: dict) -> str:
    name = item.get("name")
    if item.get("catalog_path"):
        return relative_path(item["catalog_path"])
    if not isinstance(name, str) or not name.strip():
        raise UnsafePath(f"skill name must be a nonempty string: {name!r}")
    name = name.strip()
    if relative_path(name) != name or "/" in name or name == ".":
        raise UnsafePath(f"skill name is not a directory name: {name!r}")
    return f"skills/{name}"


def _imported(item: dict, problems: list[dict]) -> bool:
    keys = ("external-repo", "external-commit", "external-path", "external-license")
    imported = any(key in item for key in keys)
    if imported:
        for key in keys:
            if not isinstance(item.get(key), str) or not item[key]:
                problems.append(finding("import-type", f"{key} must be a nonempty string", "skills.yaml"))
        try:
            relative_path(item.get("external-path"))
        except UnsafePath as exc:
            problems.append(finding("unsafe-path", str(exc), "skills.yaml"))
    return imported


def _row(item: dict, path: str, bundle: str | None, findings: list[dict]) -> dict:
    return {"name": str(item.get("name") or "").strip() or PurePosixPath(path).name or "?", "path": path, "bundle_digest": bundle,
            "checks": {"status": "pass", "findings": findings}, "evaluation": "no_tasks",
            "evaluated_here": True, "tasks": [], "planned_tasks": []}


def inventory_skills(inputs: Inputs, problems: list[dict]) -> list[dict]:
    specs = _skill_specs(inputs)
    rows: list[dict] = []
    safe = True
    for item in specs:
        findings: list[dict] = []
        path, bundle = "", None
        try:
            path = _skill_path(item)
            bundle_path = inputs.root / path
            if bundle_path.name == "SKILL.md":
                bundle_path = bundle_path.parent
                path = bundle_path.relative_to(inputs.root).as_posix()
            bundle = inputs.bundle(bundle_path)
        except UnsafePath as exc:
            safe = False
            findings.append(finding("unsafe-path", str(exc), path))
        result = _row(item, path, bundle, findings)
        result["evaluated_here"] = not _imported(item, findings)
        rows.append(result)
    if safe:
        entries = read_skill_manifest(inputs.root)
        _review(entries, specs, rows, fallback=not (inputs.root / "skills.yaml").exists())
    else:
        for item in rows:
            item["checks"]["findings"].append(finding(
                "load-refused", "catalog contains an unsafe skill path; skill loading refused", item["path"]))
    seen: set[str] = set()
    for item in rows:
        if item["name"] in seen:
            item["checks"]["findings"].append(finding("duplicate-skill", "duplicate skill identity", item["path"]))
        seen.add(item["name"])
        problems.extend(item["checks"]["findings"])
    return rows


def _review(entries: list[SkillCatalogEntry], specs: list[dict], rows: list[dict], *, fallback: bool) -> None:
    for entry, review, spec, row in zip(entries, review_skills(entries), specs, rows, strict=True):
        findings = row["checks"]["findings"]
        if entry.error:
            findings.append(finding("skill-load", entry.error, row["path"]))
        if review:
            findings.extend(finding(f.code, f.message, row["path"], f.level) for f in review.findings)
        declared = str(spec.get("name") or "").strip()
        if fallback or not declared:
            row["name"] = entry.skill.name if entry.skill else entry.name
        if entry.skill and declared and entry.skill.name != entry.name:
            findings.append(finding("skill-identity", "manifest and frontmatter skill names differ", row["path"]))


def inventory_suites(inputs: Inputs, rows: list[dict], problems: list[dict]) -> dict[str, dict]:
    path = inputs.root / DEFAULT_SUITES_MANIFEST
    try:
        checked_path(inputs.root, path)
    except UnsafePath as exc:
        problems.append(finding("unsafe-path", str(exc), DEFAULT_SUITES_MANIFEST))
        return {}
    if not path.exists():
        return {}
    try:
        data = json.loads(inputs.text(path))
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"invalid suites at {path}: {exc}") from exc
    suites = data.get("suites", []) if isinstance(data, dict) else None
    if not isinstance(suites, list) or not all(isinstance(item, dict) for item in suites):
        raise ValueError(f"invalid suites list at {path}")
    declared: dict[str, dict] = {}
    owners = {row["name"]: row for row in rows}
    seen: set[str] = set()
    for suite in suites:
        _suite(suite, owners, seen, declared, problems)
    return declared


def _suite(suite: dict, owners: dict, seen: set[str], declared: dict, problems: list[dict]) -> None:
    owner = suite.get("skill", suite.get("name"))
    if not isinstance(owner, str) or owner not in owners:
        problems.append(finding("suite-owner", f"unknown or malformed suite owner: {owner!r}", DEFAULT_SUITES_MANIFEST))
        return
    row = owners[owner]
    findings = row["checks"]["findings"]
    if owner in seen:
        findings.append(finding("duplicate-suite", f"duplicate suite for {owner}", DEFAULT_SUITES_MANIFEST))
    seen.add(owner)
    flag = suite.get("evaluated_here", row["evaluated_here"])
    if type(flag) is not bool:
        findings.append(finding("evaluated-here-type", "evaluated_here must be a boolean", DEFAULT_SUITES_MANIFEST))
    elif flag is False:
        row["evaluated_here"] = False
    tasks = suite.get("tasks", [])
    if not isinstance(tasks, list) or not all(isinstance(task, dict) for task in tasks):
        raise ValueError(f"invalid tasks list at {DEFAULT_SUITES_MANIFEST}")
    for task in tasks:
        _declaration(task, owner, row, declared)


def _declaration(task: dict, owner: str, row: dict, declared: dict) -> None:
    name = task.get("name")
    findings = row["checks"]["findings"]
    try:
        if relative_path(name) != name or "/" in name or name == ".":
            raise UnsafePath(f"task name must be a directory name: {name!r}")
    except UnsafePath as exc:
        findings.append(finding("unsafe-path", str(exc), DEFAULT_SUITES_MANIFEST))
        return
    if name in declared:
        findings.append(finding("duplicate-task", f"duplicate task declaration: {name}", DEFAULT_SUITES_MANIFEST))
    else:
        declared[name] = {**task, "owner": owner}
    if task.get("status") == "planned":
        row["planned_tasks"].append(name)


def _task_paths(config: Any) -> None:
    if not isinstance(config, dict):
        return
    local_keys = {"instruction_file", "dockerfile", "dockerfile_path", "build_context", "script_path"}
    for key, value in config.items():
        if key in local_keys:
            relative_path(value)
        elif isinstance(value, dict):
            _task_paths(value)


def inventory_tasks(inputs: Inputs, rows: list[dict], declared: dict, problems: list[dict]) -> None:
    tasks_root = inputs.root / DEFAULT_TASKS_SUBDIR
    try:
        checked_path(inputs.root, tasks_root)
        inputs.files(tasks_root)
    except UnsafePath as exc:
        problems.append(finding("unsafe-path", str(exc), DEFAULT_TASKS_SUBDIR))
        return
    try:
        children = sorted(tasks_root.iterdir()) if tasks_root.is_dir() else []
    except OSError as exc:
        raise ValueError(f"cannot inspect {tasks_root}: {exc}") from exc
    candidates = [child for child in children
                  if any((child / name).is_file() for name in ("task.toml", "task.md", "task.yaml"))]
    owners = {row["name"]: row for row in rows}
    actual: set[str] = set()
    for path in candidates:
        _task(inputs, path, owners, declared, actual, problems)
    for name, declaration in declared.items():
        if name not in actual and _implemented(declaration):
            owners[declaration["owner"]]["checks"]["findings"].append(finding(
                "missing-task", f"declared task {name} has no loadable definition", f"{DEFAULT_TASKS_SUBDIR}/{name}"))


def _task(inputs: Inputs, path: Path, owners: dict, declared: dict, actual: set[str], problems: list[dict]) -> None:
    relative = path.relative_to(inputs.root).as_posix()
    try:
        bundle = inputs.bundle(path)
        if not_a_task(path):
            return
        config = read_task_config(path)
        _task_paths(config)
        spec = load_task(path)
    except UnsafePath as exc:
        problems.append(finding("unsafe-path", str(exc), relative))
        return
    except FileNotFoundError as exc:
        problems.append(finding("task-load", str(exc), relative))
        return
    except (ValueError, OSError, UnicodeError, RecursionError, TypeError, OverflowError) as exc:
        raise ValueError(f"cannot load task at {path}: {exc}") from exc
    actual.add(spec.name)
    owner = spec.metadata.get("skill")
    if not isinstance(owner, str) or owner not in owners:
        problems.append(finding("task-owner", f"metadata.skill is not a known skill: {owner!r}", relative))
        return
    row = owners[owner]
    declaration = declared.get(spec.name)
    if declaration and declaration["owner"] != owner:
        conflict = finding("task-owner-conflict", f"suite assigns {spec.name} to {declaration['owner']}; metadata owns {owner}", relative)
        row["checks"]["findings"].append(conflict)
        owners[declaration["owner"]]["checks"]["findings"].append(conflict)
    if not declaration:
        row["checks"]["findings"].append(finding("undeclared-task", "actual task is absent from suites.json", relative, "warn"))
    row["tasks"].append({"name": spec.name, "path": relative, "digest": bundle, "declared": declaration is not None})


def _implemented(declaration: dict) -> bool:
    return declaration.get("status") != "planned" and declaration.get("track", "executable") == "executable"

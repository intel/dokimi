# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read external skill/task catalogs (e.g. uxlfoundation/skills) for reuse.

An "external catalog" is any local checkout of a repo that ships skills
(``SKILL.md`` packages) and/or executable tasks (Harbor task directories)
this repo did not author. Callers are expected to have already cloned the
repo themselves -- nothing here fetches anything over the network.

Two manifest conventions are understood:

- ``<root>/skills.yaml`` -- skill entries, either under a ``skills:`` key with
  a ``catalog_path`` relative to *root* (uxlfoundation/skills), or as a
  top-level list whose skill lives at ``skills/<name>`` (intel/skills).
- ``<root>/evaluation/harbor/suites.json`` -- suites of task entries, some
  of which are declared but not yet implemented (``status: "planned"``) or
  are not executable Harbor tasks at all (``track: "answer-quality"``).

When a root has no such manifest, both readers fall back to a bare
filesystem scan via the existing skill/task loaders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dokimi.harnesses.tasks import TaskSpec, discover_tasks, is_task_dir, load_task
from dokimi.skills import Skill, load_skill

DEFAULT_TASKS_SUBDIR = "evaluation/harbor/tasks"
DEFAULT_SUITES_MANIFEST = "evaluation/harbor/suites.json"


def _within(path: Path, root: Path) -> bool:
    """Whether *path* resolves to somewhere inside *root*.

    Manifest-declared paths (``catalog_path``, task ``name``) come from a
    file the external repo controls, not the caller -- a value like
    ``"../../etc"`` must not let a scan read or report on files outside the
    checkout the caller pointed us at.
    """
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass
class SkillCatalogEntry:
    """One skill as reported by a catalog scan."""

    name: str
    status: Optional[str]
    path: Optional[Path]
    source_root: Path
    skill: Optional[Skill] = None
    error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self.skill is not None and self.error is None


@dataclass
class TaskCatalogEntry:
    """One task as reported by a catalog scan.

    Three outcomes, not two. ``skipped`` is a declared entry a scan is
    *expected* to pass over -- ``status: "planned"`` with no task directory on
    disk yet, or a ``track`` this repo's ``tasks run`` doesn't drive.
    ``failed`` is an entry that should have worked and didn't. Only ``failed``
    makes ``catalog scan`` exit non-zero, which is the reason a pre-flight scan
    is worth running at all: collapsing both into ``skipped`` reports a catalog
    that cannot run as clean, using the same word the benign cases print.
    """

    name: str
    status: Optional[str]
    track: Optional[str]
    path: Optional[Path]
    task: Optional[TaskSpec] = None
    skipped: bool = False
    failed: bool = False
    reason: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self.task is not None and not self.skipped and not self.failed


def read_skill_manifest(root: Path) -> list[SkillCatalogEntry]:
    """Read skills under *root*, preferring a ``skills.yaml`` manifest."""

    root = Path(root)
    manifest_path = root / "skills.yaml"
    if not manifest_path.is_file():
        # One entry per SKILL.md, as the manifest path gives: a skill that does not
        # load is an error row, so one broken skill does not abort the whole scan.
        found: list[SkillCatalogEntry] = []
        for skill_file in sorted(root.rglob("SKILL.md")):
            try:
                skill = load_skill(skill_file)
            except (FileNotFoundError, ValueError) as exc:
                found.append(
                    SkillCatalogEntry(
                        name=skill_file.parent.name,
                        status=None,
                        path=skill_file.parent,
                        source_root=root,
                        error=str(exc),
                    )
                )
                continue
            found.append(
                SkillCatalogEntry(
                    name=skill.name,
                    status=None,
                    path=skill.path.parent,
                    source_root=root,
                    skill=skill,
                )
            )
        return found

    import yaml

    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid skills.yaml at {manifest_path}: {exc}") from exc

    # uxlfoundation/skills nests the entries under ``skills:``, each with a
    # required catalog_path; intel/skills makes the whole file the list and
    # keeps every skill at skills/<name>.
    if isinstance(data, dict):
        items = data.get("skills")
        if items is None:
            items = []
        list_shape = False
    elif isinstance(data, list):
        items = data
        list_shape = True
    else:
        raise ValueError(
            f"invalid skills.yaml at {manifest_path}: expected a list or a "
            f"mapping with a 'skills' list, got {type(data).__name__}"
        )
    if not isinstance(items, list):
        raise ValueError(
            f"invalid skills.yaml at {manifest_path}: 'skills' must be a list, "
            f"got {type(items).__name__}"
        )

    entries: list[SkillCatalogEntry] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            entries.append(
                SkillCatalogEntry(
                    name=f"<entry {index}>",
                    status=None,
                    path=None,
                    source_root=root,
                    error=f"manifest entry is not a mapping: {item!r}",
                )
            )
            continue
        name = str(item.get("name") or "").strip()
        entries.append(_skill_entry(root, item, name, list_shape))
    return entries


def _skill_entry(root: Path, item: dict, name: str, list_shape: bool) -> SkillCatalogEntry:
    """Resolve and load one mapping entry of ``skills.yaml``."""

    def failed(error: str, path: Optional[Path] = None) -> SkillCatalogEntry:
        return SkillCatalogEntry(name=name or "?", status=item.get("status"), path=path,
                                 source_root=root, error=error)

    catalog_path = item.get("catalog_path")
    if catalog_path is not None and not isinstance(catalog_path, str):
        return failed(f"catalog_path must be a string, got {catalog_path!r}")
    if catalog_path:
        source = "catalog_path"
    elif list_shape and name:
        # The name becomes a path, so it must be one plain directory name.
        if "/" in name or "\\" in name or name in (".", ".."):
            return failed(f"name is not a plain directory name: {name!r}")
        catalog_path, source = f"skills/{name}", "name"
    elif list_shape:
        return failed("manifest entry has no name")
    else:
        return failed("manifest entry is missing catalog_path")

    path = root / catalog_path
    if not _within(path, root):
        return failed(f"{source} escapes root: {catalog_path!r}")
    try:
        skill = load_skill(path)
    except (FileNotFoundError, ValueError) as exc:
        return failed(str(exc), path)
    # The directory is inside the root; a SKILL.md symlinked out of it is not.
    if not _within(skill.path, root):
        return failed(f"SKILL.md resolves outside root: {skill.path}", path)
    return SkillCatalogEntry(name=name or path.name, status=item.get("status"), path=path,
                             source_root=root, skill=skill)


def read_task_manifest(
    root: Path,
    tasks_subdir: str = DEFAULT_TASKS_SUBDIR,
) -> list[TaskCatalogEntry]:
    """Read tasks under *root*, preferring an ``evaluation/harbor/suites.json``
    suite manifest and cross-checking it against what's actually on disk.
    """

    root = Path(root)
    manifest_path = root / DEFAULT_SUITES_MANIFEST
    tasks_root = root / tasks_subdir
    if not manifest_path.is_file():
        return [
            TaskCatalogEntry(
                name=task.name,
                status=None,
                track=None,
                path=task.path,
                task=task,
            )
            for task in discover_tasks(tasks_root)
        ]

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid suites.json at {manifest_path}: {exc}") from exc

    entries: list[TaskCatalogEntry] = []
    for suite in data.get("suites") or []:
        for item in suite.get("tasks") or []:
            name = str(item.get("name") or "").strip()
            status = item.get("status")
            track = item.get("track")
            task_dir = tasks_root / name

            if not _within(task_dir, tasks_root):
                # A failure, not a skip: the manifest asked us to leave the
                # checkout, which is the same thing ``read_skill_manifest``
                # reports as an error for ``catalog_path``.
                entries.append(
                    TaskCatalogEntry(
                        name=name,
                        status=status,
                        track=track,
                        path=None,
                        failed=True,
                        reason=f"task name escapes tasks root: {name!r}",
                    )
                )
                continue

            if not is_task_dir(task_dir):
                entries.append(
                    TaskCatalogEntry(
                        name=name,
                        status=status,
                        track=track,
                        path=None,
                        skipped=True,
                        reason=f"no task.toml or task.md under {task_dir} (manifest status={status!r})",
                    )
                )
                continue

            if track != "executable":
                entries.append(
                    TaskCatalogEntry(
                        name=name,
                        status=status,
                        track=track,
                        path=task_dir,
                        skipped=True,
                        reason=f"track={track!r} is not an executable Harbor task",
                    )
                )
                continue

            try:
                task = load_task(task_dir)
            except (FileNotFoundError, ValueError) as exc:
                # The entry is declared executable and its directory is on
                # disk, so this is the case the scan exists to catch.
                entries.append(
                    TaskCatalogEntry(
                        name=name,
                        status=status,
                        track=track,
                        path=task_dir,
                        failed=True,
                        reason=f"failed to load: {exc}",
                    )
                )
                continue

            entries.append(
                TaskCatalogEntry(
                    name=name,
                    status=status,
                    track=track,
                    path=task_dir,
                    task=task,
                )
            )
    return entries

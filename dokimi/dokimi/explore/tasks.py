# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""ExploreBench task discovery.

An exploration task poses a localization question over a repository/doc tree and
declares the reference locations a good explorer should cite. Tasks are small
YAML files::

    id: onetbb_flow_graph_locate_buffering
    product: oneTBB
    query: "Find the docs and example code for a bounded flow-graph pipeline."
    repo_root: .            # optional; else --repo-root, else this repo
    references:
      - path: docs/flow_graph.md
        lines: [120, 180]
      - path: examples/pipeline.cpp     # whole-file reference (no lines)

Reference ``lines`` are inclusive ``[start, end]`` (or a single ``[n]``); a
missing/empty ``lines`` is a whole-file reference that scores at file
granularity only. This module is pure metadata loading — it neither runs an
explorer nor reads the referenced files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dokimi.metrics.exploration import ReferenceLocations, normalize_path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPLORE_TASKS_ROOT = REPO_ROOT / "data" / "explore_tasks"


def _tasks_root(root: Path | None) -> Path:
    """``root``, else the bundled tasks — which exist only in a source checkout."""
    if root is not None:
        return Path(root)
    if not EXPLORE_TASKS_ROOT.is_dir():
        raise FileNotFoundError(
            f"No bundled explore tasks at {EXPLORE_TASKS_ROOT}: they ship with a source "
            "checkout of dokimi, not with the installed package. Pass --tasks-root <dir>, "
            "and --repo-root <tree> for tasks whose YAML omits repo_root:."
        )
    return EXPLORE_TASKS_ROOT


@dataclass(frozen=True)
class ExploreReference:
    """One ground-truth location: a path and an optional inclusive line range."""

    path: str
    lines: tuple[int, int] | None = None

    def as_dict(self) -> dict:
        return {"path": self.path, "lines": list(self.lines) if self.lines else None}


@dataclass(frozen=True)
class ExploreTask:
    """A resolved exploration task."""

    id: str
    product: str
    query: str
    references: tuple[ExploreReference, ...]
    path: Path
    repo_root: Path = REPO_ROOT
    metadata: dict = field(default_factory=dict)

    def reference_locations(self) -> ReferenceLocations:
        """Build the scorer's :class:`ReferenceLocations` from the references."""
        files: dict[str, tuple[tuple[int, int], ...]] = {}
        for ref in self.references:
            key = normalize_path(ref.path)
            ranges = files.get(key, ())
            if ref.lines is not None:
                ranges = ranges + (ref.lines,)
            files[key] = ranges
        return ReferenceLocations(files=files)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "product": self.product,
            "query": self.query,
            "references": [r.as_dict() for r in self.references],
        }


def _parse_lines(value) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not (1 <= len(value) <= 2):
        raise ValueError(f"'lines' must be [start, end] or [n], got {value!r}")
    try:
        nums = [int(v) for v in value]
    except (TypeError, ValueError, OverflowError) as exc:  # OverflowError: int(float('inf'))
        raise ValueError(f"'lines' must contain integers, got {value!r}") from exc
    if any(n < 1 for n in nums):
        raise ValueError(f"'lines' values must be >= 1 (1-based), got {value!r}")
    start = nums[0]
    end = nums[1] if len(nums) > 1 else nums[0]
    return (min(start, end), max(start, end))


def _read_task_yaml(path: Path) -> dict:
    """The mapping in the task file at ``path``; any unreadable or malformed file is a ``ValueError`` naming it."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"Explore task {path} cannot be read: {exc}") from exc
    except (ValueError, yaml.YAMLError, RecursionError) as exc:  # ValueError: bad UTF-8, an invalid YAML timestamp
        raise ValueError(f"Explore task {path} is not valid UTF-8 YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Explore task {path} must hold a YAML mapping")
    return data


def _task_from_data(
    data: dict, path: Path, default_repo_root: Path | None = None
) -> ExploreTask:
    missing = [k for k in ("id", "query", "references") if k not in data]
    if missing:
        raise ValueError(f"Explore task {path} missing required field(s): {missing}")
    raw_refs = data["references"]
    if not isinstance(raw_refs, list) or not all(
        isinstance(ref, dict) and "path" in ref for ref in raw_refs
    ):
        raise ValueError(f"Explore task {path} references must be a list of entries with a 'path'")
    references = tuple(
        ExploreReference(path=str(ref["path"]), lines=_parse_lines(ref.get("lines")))
        for ref in raw_refs
    )
    if not references:
        raise ValueError(f"Explore task {path} has no references")
    repo_root = data.get("repo_root")
    if repo_root is not None and not isinstance(repo_root, str):
        raise ValueError(f"Explore task {path} repo_root must be a path string")
    if repo_root:
        resolved_root = (path.parent / repo_root).resolve()
    elif default_repo_root is not None:
        resolved_root = Path(default_repo_root).resolve()
    else:
        resolved_root = REPO_ROOT
    known = {"id", "product", "query", "references", "repo_root"}
    return ExploreTask(
        id=str(data["id"]),
        product=str(data.get("product", "unknown")),
        query=str(data["query"]),
        references=references,
        path=path,
        repo_root=resolved_root,
        metadata={k: v for k, v in data.items() if k not in known},
    )


def load_explore_task(
    ref: str, root: Path | None = None, default_repo_root: Path | None = None
) -> ExploreTask:
    """Load one task by id (or path) from the explore-tasks root.

    A direct file path is loaded as-is. Otherwise ``ref`` is matched against
    each file's parsed ``id`` first (the documented selector that ``explore
    list`` advertises) and only then against the filename stem, so a file whose
    ``id`` differs from its name cannot shadow the task that owns that id.

    Args:
        ref: task id, filename stem, or path to a task YAML file.
        root: directory of task YAML files (defaults to ``data/explore_tasks``).
        default_repo_root: tree a task scores against when its YAML omits
            ``repo_root:`` (defaults to this repository, which is only right
            for the self-dogfooding tasks bundled here).
    """
    candidate = Path(ref)
    if candidate.is_file():
        return _task_from_data(_read_task_yaml(candidate), candidate, default_repo_root)

    root = _tasks_root(root)
    files = _task_files(root)
    loaded = [(path, _read_task_yaml(path)) for path in files]
    id_matches = [(path, data) for path, data in loaded if str(data.get("id")) == ref]
    if len(id_matches) > 1:
        paths = ", ".join(str(p) for p, _ in id_matches)
        raise ValueError(f"Ambiguous explore task id {ref!r} matches multiple files: {paths}")
    if id_matches:
        path, data = id_matches[0]
        return _task_from_data(data, path, default_repo_root)
    # Fall back to filename stem only when no task declares this id.
    for path, data in loaded:
        if path.stem == ref:
            return _task_from_data(data, path, default_repo_root)
    raise FileNotFoundError(f"Explore task not found: {ref!r} under {root}")


def discover_explore_tasks(
    root: Path | None = None, default_repo_root: Path | None = None
) -> list[ExploreTask]:
    """Load every exploration task under the explore-tasks root, sorted by id.

    ``default_repo_root`` is the tree a task scores against when its YAML omits
    ``repo_root:``; it defaults to this repository.
    """
    root = _tasks_root(root)
    tasks = []
    for path in _task_files(root):
        tasks.append(_task_from_data(_read_task_yaml(path), path, default_repo_root))
    by_id: dict[str, list[Path]] = {}
    for t in tasks:
        by_id.setdefault(t.id, []).append(t.path)
    dupes = {k: v for k, v in by_id.items() if len(v) > 1}
    if dupes:
        detail = "; ".join(f"{k}: {', '.join(map(str, v))}" for k, v in dupes.items())
        raise ValueError(f"Duplicate explore task ids: {detail}")
    return sorted(tasks, key=lambda t: t.id)


def _task_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.yaml") if p.is_file())

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Load and validate a repository's ``repo-eval.yaml`` (``repo_manifest.v1``).

The manifest and the tasks it points at belong to the scanned repository
(docs/repo-evaluation-contract.md). This module checks them in two passes:

- :func:`load_manifest` — the manifest on its own: JSON Schema shape, then the
  rules a schema cannot express (root-relative paths, allowed placeholders).
  Any failure raises :class:`ManifestError`.
- :func:`check_repo` — the manifest against one snapshot of the repository:
  declared paths exist, guidance globs match something, and every task
  directory carries a ``task.json`` whose core keys follow the contract (§3).
  Problems are collected into a :class:`RepoCheckReport`, not raised.

Keys in ``task.json`` beyond the core and workspace keys belong to the
repository's grader and are not read.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable

import yaml

from dokimi.artifacts import schema_errors

from .graders import DATA_FILES, NAMES as BUILTIN_NAMES, PREFIX as BUILTIN_PREFIX
from .source import RepoTree

SCHEMA_VERSION = "repo_manifest.v1"
_RUN_KEYS = ("eval_dir", "tasks", "entry_points")
DEFAULT_MANIFEST_PATH = ".agent-eval/repo-eval.yaml"
PLACEHOLDERS = frozenset({"eval_dir", "source_dir", "run_dir", "out", "task"})
# {source_dir} is a full-history clone, so it holds every fix: only the grading
# container gets it (contract §2).
_SOURCE_DIR_ENTRY_POINTS = frozenset({"entry_points/grade", "entry_points/oracle"})
# An egress host (contract §2), the same pattern as harness_profile.v1 `egress`: DNS labels
# (1-63 characters), optional :port (1-65535). `*` stands for exactly one whole label, and the last two labels are literal,
# so `*.example.com` and `bedrock-runtime.*.amazonaws.com` pass while `*`, `**` and `*.com` do not.
HOST_PATTERN = r"^(?:(?:\*|[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?::(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?$"
_HOST_RE = re.compile(HOST_PATTERN)
# A commit SHA, optionally its parent. Branches, tags and HEAD move between
# repetitions, so they are refused (contract §3).
_BASE_RE = re.compile(r"^([0-9a-f]{7,40})(\^?)$")
TASK_REQUIRED = ("id", "family", "base", "prompt", "grader")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


# Run-dir entries the framework creates itself and the agent's container does not mount. An `image.env`
# scratch directory with one of these names would hand the agent the framework's own entry. The
# mounted ones (workspace, home, config) are not scratch and stay allowed: nothing new is mounted.
RESERVED_RUN_ENTRIES = frozenset({
    "grade-home", "agent-git",
    "meta.json", "task.json", "answer.txt", "events.jsonl", "result.json", "grade.json",
    "agent.stdout", "agent.stderr", "grade.log", "grade.err", "oracle.log", "oracle.err",
    "isolation.log", "isolation.err",
})
_SCRATCH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RUN_DIR_PREFIX = "{run_dir}/"


def _plain_part(part: str, first: bool) -> bool:
    if first:  # the entry itself: no leading dot (the runner's temporary files), no trailing dot (folded on Windows)
        return bool(_SCRATCH_PART.fullmatch(part)) and not part.endswith(".")
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", part)) and part not in (".", "..")


def scratch_name(value: str) -> str | None:
    """The run-dir entry an ``image.env`` value places state in, ``None`` for a value outside ``{run_dir}``.

    ``{run_dir}/ob`` and ``{run_dir}/ob/sub`` give ``ob``. Every component must be a plain name, and
    the first must not be a framework entry (compared without case: the host may fold it). Anything
    else (``{run_dir}//tmp``, ``..``, a NUL, a backslash) raises ``ValueError``.
    """
    if not value.startswith(_RUN_DIR_PREFIX):
        return None
    parts = value.removeprefix(_RUN_DIR_PREFIX).removesuffix("/").split("/")
    bad = next((p for i, p in enumerate(parts) if not _plain_part(p, first=i == 0)), None)
    if bad is not None:
        raise ValueError(f"{value!r}: {bad!r} is not a plain name (letters, digits, '.', '_' and '-', "
                         "starting with a letter or digit)")
    if parts[0].casefold() in RESERVED_RUN_ENTRIES:
        raise ValueError(f"{value!r}: {parts[0]!r} is a directory or file the runner creates in the run dir")
    return parts[0]


class ManifestError(ValueError):
    """The manifest is malformed; ``errors`` lists every problem found."""

    def __init__(self, path: str, errors: list[str]):
        self.path = path
        self.errors = errors
        super().__init__(f"{path}: invalid {SCHEMA_VERSION}:\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass(frozen=True)
class CacheSpec:
    name: str
    path: str


@dataclass(frozen=True)
class ImageSpec:
    dockerfile: str
    caches: tuple[CacheSpec, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    network: str = "none"


@dataclass(frozen=True)
class RepoManifest:
    """A validated ``repo-eval.yaml``. Every path is relative to the repository root."""

    path: str
    id: str
    guidance_globs: tuple[str, ...]
    # None in a guidance-only manifest: enough for `scan` and `reach`, not for running tasks
    eval_dir: str | None = None
    entry_points: dict[str, str] = field(default_factory=dict)
    tasks: str | None = None
    also_scan: tuple[str, ...] = ()
    source_url: str | None = None
    image: ImageSpec | None = None
    explore_tasks: str | None = None
    build_systems: tuple[str, ...] = ()

    @property
    def runnable(self) -> bool:
        """Whether the manifest declares tasks to run, or only the guidance to check."""
        return self.eval_dir is not None

    def run_paths(self) -> tuple[str, str]:
        """``(eval_dir, tasks)``; a guidance-only manifest raises :class:`ManifestError`."""
        if self.eval_dir is None or self.tasks is None:
            raise ManifestError(self.path, [
                "guidance-only manifest: running tasks needs eval_dir, tasks and entry_points "
                "(docs/repo-evaluation-contract.md §2)"
            ])
        return self.eval_dir, self.tasks

    def in_eval_dir(self, path: str) -> bool:
        return self.eval_dir is not None and _is_within(path, self.eval_dir)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "id": self.id,
            "manifest": self.path,
            "source_url": self.source_url,
            "eval_dir": self.eval_dir,
            "guidance": {"globs": list(self.guidance_globs), "also_scan": list(self.also_scan)},
            "image": None if self.image is None else {
                "dockerfile": self.image.dockerfile,
                "caches": [c.__dict__ for c in self.image.caches],
                "env": dict(self.image.env),
                "network": self.image.network,
            },
            "entry_points": dict(self.entry_points),
            "tasks": self.tasks,
            "explore_tasks": self.explore_tasks,
            "build_systems": list(self.build_systems),
        }


@dataclass
class TaskSummary:
    id: str
    family: str
    base: str
    grader: str
    gate: tuple[str, ...]
    score: dict[str, Any] | None = None
    egress: tuple[str, ...] = ()


@dataclass
class RepoCheckReport:
    """Outcome of :func:`check_repo`. ``ok`` means no errors; warnings do not fail."""

    tree: str
    manifest: RepoManifest
    tasks: list[TaskSummary] = field(default_factory=list)
    guidance_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # guidance file count per task base, filled when the bases were readable
    guidance_by_base: dict[str, int] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        families: dict[str, int] = {}
        for task in self.tasks:
            families[task.family] = families.get(task.family, 0) + 1
        return {
            "tree": self.tree,
            "ok": self.ok,
            "manifest": self.manifest.to_dict(),
            "guidance_files": self.guidance_files,
            "guidance_by_base": self.guidance_by_base,
            "tasks": {"count": len(self.tasks), "by_family": dict(sorted(families.items()))},
            "errors": self.errors,
            "warnings": self.warnings,
            "notes": self.notes,
        }


def load_manifest(tree: RepoTree, path: str = DEFAULT_MANIFEST_PATH) -> RepoManifest:
    """Read and validate the manifest at ``path`` inside ``tree``."""
    if not tree.is_file(path):
        raise ManifestError(path, [f"manifest not found in {tree.label}"])
    try:
        raw = yaml.safe_load(tree.read_text(path))
    except yaml.YAMLError as exc:
        raise ManifestError(path, [f"not valid YAML: {exc}"]) from exc
    return parse_manifest(raw, path)


def parse_manifest(raw: Any, path: str = "<manifest>") -> RepoManifest:
    """Validate an already-parsed manifest mapping."""
    # The schema says this too (dependentRequired); said first, in the manifest's own terms.
    # A runnable manifest needs all three keys; a guidance-only one, none of them (contract §2).
    run_keys = [k for k in _RUN_KEYS if k in raw] if isinstance(raw, dict) else []
    if run_keys and len(run_keys) < len(_RUN_KEYS):
        missing = [k for k in _RUN_KEYS if k not in raw]
        raise ManifestError(path, [f"{', '.join(run_keys)} without {', '.join(missing)}: a manifest "
                                   "that runs tasks needs all of eval_dir, tasks and entry_points"])
    needs_eval = [k for k in ("image", "explore_tasks")
                  if isinstance(raw, dict) and k in raw and not run_keys]
    if needs_eval:
        raise ManifestError(path, [f"{', '.join(needs_eval)} without eval_dir, tasks and entry_points: "
                                   "only a manifest that runs tasks uses them"])
    errors = schema_errors(SCHEMA_VERSION, raw)
    if errors:
        raise ManifestError(path, errors)

    for key, value in _path_fields(raw):
        problem = _path_problem(value)
        if problem:
            errors.append(f"{key}: {problem}: {value!r}")
    if "eval_dir" in raw and _norm(raw["eval_dir"]) == ".":
        errors.append("eval_dir: must be a subdirectory; it is deleted from every workspace")
    image = raw.get("image") or {}
    templates = [(f"entry_points/{k}", v) for k, v in (raw.get("entry_points") or {}).items()]
    templates += [(f"image/env/{k}", v) for k, v in (image.get("env") or {}).items()]
    for key, value in templates:
        found = set(_PLACEHOLDER_RE.findall(value))
        unknown = sorted(found - PLACEHOLDERS)
        if unknown:
            errors.append(f"{key}: unknown placeholder(s) {unknown}; allowed: {sorted(PLACEHOLDERS)}")
        if "source_dir" in found and key not in _SOURCE_DIR_ENTRY_POINTS:
            errors.append(f"{key}: {{source_dir}} holds the full history and is only for grade and oracle")
    if any("{source_dir}" in v for _, v in templates) and not (raw.get("source") or {}).get("url"):
        errors.append("source/url: required when an entry point uses {source_dir}, which clones it")
    for key, value in (image.get("env") or {}).items():
        try:
            scratch_name(value)
        except ValueError as exc:
            errors.append(f"image/env/{key}: {exc}")
    cache_names = [c["name"] for c in image.get("caches") or []]
    duplicates = sorted({n for n in cache_names if cache_names.count(n) > 1})
    if duplicates:
        errors.append(f"image/caches: duplicate cache name(s) {duplicates}")
    if errors:
        raise ManifestError(path, errors)

    return RepoManifest(
        path=path,
        id=raw["id"],
        eval_dir=_norm(raw["eval_dir"]) if "eval_dir" in raw else None,
        guidance_globs=tuple(raw["guidance"]["globs"]),
        also_scan=tuple(raw["guidance"].get("also_scan") or ()),
        entry_points=dict(raw.get("entry_points") or {}),
        tasks=_norm(raw["tasks"]) if "tasks" in raw else None,
        source_url=(raw.get("source") or {}).get("url"),
        image=None if not image else ImageSpec(
            dockerfile=_norm(image["dockerfile"]),
            caches=tuple(CacheSpec(c["name"], c["path"]) for c in image.get("caches") or ()),
            env=dict(image.get("env") or {}),
            network=image.get("network", "none"),
        ),
        explore_tasks=_norm(raw["explore_tasks"]) if raw.get("explore_tasks") else None,
        build_systems=tuple(raw.get("build_systems") or ()),
    )


def check_repo(tree: RepoTree, manifest: RepoManifest,
               open_base: Callable[[str], RepoTree] | None = None) -> RepoCheckReport:
    """Check ``manifest`` against the snapshot in ``tree``.

    With ``open_base`` (a task ``base`` to its tree), every base must resolve,
    and guidance globs are evaluated at each base too (contract §2).
    """
    report = RepoCheckReport(tree=tree.label, manifest=manifest)
    if manifest.eval_dir is None or manifest.tasks is None:
        report.warnings.extend(f"guidance glob matches no file: {p}"
                               for p in _check_guidance(tree, manifest, report))
        report.notes.append("guidance-only manifest: no eval_dir, tasks or entry_points, "
                            "so `repo scan` and `repo reach` apply and `repo run` does not")
        return report
    if not tree.is_dir(manifest.eval_dir):
        report.errors.append(f"eval_dir does not exist: {manifest.eval_dir}")
    elif not _is_within(manifest.path, manifest.eval_dir):
        report.warnings.append(
            f"manifest {manifest.path} is outside eval_dir {manifest.eval_dir}, "
            "so it stays visible to the agent"
        )
    if manifest.image is not None and not tree.is_file(manifest.image.dockerfile):
        report.errors.append(f"image/dockerfile does not exist: {manifest.image.dockerfile}")
    if manifest.explore_tasks and not tree.is_dir(manifest.explore_tasks):
        report.errors.append(f"explore_tasks does not exist: {manifest.explore_tasks}")
    elif manifest.explore_tasks and not _is_within(manifest.explore_tasks, manifest.eval_dir):
        report.errors.append(
            f"explore_tasks {manifest.explore_tasks} is outside eval_dir {manifest.eval_dir}; "
            "its reference answers would be visible to the agent"
        )

    files = tree.files()
    unmatched = _check_guidance(tree, manifest, report)
    inside_eval = [f for f in report.guidance_files if manifest.in_eval_dir(f)]
    if inside_eval:
        report.warnings.append(
            f"guidance files inside eval_dir are removed from every workspace: {inside_eval}"
        )

    if tree.is_dir(manifest.tasks):
        _check_tasks(tree, manifest, files, report)
    else:
        report.errors.append(f"tasks directory does not exist: {manifest.tasks}")
    if open_base is None:
        report.warnings.extend(f"guidance glob matches no file: {p}" for p in unmatched)
    else:
        from .bases import check_bases

        check_bases(report, open_base, unmatched)
    return report


def require_runnable(manifest: RepoManifest) -> None:
    """Refuse a guidance-only manifest where tasks are about to be prepared or run."""
    manifest.run_paths()


def _check_guidance(tree: RepoTree, manifest: RepoManifest, report: RepoCheckReport) -> list[str]:
    """Fill ``report.guidance_files``; return the globs that matched nothing."""
    files = tree.files()
    unmatched = []
    for pattern in manifest.guidance_globs:
        matched = [f for f in files if match_glob(pattern, f)]
        if not matched:
            unmatched.append(pattern)
        report.guidance_files.extend(m for m in matched if m not in report.guidance_files)
    for pattern in manifest.also_scan:
        if not any(match_glob(pattern, f) for f in files):
            report.warnings.append(f"guidance/also_scan matches no file: {pattern}")
    return unmatched


def _check_tasks(tree: RepoTree, manifest: RepoManifest, files: list[str], report: RepoCheckReport) -> None:
    eval_dir, tasks = manifest.run_paths()
    if not _is_within(tasks, eval_dir):
        report.errors.append(
            f"tasks {manifest.tasks} is outside eval_dir {manifest.eval_dir}; "
            "task content would be visible to the agent"
        )
    task_dirs = sorted({
        PurePosixPath(f).parent.as_posix() for f in files
        if PurePosixPath(f).name == "task.json"
        and PurePosixPath(f).parent.parent.as_posix() == manifest.tasks
    })
    if not task_dirs:
        report.errors.append(f"no <task>/task.json found under {manifest.tasks}")
    for task_dir in task_dirs:
        _check_task(tree, task_dir, report)


def match_glob(pattern: str, path: str) -> bool:
    """Match a root-relative POSIX path against a ``**``-aware glob.

    ``*`` and ``?`` stay inside one path segment; ``**/`` matches zero or more
    directories, so ``**/AGENTS.md`` also matches a root ``AGENTS.md``.
    """
    return _glob_regex(pattern).fullmatch(path) is not None


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _check_task(tree: RepoTree, task_dir: str, report: RepoCheckReport) -> None:
    name = PurePosixPath(task_dir).name
    where = f"{task_dir}/task.json"
    try:
        raw = json.loads(tree.read_text(where))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.errors.append(f"{where}: not valid JSON: {exc}")
        return
    if not isinstance(raw, dict):
        report.errors.append(f"{where}: must be a JSON object")
        return
    problems = [f"missing or empty required key '{k}'" for k in TASK_REQUIRED
                if not isinstance(raw.get(k), str) or not raw[k].strip()]
    if isinstance(raw.get("id"), str) and raw["id"] != name:
        problems.append(f"id {raw['id']!r} does not equal the directory name {name!r}")

    if "setup_patch" in raw and "setup_patches" in raw:
        problems.append("give setup_patch or setup_patches, not both")
    patches = raw.get("setup_patches", [raw["setup_patch"]] if "setup_patch" in raw else [])
    if not isinstance(patches, list) or not all(isinstance(p, str) for p in patches):
        problems.append("setup_patches must be a list of paths")
        patches = []
    if "review_patch" in raw:
        if isinstance(raw["review_patch"], str):
            patches = [*patches, raw["review_patch"]]
        else:
            problems.append("review_patch must be a path")
    for patch in patches:
        problem = _path_problem(patch)
        if problem:
            problems.append(f"patch {patch!r}: {problem}")
        elif not tree.is_file(f"{task_dir}/{patch}"):
            problems.append(f"patch file does not exist: {patch}")
    grader = raw.get("grader")
    if isinstance(grader, str) and grader.startswith(BUILTIN_PREFIX):
        problems += _builtin_problems(tree, task_dir, raw, grader[len(BUILTIN_PREFIX):])

    base = raw.get("base")
    if isinstance(base, str) and base.strip():
        match = _BASE_RE.match(base)
        if not match:
            problems.append(f"base {base!r} must be a commit SHA, optionally followed by '^'; "
                            "branches, tags and HEAD are refused")
        elif len(match.group(1)) < 40:
            sha, parent = match.groups()
            report.warnings.append(f"{where}: base {base!r}: SHA {sha!r} has {len(sha)} characters; use all 40"
                                   + (" (the trailing '^' is fine)" if parent else ""))

    gate, gate_problem = _parse_gate(raw.get("gate", ["pass"]))
    if gate_problem:
        problems.append(gate_problem)
    score = raw.get("score")
    if score is not None and not (
        isinstance(score, dict) and set(score) == {"metric", "max"}
        and isinstance(score["metric"], str) and score["metric"]
        and _is_number(score["max"])
    ):
        problems.append("score must be {\"metric\": <name>, \"max\": <number>}")
    egress = raw.get("egress", [])
    if not (isinstance(egress, list) and all(isinstance(h, str) and _HOST_RE.match(h) for h in egress)):
        problems.append("egress must be a list of hosts (DNS labels, `*` for one whole label, the last two literal, optional :port)")
        egress = []
    for key in ("timeout_s", "max_turns", "budget_usd"):
        value = raw.get(key)
        if value is not None and not _positive(value):
            problems.append(f"{key} must be a positive, finite number")

    report.errors.extend(f"{where}: {p}" for p in problems)
    if not problems:
        report.tasks.append(TaskSummary(
            id=raw["id"], family=raw["family"], base=raw["base"], grader=raw["grader"],
            gate=gate, score=score, egress=tuple(egress),
        ))


def _parse_gate(gate: Any) -> tuple[tuple[str, ...], str | None]:
    """Gate entries as display strings: ``name`` for a boolean metric, ``name>=min`` for a numeric one."""
    problem = "gate must be a non-empty list of metric names or {\"metric\": <name>, \"min\": <number>}"
    if not isinstance(gate, list) or not gate:
        return ("pass",), problem
    out = []
    for entry in gate:
        if isinstance(entry, str) and entry:
            out.append(entry)
        elif (isinstance(entry, dict) and set(entry) == {"metric", "min"}
              and isinstance(entry["metric"], str) and entry["metric"] and _is_number(entry["min"])):
            out.append(f"{entry['metric']}>={entry['min']}")
        else:
            return ("pass",), problem
    return tuple(out), None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive(value: Any) -> bool:
    """A finite number > 0: JSON ``NaN`` passes ``<= 0`` checks and would disable a cap."""
    return _is_number(value) and math.isfinite(value) and value > 0


def _builtin_problems(tree: RepoTree, task_dir: str, raw: dict[str, Any], name: str) -> list[str]:
    """A ``dokimi:`` grader must be one the framework has, and its data file must be in the task directory."""
    if name not in BUILTIN_NAMES:
        return [f"unknown built-in grader {BUILTIN_PREFIX + name!r}; known: "
                + ", ".join(BUILTIN_PREFIX + n for n in BUILTIN_NAMES)]
    key, default = DATA_FILES[name]
    path = raw.get(key, default)
    if not isinstance(path, str):
        return [f"{key} must be a path"]
    problem = _path_problem(path)
    if problem:
        return [f"{key} {path!r}: {problem}"]
    if not tree.is_file(f"{task_dir}/{path}"):
        return [f"{key} file does not exist: {path}"]
    if name == "review_replay" and not _positive(raw.get("window", 5)):  # review.py compares lines to it
        return [f"window must be a positive number, not {raw.get('window')!r}"]
    return []


def _path_fields(raw: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [(key, raw[key]) for key in ("eval_dir", "tasks") if key in raw]
    if raw.get("explore_tasks"):
        fields.append(("explore_tasks", raw["explore_tasks"]))
    if raw.get("image"):
        fields.append(("image/dockerfile", raw["image"]["dockerfile"]))
    guidance = raw["guidance"]
    fields += [(f"guidance/globs/{i}", g) for i, g in enumerate(guidance["globs"])]
    fields += [(f"guidance/also_scan/{i}", g) for i, g in enumerate(guidance.get("also_scan") or [])]
    return fields


def _path_problem(value: str) -> str | None:
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        return "must be relative to the repository root"
    if "\\" in value:
        return "must use '/' separators"
    if ".." in PurePosixPath(value).parts:
        return "must not contain '..'"
    return None


def _norm(path: str) -> str:
    return PurePosixPath(path).as_posix().removeprefix("./").rstrip("/") or "."


def _is_within(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(directory.rstrip("/") + "/")

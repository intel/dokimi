# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A repository's dokimi config (``dokimi_config.v1``): its subjects, their evals, changed-file impact.

Discovery, in order: an explicit path, ``.github/dokimi/config.yaml``, then ``.dokimi/config.yaml``,
all under the repository root (the nearest directory at or above the start holding ``.git``, else
the start itself). Every path in the config is repository-relative; a glob may use ``**``.

A subject is a skill (``skill/<name>``, a directory whose evals are a suite beside it or elsewhere)
or the repository (``kind: repo``, a manifest). ``skill_sha`` is the tree digest of a skill directory
without the files its suite matches; ``suite_hash`` digests the suite's files, plus the manifest of a
repository. A changed file maps to a suite, to a skill, to the ``shared`` list, or to nothing.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import math
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.utils import tree_digest

KIND = "dokimi_config"
CONFIG_CANDIDATES = (".github/dokimi/config.yaml", ".dokimi/config.yaml")
DEFAULT_SUITE = "{skill}/evals"
SKILL_PLACEHOLDER = "{skill}"
DRIVE = re.compile(r"[A-Za-z]:")


class ConfigError(ValueError):
    """The config is missing, unreadable, invalid or does not resolve; the message names the file or subject."""


@dataclass(frozen=True)
class Subject:
    """One thing under test. The hashes are None until :meth:`RepoConfig.subjects` computes them."""

    id: str
    kind: str  # "skill" or "repo"
    path: str  # the skill directory, repository-relative; "." for a repository
    manifest: str | None  # repository only
    suite_globs: tuple[str, ...]  # repository-relative, from the suite spec with {skill} filled in
    skill_sha: str | None = None
    suite_hash: str | None = None

    @property
    def eval_globs(self) -> tuple[str, ...]:
        """Every glob whose matches are this subject's evals: its suite, and the manifest, escaped to a literal."""
        return self.suite_globs + ((glob.escape(self.manifest),) if self.manifest else ())


@dataclass(frozen=True)
class Impact:
    """Where each changed file goes. ``unclaimed`` is for the caller to report as inconclusive."""

    skills: dict[str, list[str]] = field(default_factory=dict)  # subject id -> changed files in its skill
    suites: dict[str, list[str]] = field(default_factory=dict)  # subject id -> changed files in its evals
    shared: list[str] = field(default_factory=list)  # match ``shared``: the smoke set
    unclaimed: list[str] = field(default_factory=list)  # nothing claims them

    @property
    def subjects(self) -> list[str]:
        """Every subject id a changed file reaches, skill or suite, sorted."""
        return sorted(set(self.skills) | set(self.suites))


@dataclass(frozen=True)
class RepoConfig:
    """A schema-valid config, and the repository root its paths are relative to."""

    path: Path
    root: Path
    data: dict[str, Any]

    def subjects(self, *, hashes: bool = True) -> list[Subject]:
        """The subjects after defaults and explicit overrides, sorted by id; ``hashes=False`` skips the tree walks."""
        resolved = _resolve(self.data, self.root)
        for subject in resolved:
            _check_suite(subject, self.root)
        return [_hashed(s, self.root, resolved) for s in resolved] if hashes else resolved

    def subjects_for_changed(self, paths: Iterable[str]) -> Impact:
        """Map repository-relative changed paths onto subjects, suites and ``shared``; the rest is unclaimed."""
        subjects = self.subjects(hashes=False)
        shared = [*(_pattern(g, "shared") for g in _as_list(self.data.get("shared"))), *_own_path(self)]
        impact = Impact()
        for raw in paths:
            rel = _norm(str(raw))
            if rel is None:
                impact.unclaimed.append(str(raw))
                continue
            _map_one(rel, subjects, shared, impact)
        return impact


def find_repo_root(start: Path) -> Path:
    """The nearest directory at or above ``start`` holding ``.git`` (a directory, or a file in a worktree); else ``start``."""
    here = Path(start).resolve()
    for directory in (here, *here.parents):
        if (directory / ".git").exists():
            return directory
    return here


def discover(start: Path | None = None, *, explicit: Path | None = None) -> Path | None:
    """The config file to use: ``explicit`` (relative to ``start``, and it must exist), else the first candidate under the root, else None."""
    here = Path(start or Path.cwd()).resolve()
    if explicit is not None:
        path = Path(explicit) if Path(explicit).is_absolute() else here / explicit
        if not path.is_file():
            raise ConfigError(f"config not found: {path}")
        return path
    root = find_repo_root(here)
    for rel in CONFIG_CANDIDATES:
        if (root / rel).is_file():
            return root / rel
    return None


def load(path: Path, *, root: Path) -> RepoConfig:
    """Read and schema-validate the config at ``path``; its paths are relative to ``root``."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: cannot read the config: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the config must be a mapping")
    _check_budget_numbers(data, path)
    try:
        validate_artifact(KIND, data)
    except ArtifactValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    baseline = data.get("baseline")
    if isinstance(baseline, dict) and baseline.get("store") is False:
        baseline["store"] = "off"  # PyYAML reads an unquoted off as false
    return RepoConfig(path=path, root=Path(root), data=data)


_NUMBER_AS_TEXT = re.compile(r"[-+]?[0-9.]+[eE][-+]?[0-9]+")


def _check_budget_numbers(data: dict[str, Any], path: Path) -> None:
    """PyYAML reads ``1e3`` (no dot, no signed exponent) as text; say so, with the two spellings that parse."""
    budget = data.get("budget")
    if not isinstance(budget, dict):
        return
    for key in ("pr_max_usd", "record_max_usd"):
        value = budget.get(key)
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError(f"{path}: budget.{key} must be a finite number, not {value}: a cap of nan or inf never stops the spend")
        text = value.strip() if isinstance(value, str) else ""
        if _NUMBER_AS_TEXT.fullmatch(text):
            try:
                plain = format(Decimal(text), "f")  # exact: 1e-7 is 0.0000001, not 0
            except InvalidOperation:
                continue  # text that is no number at all: the schema refuses it as a type error
            raise ConfigError(
                f"{path}: budget.{key} is the text {value!r}, not a number: YAML reads it as text. "
                f"Write {plain} (the plain decimal) or 1.0e+3 style (a dot and a signed exponent)"
            )


def load_repo_config(start: Path | None = None, *, explicit: Path | None = None) -> RepoConfig:
    """:func:`discover` then :func:`load`, rooted at the repository above ``start``; raises when no config is found."""
    here = Path(start or Path.cwd()).resolve()
    path = discover(here, explicit=explicit)
    if path is None:
        raise ConfigError(f"no dokimi config under {find_repo_root(here)}: looked for {', '.join(CONFIG_CANDIDATES)}")
    return load(path, root=find_repo_root(here))


def _norm(value: str) -> str | None:
    """A repository-relative POSIX path or glob, or None when it is empty, absolute or climbs out."""
    norm = PurePosixPath(value.replace("\\", "/")).as_posix()
    # A drive (C:, C:skill) makes Path join discard the root on Windows, so it is refused like an absolute path.
    if norm in ("", ".") or norm.startswith("/") or DRIVE.match(norm) or ".." in norm.split("/"):
        return None
    return norm


def _pattern(value: Any, where: str) -> str:
    norm = _norm(str(value))
    if norm is None:
        raise ConfigError(f"{where}: {value!r} must be a path or glob inside the repository")
    return norm


def _glob(value: Any, where: str) -> str:
    """A glob from a glob field: ``**`` must be a whole path component (the glob module raises otherwise, on 3.11/3.12)."""
    norm = _pattern(value, where)
    if any("**" in seg and seg != "**" for seg in norm.split("/")):
        raise ConfigError(f"{where}: {value!r} is not a valid glob, '**' must be a whole path component")
    return norm


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _own_path(config: RepoConfig) -> list[str]:
    """The config file is shared: a change to it reruns the smoke set. Empty when it sits outside the root."""
    try:
        return [config.path.resolve().relative_to(config.root.resolve()).as_posix()]
    except ValueError:
        return []


def _claims(pattern: str, segs: tuple[str, ...]) -> bool:
    """True when the glob matches ``segs`` or one of its parent directories (a directory glob claims what is under it)."""
    pat = tuple(pattern.split("/"))
    return any(_match(pat, segs[:k]) for k in range(1, len(segs) + 1))


def _match(pat: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    if not pat:
        return not segs
    if pat[0] == "**":
        return any(_match(pat[1:], segs[i:]) for i in range(len(segs) + 1))
    return bool(segs) and fnmatch.fnmatchcase(segs[0], pat[0]) and _match(pat[1:], segs[1:])


def _map_one(rel: str, subjects: list[Subject], shared: list[str], impact: Impact) -> None:
    segs = tuple(rel.split("/"))
    is_shared = any(_claims(g, segs) for g in shared)
    suites = [s.id for s in subjects if any(_claims(g, segs) for g in s.eval_globs)]
    skill = _skill_owner(subjects, segs)
    if skill is not None and any(_claims(g, segs) for g in skill.suite_globs):
        skill = None  # the skill's own suite holds this file: it is eval, and skill_sha leaves it out
    if is_shared:
        impact.shared.append(rel)
    for sid in suites:
        impact.suites.setdefault(sid, []).append(rel)
    if skill is not None:
        impact.skills.setdefault(skill.id, []).append(rel)
    if not (is_shared or suites or skill is not None):
        impact.unclaimed.append(rel)


def _skill_owner(subjects: list[Subject], segs: tuple[str, ...]) -> Subject | None:
    """The skill whose directory holds ``segs``; the deepest one when skills nest."""
    owners = [s for s in subjects if s.kind == "skill" and segs[:len(_segs(s.path))] == _segs(s.path)]
    return max(owners, key=lambda s: len(_segs(s.path)), default=None)


def _segs(path: str) -> tuple[str, ...]:
    return tuple(path.split("/"))


def _resolve(data: dict[str, Any], root: Path) -> list[Subject]:
    """Defaults plus explicit subjects; an explicit subject replaces a default with its id or its directory."""
    template = (data.get("defaults") or {}).get("suite", DEFAULT_SUITE)
    explicit = [_subject(entry, root, template) for entry in data.get("subjects") or []]
    taken_ids = {s.id for s in explicit}
    taken_paths = {s.path for s in explicit}
    found = [_subject({"id": f"skill/{PurePosixPath(rel).name}", "path": rel}, root, template)
             for rel in _skill_dirs(data, root) if rel not in taken_paths]
    subjects = [*explicit, *(s for s in found if s.id not in taken_ids)]
    _check_unique(subjects)
    return sorted(subjects, key=lambda s: s.id)


def _skill_dirs(data: dict[str, Any], root: Path) -> list[str]:
    """Repository-relative directories matched by ``defaults.skills``; files do not count."""
    rels: set[str] = set()
    for pattern in _as_list((data.get("defaults") or {}).get("skills")):
        for match in root.glob(_glob(pattern, "defaults.skills")):
            if match.is_dir():
                rels.add(match.relative_to(root).as_posix())
    return sorted(rels)


def _check_unique(subjects: list[Subject]) -> None:
    ids: set[str] = set()
    paths: set[str] = set()
    for s in subjects:
        if s.id in ids:
            raise ConfigError(f"subject id {s.id!r} is used by more than one subject; give one of them a distinct id")
        ids.add(s.id)
        if s.kind == "skill":
            if s.path in paths:
                raise ConfigError(f"two subjects share the skill directory {s.path!r}")
            paths.add(s.path)


def _subject(entry: dict[str, Any], root: Path, template: Any) -> Subject:
    sid = str(entry["id"])
    if entry.get("kind", "skill") == "repo":
        manifest = _pattern(entry.get("manifest"), f"subject {sid}: manifest")
        if not (root / manifest).is_file():
            raise ConfigError(f"subject {sid}: manifest {manifest} is not a file under {root}")
        globs = _suite_globs(entry.get("suite"), where=f"subject {sid}: suite", skill=None)
        return Subject(id=sid, kind="repo", path=".", manifest=manifest, suite_globs=globs)
    path = _pattern(entry.get("path"), f"subject {sid}: path")
    if not (root / path).is_dir():
        raise ConfigError(f"subject {sid}: {path} is not a directory under {root}")
    globs = _suite_globs(entry.get("suite", template), where=f"subject {sid}: suite", skill=path)
    return Subject(id=sid, kind="skill", path=path, manifest=None, suite_globs=globs)


def _suite_globs(spec: Any, *, where: str, skill: str | None) -> tuple[str, ...]:
    """Flatten a suite spec (one glob, a list, or named layouts) into repository-relative globs."""
    if spec is None:
        return ()
    values = list(spec.values()) if isinstance(spec, dict) else [spec]
    globs: list[str] = []
    for value in values:
        for text in _as_list(value):
            if skill is None and SKILL_PLACEHOLDER in text:
                raise ConfigError(f"{where}: {SKILL_PLACEHOLDER} needs a skill subject, not a repository")
            globs.append(_glob((text.replace(SKILL_PLACEHOLDER, glob.escape(skill)) if skill else text), where))
    return tuple(dict.fromkeys(globs))


def _suite_files(root: Path, globs: tuple[str, ...]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for pattern in globs:
        for match in sorted(root.glob(pattern)):
            found.setdefault(match.relative_to(root).as_posix(), match)
    return found


def _digest_files(files: dict[str, Path]) -> str:
    """One digest over the suite's files. A directory is walked file by file, and a symlink is hashed by what it points to."""
    h = hashlib.sha256()
    for rel in sorted(files):
        for name, path in _walk(files[rel], rel):
            target = path.resolve() if path.exists() else path
            h.update(f"{name}\0{tree_digest(target)}\n".encode())
    return "sha256:" + h.hexdigest()


def _walk(path: Path, rel: str) -> list[tuple[str, Path]]:
    """The regular files under ``path`` (or ``path`` itself), following symlinks; a directory is visited once, so a cycle ends."""
    if not path.is_dir():
        return [(rel, path)]
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def visit(directory: Path, prefix: str) -> None:
        real = directory.resolve()
        if real in seen:
            return
        seen.add(real)
        for child in sorted(directory.iterdir()):
            if child.is_dir():
                visit(child, f"{prefix}/{child.name}")
            elif child.is_file():
                found.append((f"{prefix}/{child.name}", child))

    visit(path, rel)
    return sorted(found)


def _within(rel: str, base: str) -> bool:
    """True when ``rel`` is ``base`` or sits under it (repository-relative paths)."""
    return rel == base or rel.startswith(base + "/")


def _check_suite(subject: Subject, root: Path) -> None:
    """A skill's suite claims whole directories of its skill, never single files of it (SKILL.md, scripts, references)."""
    if subject.kind != "skill":
        return
    matches = _suite_files(root, subject.suite_globs)
    real_skill = (root / subject.path).resolve()
    for rel, path in matches.items():
        if rel == subject.path:
            raise ConfigError(f"subject {subject.id}: its suite matches the whole skill directory {subject.path}")
        if subject.path.startswith(rel + "/"):
            raise ConfigError(f"subject {subject.id}: its suite {rel} contains the skill directory {subject.path}")
        if path.is_dir() and real_skill.is_relative_to(path.resolve()):
            raise ConfigError(
                f"subject {subject.id}: its directory {rel} links to its skill or an ancestor of it, "
                "so the suite would hold the skill's own files"
            )
    dirs = [rel for rel, path in matches.items() if path.is_dir() and _within(rel, subject.path)]
    for rel, path in matches.items():
        if path.is_dir() or not _within(rel, subject.path) or any(_within(rel, d) for d in dirs):
            continue
        raise ConfigError(
            f"subject {subject.id}: its suite claims the file {rel} of its skill, not a whole directory; "
            f"claim the directory instead, for example {subject.path}/evals/**"
        )


def _hashed(subject: Subject, root: Path, all_subjects: list[Subject]) -> Subject:
    files = _suite_files(root, subject.suite_globs)
    if subject.kind == "repo":
        assert subject.manifest is not None  # set by _subject for kind repo
        files.setdefault(subject.manifest, root / subject.manifest)
        return replace(subject, suite_hash=_digest_files(files))
    inside = [rel[len(subject.path) + 1:] for rel in files if _within(rel, subject.path) and rel != subject.path]
    # a nested skill is its own subject: its files belong to its own skill_sha, not to the outer one
    nested = [
        s.path[len(subject.path) + 1:]
        for s in all_subjects
        if s.kind == "skill" and s.path != subject.path and _within(s.path, subject.path)
    ]
    # A skill directory that is a symlink is its target's content (a suite match is hashed by its link instead).
    skill_sha = "sha256:" + tree_digest((root / subject.path).resolve(), exclude=[*inside, *nested])
    return replace(subject, skill_sha=skill_sha, suite_hash=_digest_files(files))

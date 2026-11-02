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
import os
import math
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import yaml

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.repo_glob import glob_links as _glob_links, glob_paths as _glob_paths
from dokimi.repo_links import _declared_links, _hops, _inside, _real, _under, _walk, _within
from dokimi.utils import _tracked_modes, link_text, tree_digest

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
        """The subjects after defaults and explicit overrides, sorted by id; ``hashes=False`` skips the tree walks.
        A directory that cannot be read raises ConfigError naming it."""
        try:
            resolved = _resolve(self.data, self.root)
            _check_nesting(resolved, self.root)
            for subject in resolved:
                _check_suite(subject, self.root)
            if not hashes:
                return resolved
            modes = _tracked_modes(self.root, ".")  # the index is read once for every subject's suite files
            return [_hashed(s, self.root, resolved, modes) for s in resolved]
        except OSError as exc:
            raise _unreadable(exc) from exc

    def subjects_for_changed(self, paths: Iterable[str]) -> Impact:
        """Map repository-relative changed paths onto subjects, suites and ``shared``; the rest is unclaimed."""
        try:
            subjects = self.subjects(hashes=False)
            reach = {s.id: _reach(s, self.root) for s in subjects}
            shared = [*(_pattern(g, "shared") for g in _as_list(self.data.get("shared"))), *_own_path(self)]
            impact = Impact()
            for raw in paths:
                rel = _norm(str(raw))
                if rel is None:
                    impact.unclaimed.append(str(raw))
                    continue
                _map_one(rel, subjects, reach, shared, impact, gone=not os.path.lexists(self.root / rel))
            return impact
        except OSError as exc:
            raise _unreadable(exc) from exc


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
                number = Decimal(text)
            except InvalidOperation:
                continue  # text that is no number at all: the schema refuses it as a type error
            spelled = "Write the number with a dot and a signed exponent, 1.0e+3 style"
            if abs(number.adjusted()) <= 30:  # exact: 1e-7 is 0.0000001, not 0; a huge exponent is not expanded
                spelled = f"Write {format(number, 'f')} (the plain decimal) or 1.0e+3 style (a dot and a signed exponent)"
            raise ConfigError(f"{path}: budget.{key} is the text {value!r}, not a number: YAML reads it as text. {spelled}")


def load_repo_config(start: Path | None = None, *, explicit: Path | None = None) -> RepoConfig:
    """:func:`discover` then :func:`load`, rooted at the repository above ``start``; raises when no config is found."""
    here = Path(start or Path.cwd()).resolve()
    path = discover(here, explicit=explicit)
    if path is None:
        raise ConfigError(f"no dokimi config under {find_repo_root(here)}: looked for {', '.join(CONFIG_CANDIDATES)}")
    return load(path, root=find_repo_root(here))


def _norm(value: str) -> str | None:
    """A repository-relative POSIX path or glob, or None when it is empty, absolute or climbs out. git names changed
    files with "/" on every OS; a backslash is a separator only on Windows, and an ordinary name character elsewhere."""
    norm = PurePosixPath(value.replace("\\", "/") if os.sep == "\\" else value).as_posix()
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
    """The config file is shared: a change to it reruns the smoke set. Both its declared path and the file it
    resolves to, so a config linked from elsewhere in the repository counts at either. A path outside the root is
    skipped. Each is a literal path, but ``shared`` is matched as a glob, so its metacharacters are escaped."""
    found: list[str] = []
    for path, root in ((config.path, config.root), (config.path.resolve(), config.root.resolve())):
        try:
            found.append(glob.escape(path.relative_to(root).as_posix()))
        except ValueError:
            pass
    # and every link on the way to it, as written: retargeting an intermediate link moves the config too
    root_real = _real(config.root)
    try:
        hops = _hops(config.path, config.root, root_real) if root_real is not None else []
    except ValueError:  # the config is not under its root as written
        hops = []
    found.extend(glob.escape("/".join(hop)) for hop in hops)
    return list(dict.fromkeys(found))


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


@dataclass(frozen=True)
class Reach:
    """Where a subject's hashes read, by repository-relative segments.

    ``skills``: skill directories, declared and resolved (a change under one moves skill_sha).
    ``suites``: suite directories and files, reached through links (a change under one moves suite_hash).
    ``links``, ``skill_links``: symlinks on the declared way to a suite read or to a skill directory, matched
    exactly: retargeting one moves the hash, but a change to a file beside it does not.
    ``globs``: the eval globs, with a skill's resolved directory in place of its declared one, so a suite still
    claims its files after the link's target loses them (a deleted eval has no match to find)."""

    skills: list[tuple[str, ...]]
    suites: list[tuple[str, ...]]
    links: list[tuple[str, ...]]
    skill_links: list[tuple[str, ...]]
    globs: tuple[str, ...]


def _reach(subject: Subject, root: Path) -> Reach:
    """Where a subject's hashes read, as repository-relative segments, through links as well as by declared path:
    a symlinked skill directory or suite file maps a change at its target to the subject, and a symlink on the way
    to any of them maps a change to the link itself."""
    root_real = _real(root)
    skills: list[tuple[str, ...]] = [_segs(subject.path)] if subject.kind == "skill" else []
    suites: list[tuple[str, ...]] = []
    links: list[tuple[str, ...]] = []
    skill_links: list[tuple[str, ...]] = []
    globs = subject.eval_globs
    if root_real is None:
        return Reach(skills, suites, links, skill_links, globs)
    real_skill = _inside(root / subject.path, root_real) if subject.kind == "skill" else None
    if subject.kind == "skill":  # the links on the way to the skill directory, whether it resolves inside the repository or not
        skill_links.extend(_hops(root / subject.path, root, root_real))
    if real_skill is not None:
        skills.append(real_skill)
        declared, resolved = glob.escape(subject.path), glob.escape("/".join(real_skill))
        if resolved != declared:  # a linked skill: its suite globs, rewritten to where the files live
            moved = [resolved + g[len(declared):] for g in globs if g == declared or g.startswith(declared + "/")]
            globs = (*globs, *moved)
    if subject.manifest is not None:  # the repo subject hashes its manifest through a link, so its target is covered
        real_manifest = _inside(root / subject.manifest, root_real)
        if real_manifest is not None:
            suites.append(real_manifest)
        links.extend(_hops(root / subject.manifest, root, root_real))
    matches = _suite_files(root, subject.suite_globs)
    named = {**matches, **_resolved_spellings(matches, root_real)}  # each match by its spelling, and as it resolves
    for rel, path in matches.items():
        for _, entry in _walk(path, rel, dirs=True):
            _suite_entry(entry, root, root_real, real_skill, suites, links, skill_links, named)
    globs = (*globs, *_unnamed_literals(subject.suite_globs, named, root, root_real, real_skill,
                                        suites, links, skill_links))
    return Reach(skills, suites, links, skill_links, globs)


def _unnamed_literals(literals: tuple[str, ...], matches: dict[str, Path], root: Path, root_real: Path,
                      real_skill: tuple[str, ...] | None, suites: list[tuple[str, ...]],
                      links: list[tuple[str, ...]], skill_links: list[tuple[str, ...]]) -> tuple[str, ...]:
    """A literal suite path that names nothing now (its file deleted, or a link on the way retargeted where the file
    is absent) still claims the links on its way and the place it resolves to, so the change maps to the suite.
    A glob claims the links on the way to its literal prefix and its own pattern under the place the prefix resolves
    to, whether or not it still matches files (deleting one of several matches moves the suite too).
    Returns the escaped glob of each such place that resolves inside the repo."""
    globs: list[str] = []
    for literal in literals:
        if literal in matches:
            continue
        segs = literal.split("/")
        wild = next((i for i, s in enumerate(segs) if any(c in s for c in "*?[")), None)
        if wild is not None:  # a glob claims each link its walk reads through, and its own pattern where its prefix resolves
            for link, rest in _glob_links(root, literal):
                links.extend(_hops(link, root, root_real))
                real_link = _inside(link, root_real)  # the rest of the pattern, under the place the link resolves to
                if real_link is not None and rest:
                    globs.append("/".join([glob.escape("/".join(real_link)), *rest]))
            if wild == 0:
                continue
            prefix = root.joinpath(*segs[:wild])
            real = _inside(prefix, root_real)
            if real is not None:
                globs.append("/".join([glob.escape("/".join(real)), *segs[wild:]]))
            continue
        entry = root.joinpath(*segs)
        if not _declared_links(entry, root):
            # no link on the way: the literal is its own place, as declared. Resolving it would take the spelling on
            # disk, which a case-folding filesystem (Windows) returns for a name that differs only in case.
            suites.append(tuple(segs))
            globs.append(glob.escape("/".join(segs)))
            continue
        _suite_entry(entry, root, root_real, real_skill, suites, links, skill_links, matches)
        real = _inside(entry, root_real)
        if real is not None:
            globs.append(glob.escape("/".join(real)))
    return tuple(globs)


def _resolved_spellings(matches: dict[str, Path], root_real: Path) -> dict[str, Path]:
    """Each match again, spelled by where its directory resolves: a suite reached through a linked skill is matched
    under the link's spelling, and its files are hashed where they live."""
    spelled: dict[str, Path] = {}
    for path in matches.values():
        segs = _inside(path.parent, root_real)
        if segs is not None:
            spelled["/".join((*segs, path.name))] = path
    return spelled


def _suite_entry(entry: Path, root: Path, root_real: Path, real_skill: tuple[str, ...] | None,
                 suites: list[tuple[str, ...]], links: list[tuple[str, ...]],
                 skill_links: list[tuple[str, ...]], matches: dict[str, Path]) -> None:
    """Add one suite file's claims: its own location and the links on the way to it, and a link inside the skill
    that the suite reaches through (skill_sha covers it, so the skill claims it too). A link the suite matches is
    left out of skill_sha, so it is not a skill link; ``matches`` holds the suite's matched paths, under their
    declared and their resolved spellings."""
    for hop in _hops(entry, root, root_real):
        links.append(hop)
        in_suite = any(_within("/".join(hop), m) for m in matches)
        if real_skill is not None and _under(hop, real_skill) and not in_suite:
            skill_links.append(hop)
    real = _inside(entry, root_real)
    if real is not None:
        suites.append(real)


def _in_suite(subject: Subject, segs: tuple[str, ...], reach: Reach, gone: bool = False) -> bool:
    return (any(_claims(g, segs) or (gone and _ancestor_of(g, segs)) for g in reach.globs)
            or any(_under(segs, r) for r in reach.suites) or segs in reach.links)


def _ancestor_of(pattern: str, segs: tuple[str, ...]) -> bool:
    """True when ``segs`` is a strict ancestor of a path the glob ``pattern`` matches. Only asked of a changed path that
    is gone from disk: a link on the way to a suite that was deleted still changes what the suite reads, and no link
    is left to find, so the change claims the suite. (A ``**`` leading the pattern can match under any path.)"""
    return _ancestor(tuple(pattern.split("/")), segs)


def _ancestor(pat: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    if not segs:
        return bool(pat)
    if not pat:
        return False
    if pat[0] == "**":
        return True  # a ** below here may match anything under the path
    return fnmatch.fnmatchcase(segs[0], pat[0]) and _ancestor(pat[1:], segs[1:])


def _map_one(rel: str, subjects: list[Subject], reach: dict[str, Reach], shared: list[str], impact: Impact,
             *, gone: bool = False) -> None:
    segs = tuple(rel.split("/"))
    is_shared = any(_claims(g, segs) for g in shared)
    suites = [s.id for s in subjects if _in_suite(s, segs, reach[s.id], gone)]
    # a skill whose own suite holds this file gets nothing: it is eval, and skill_sha leaves it out
    skills = [
        s for s in _skill_owners(subjects, reach, segs)
        if not _in_suite(s, segs, reach[s.id], gone) or segs in reach[s.id].skill_links
    ]
    if is_shared:
        impact.shared.append(rel)
    for sid in suites:
        impact.suites.setdefault(sid, []).append(rel)
    for skill in skills:
        impact.skills.setdefault(skill.id, []).append(rel)
    if not (is_shared or suites or skills):
        impact.unclaimed.append(rel)


def _skill_owners(subjects: list[Subject], reach: dict[str, Reach], segs: tuple[str, ...]) -> list[Subject]:
    """The skills whose directory (declared or resolved) holds ``segs``: the deepest ones when skills nest. Two subjects
    that alias one directory both own its files, since both skill_sha values cover them. A symlink on a skill's way
    is owned by that skill, whatever is beside it, since retargeting it changes the directory the skill hashes."""
    depth = max(
        (len(root) for s in subjects if s.kind == "skill" for root in reach[s.id].skills if _under(segs, root)),
        default=0,
    )
    owned = {
        s.id for s in subjects
        if s.kind == "skill" and depth and any(len(root) == depth and _under(segs, root) for root in reach[s.id].skills)
    }
    owned |= {s.id for s in subjects if s.kind == "skill" and segs in reach[s.id].skill_links}
    return [s for s in subjects if s.id in owned]


def _segs(path: str) -> tuple[str, ...]:
    return tuple(path.split("/"))


def _resolve(data: dict[str, Any], root: Path) -> list[Subject]:
    """Defaults plus explicit subjects; an explicit subject replaces a default with its id or its directory."""
    template = (data.get("defaults") or {}).get("suite", DEFAULT_SUITE)
    explicit = [_subject(entry, root, template) for entry in data.get("subjects") or []]
    taken_paths = {s.path for s in explicit}
    found: list[Subject] = []
    for rel in _skill_dirs(data, root):
        if rel in taken_paths:
            continue
        default = _subject({"id": f"skill/{PurePosixPath(rel).name}", "path": rel}, root, template)
        clash = next((s for s in explicit if s.id == default.id), None)
        if clash is not None:
            raise ConfigError(
                f"subject id {default.id!r} is the explicit subject at {clash.path}, and the default skill at {rel} "
                "has the same id: give one of them a distinct id"
            )
        found.append(default)
    subjects = [*explicit, *found]
    _check_unique(subjects)
    return sorted(subjects, key=lambda s: s.id)


def _check_nesting(subjects: list[Subject], root: Path) -> None:
    """A skill whose resolved directory lies inside another skill's is refused unless its declared path lies inside the
    other's declared path and resolves to the same place inside it: the outer skill's hash covers the inner's resolved
    directory, and a change to it could not be attributed to one owner. Skills that alias one directory are both owners,
    so they are allowed."""
    real = {s.id: _real(root / s.path) for s in subjects if s.kind == "skill"}
    for s in subjects:
        s_real = real.get(s.id) if s.kind == "skill" else None
        if s_real is None:
            continue
        for t in subjects:
            t_real = real.get(t.id) if t.kind == "skill" and t is not s else None
            if t_real is None or t_real == s_real:
                continue
            if not s_real.is_relative_to(t_real):
                continue
            # the outer hash leaves out the inner's declared directory only, so the inner must resolve to that same
            # place inside the outer (a link inside the outer that the inner reaches would stay in the outer's hash)
            declared = s.path[len(t.path) + 1:] if _within(s.path, t.path) else None
            if declared is None or s_real.relative_to(t_real).as_posix() != declared:
                raise ConfigError(
                    f"subject {s.id}: its directory {s.path} is inside the skill {t.path} through a link; "
                    "a skill reached by a link into another skill is not supported"
                )


def _skill_dirs(data: dict[str, Any], root: Path) -> list[str]:
    """Repository-relative directories matched by ``defaults.skills``; files do not count."""
    rels: set[str] = set()
    for pattern in _as_list((data.get("defaults") or {}).get("skills")):
        for match in _glob_paths(root, _glob(pattern, "defaults.skills")):
            if match.is_dir():  # an OSError here is turned into ConfigError by the caller
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


def _unreadable(exc: OSError) -> ConfigError:
    """A ConfigError naming the path that could not be read, so an unreadable directory is a config problem, not a
    traceback out of the loader."""
    return ConfigError(f"cannot read {exc.filename or 'part of the repository'}: {exc.strerror or exc}")


def _suite_files(root: Path, globs: tuple[str, ...]) -> dict[str, Path]:
    """The suite's matches by repository-relative path. A match under another match is left out: that match's walk
    already covers it, so ``{skill}/evals/**`` hashes the same whether ``**`` yields files too (3.13+) or not (3.11/3.12)."""
    found: dict[str, Path] = {}
    for pattern in globs:
        for match in _glob_paths(root, pattern, strict=True):
            found.setdefault(match.relative_to(root).as_posix(), match)
    return {rel: path for rel, path in found.items() if not any(other != rel and _within(rel, other) for other in found)}


def _digest_files(files: dict[str, Path], root: Path, modes: Mapping[str, bool] | None = None) -> str:
    """One digest over the suite's files. A directory is walked file by file; a symlink counts by its target text
    (as tree_digest does) and by what it points to, so retargeting a link moves the hash even between identical trees.
    Every link met on the way to a match or a file counts too (a link above it, or one on the way to its target), so
    retargeting a link on the way to a suite moves the hash even when the files behind it are identical, or there are
    none. ``modes`` are the index's executable bits keyed by repository-relative path, read once by the caller."""
    h = hashlib.sha256()
    root_real = _real(root) or root
    seen: set[tuple[str, ...]] = set()  # a link on the way to several matches is hashed once

    def hop_lines(path: Path) -> None:
        for hop in _hops(path, root, root_real):
            if hop not in seen:
                seen.add(hop)
                text = link_text(root.joinpath(*hop), root)  # the same spelling of the tree as the symlink lines below
                h.update(os.fsencode(f"link {'/'.join(hop)}\0-> {text}\n"))

    for rel in sorted(files):
        hop_lines(files[rel])  # a directory match with no file under it reads its links too
        for name, path in _walk(files[rel], rel):
            hop_lines(path)
            if path.is_symlink():
                h.update(os.fsencode(f"{name}\0-> {link_text(path, root)}\n"))  # names may hold undecodable bytes
            if path.is_dir():
                continue  # a linked directory: its files are walked and hashed one by one
            target = path.resolve() if path.exists() else path
            h.update(os.fsencode(f"{name}\0{tree_digest(target, tracked=_file_modes(target, root_real, modes))}\n"))
    return "sha256:" + h.hexdigest()


def _file_modes(target: Path, root_real: Path, modes: Mapping[str, bool] | None) -> dict[str, bool] | None:
    """The index's executable bit for one file, keyed as tree_digest keys a file it digests by itself. None when the
    index was not read, or the file is not in the repository's index (a submodule's, say): git is asked about it then."""
    if modes is None:
        return None
    try:
        key = target.relative_to(root_real).as_posix()
    except ValueError:
        return None
    return {target.name: modes[key]} if key in modes else None


def _check_suite(subject: Subject, root: Path) -> None:
    """A skill's suite claims whole directories of its skill, never single files of it (SKILL.md, scripts, references),
    and no link in it reaches into its skill or to an ancestor of it. Declared paths and resolved links are both checked."""
    if subject.kind != "skill":
        return
    matches = _suite_files(root, subject.suite_globs)
    real_skill = _real(root / subject.path)
    if real_skill is None:
        raise ConfigError(f"subject {subject.id}: {subject.path} is a link that does not resolve")
    real_root = _real(root)
    if real_root is not None and os.path.normcase(os.fspath(real_skill)) == os.path.normcase(os.fspath(real_root)):
        raise ConfigError(f"subject {subject.id}: {subject.path} resolves to the repository root; a skill is a directory inside it")
    for rel, path in matches.items():
        _check_match(subject, rel, path, real_skill, root)
    _check_claims(subject, matches, real_skill)


def _into_skill(path: Path, real_skill: Path) -> bool:
    real = _real(path)
    return real is not None and real.is_relative_to(real_skill)


def _check_match(subject: Subject, rel: str, path: Path, real_skill: Path, root: Path) -> None:
    if rel == subject.path:
        raise ConfigError(f"subject {subject.id}: its suite matches the whole skill directory {subject.path}")
    if subject.path.startswith(rel + "/"):
        raise ConfigError(f"subject {subject.id}: its suite {rel} contains the skill directory {subject.path}")
    real = _real(path)
    if real is None:
        raise ConfigError(f"subject {subject.id}: its suite holds {rel}, a link that loops; a suite holds real files")
    if path.is_dir() and real_skill.is_relative_to(real):
        raise ConfigError(
            f"subject {subject.id}: its directory {rel} links to its skill or an ancestor of it, "
            "so the suite would hold the skill's own files"
        )
    for name, entry in _walk(path, rel):
        if entry.is_symlink():
            _check_link(subject, name, entry, real_skill)
    # a link on the way to the match is read by the suite as much as one inside it, so the same rule applies to it,
    # except the links on the skill's own way (a skill that is itself a link into the repository)
    _check_hops(subject, rel, path, real_skill, root)


def _check_hops(subject: Subject, rel: str, path: Path, real_skill: Path, root: Path) -> None:
    """A link on the way to the match that leads into the skill (or to an ancestor of it) is refused as one inside."""
    root_real = _real(root)
    if root_real is None:
        return
    own_way = set(_hops(root / subject.path, root, root_real))
    for hop in _hops(path, root, root_real):
        target = _real(root.joinpath(*hop))
        if hop in own_way or target is None:
            continue
        if target.is_relative_to(real_skill) or real_skill.is_relative_to(target):
            raise ConfigError(
                f"subject {subject.id}: its suite holds {rel}, reached through the link {'/'.join(hop)}, which links "
                f"into its skill {subject.path} or to an ancestor of it; a suite holds its own files, not links into the skill"
            )


def _check_link(subject: Subject, name: str, entry: Path, real_skill: Path) -> None:
    target = _real(entry)
    if target is None:
        raise ConfigError(f"subject {subject.id}: its suite holds {name}, a link that loops; a suite holds real files")
    if target.is_relative_to(real_skill):
        raise ConfigError(
            f"subject {subject.id}: its suite holds {name}, a link into its skill {subject.path}; "
            "a suite holds its own files, not links into the skill"
        )
    if real_skill.is_relative_to(target):
        raise ConfigError(
            f"subject {subject.id}: its suite holds {name}, a link to {subject.path} or an ancestor of it, "
            "so the suite would hold the skill's own files"
        )


def _check_claims(subject: Subject, matches: dict[str, Path], real_skill: Path) -> None:
    dirs = [rel for rel, path in matches.items() if path.is_dir() and _within(rel, subject.path)]
    for rel, path in matches.items():
        into = _into_skill(path, real_skill)
        if path.is_dir() and not _within(rel, subject.path) and into:
            how = "links into" if path.is_symlink() else "is inside the real directory of"
            raise ConfigError(
                f"subject {subject.id}: its directory {rel} {how} its skill {subject.path}, "
                "so the skill's hash would cover the suite's files"
            )
        inside = _within(rel, subject.path) or into
        if path.is_dir() or not inside or any(_within(rel, d) for d in dirs):
            continue
        raise ConfigError(
            f"subject {subject.id}: its suite claims the file {rel} of its skill, not a whole directory; "
            f"claim the directory instead, for example {subject.path}/evals/**"
        )


def _hashed(subject: Subject, root: Path, all_subjects: list[Subject], modes: Mapping[str, bool] | None) -> Subject:
    files = _suite_files(root, subject.suite_globs)
    if subject.kind == "repo":
        assert subject.manifest is not None  # set by _subject for kind repo
        if not any(_within(subject.manifest, rel) for rel in files):  # a suite directory already walks it
            files.setdefault(subject.manifest, root / subject.manifest)
        return replace(subject, suite_hash=_digest_files(files, root, modes))
    inside = [rel[len(subject.path) + 1:] for rel in files if _within(rel, subject.path) and rel != subject.path]
    # a nested skill is its own subject: its files belong to its own skill_sha, not to the outer one
    nested = [
        s.path[len(subject.path) + 1:]
        for s in all_subjects
        if s.kind == "skill" and s.path != subject.path and _within(s.path, subject.path)
    ]
    # A skill directory that is a symlink is its target's content (a suite match is hashed by its link instead).
    skill_sha = "sha256:" + tree_digest((root / subject.path).resolve(), exclude=[*inside, *nested], strict=True)
    return replace(subject, skill_sha=skill_sha, suite_hash=_digest_files(files, root, modes))

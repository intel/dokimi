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
        """The subjects after defaults and explicit overrides, sorted by id; ``hashes=False`` skips the tree walks.
        A directory that cannot be read raises ConfigError naming it."""
        try:
            resolved = _resolve(self.data, self.root)
            _check_nesting(resolved, self.root)
            for subject in resolved:
                _check_suite(subject, self.root)
            return [_hashed(s, self.root, resolved) for s in resolved] if hashes else resolved
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
                _map_one(rel, subjects, reach, shared, impact)
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
    """The config file is shared: a change to it reruns the smoke set. Both its declared path and the file it
    resolves to, so a config linked from elsewhere in the repository counts at either. A path outside the root is
    skipped. Each is a literal path, but ``shared`` is matched as a glob, so its metacharacters are escaped."""
    found: list[str] = []
    for path, root in ((config.path, config.root), (config.path.resolve(), config.root.resolve())):
        try:
            found.append(glob.escape(path.relative_to(root).as_posix()))
        except ValueError:
            pass
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


def _link_ancestors(rel: str, root: Path) -> list[tuple[str, ...]]:
    """The proper ancestors of ``rel`` (repository-relative) that are symbolic links under ``root``."""
    parts = rel.split("/")
    return [tuple(parts[:k]) for k in range(1, len(parts)) if root.joinpath(*parts[:k]).is_symlink()]


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
    if real_skill is not None:
        skills.append(real_skill)
        declared, resolved = glob.escape(subject.path), glob.escape("/".join(real_skill))
        if resolved != declared:  # a linked skill: its suite globs, rewritten to where the files live
            moved = [resolved + g[len(declared):] for g in globs if g == declared or g.startswith(declared + "/")]
            globs = (*globs, *moved)
        skill_links.extend(_link_ancestors(subject.path, root))
    if subject.manifest is not None:  # the repo subject hashes its manifest through a link, so its target is covered
        real_manifest = _inside(root / subject.manifest, root_real)
        if real_manifest is not None:
            suites.append(real_manifest)
        links.extend(_link_ancestors(subject.manifest, root))
    for rel, path in _suite_files(root, subject.suite_globs).items():
        links.extend(_link_ancestors(rel, root))
        for _, entry in _walk(path, rel, dirs=True):
            real = _inside(entry, root_real)
            if real is not None:
                suites.append(real)
    return Reach(skills, suites, links, skill_links, globs)


def _inside(path: Path, root_real: Path) -> tuple[str, ...] | None:
    """The segments of ``path``'s resolved location under the repository root; None when it is the root or outside it."""
    real = _real(path)
    if real is None or real == root_real or not real.is_relative_to(root_real):
        return None
    return tuple(real.relative_to(root_real).as_posix().split("/"))


def _under(segs: tuple[str, ...], base: tuple[str, ...]) -> bool:
    return segs[:len(base)] == base


def _in_suite(subject: Subject, segs: tuple[str, ...], reach: Reach) -> bool:
    return (any(_claims(g, segs) for g in reach.globs) or any(_under(segs, r) for r in reach.suites)
            or segs in reach.links)


def _map_one(rel: str, subjects: list[Subject], reach: dict[str, Reach], shared: list[str], impact: Impact) -> None:
    segs = tuple(rel.split("/"))
    is_shared = any(_claims(g, segs) for g in shared)
    suites = [s.id for s in subjects if _in_suite(s, segs, reach[s.id])]
    # a skill whose own suite holds this file gets nothing: it is eval, and skill_sha leaves it out
    skills = [s for s in _skill_owners(subjects, reach, segs) if not _in_suite(s, segs, reach[s.id])]
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
    """A skill whose resolved directory lies inside another skill's, but whose declared path does not, is refused: the
    outer skill's hash covers it, and a change to it could not be attributed to one owner. Skills that alias one
    directory are both owners, so they are allowed."""
    real = {s.id: _real(root / s.path) for s in subjects if s.kind == "skill"}
    for s in subjects:
        s_real = real.get(s.id) if s.kind == "skill" else None
        if s_real is None:
            continue
        for t in subjects:
            t_real = real.get(t.id) if t.kind == "skill" and t is not s else None
            if t_real is None or t_real == s_real:
                continue
            if s_real.is_relative_to(t_real) and not _within(s.path, t.path):
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


def _listing(base: Path) -> list[tuple[str, bool, bool]]:
    """(name, is_dir, is_symlink) of each entry of ``base`` that can be read; an unreadable one is left out."""
    out: list[tuple[str, bool, bool]] = []
    try:
        with os.scandir(base) as it:
            for entry in it:
                try:
                    out.append((entry.name, entry.is_dir(), entry.is_symlink()))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _glob_walk(base: Path, segs: tuple[str, ...], i: int, found: set[Path]) -> None:
    if i == len(segs):
        found.add(base)
        return
    seg = segs[i]
    if seg == "**":
        _glob_walk(base, segs, i + 1, found)
        for name, is_dir, is_link in _listing(base):
            if is_dir:  # follows symlinks: a linked directory is matched, but not descended
                _glob_walk(base / name, segs, i + 1 if is_link else i, found)
        return
    wild = any(c in seg for c in "*?[")  # matched by exact name against the listing: case-sensitive everywhere
    for name, _, _ in _listing(base):
        if fnmatch.fnmatchcase(name, seg) if wild else name == seg:
            _glob_walk(base / name, segs, i + 1, found)


def _glob_paths(root: Path, pattern: str) -> list[Path]:
    """The paths ``pattern`` matches under ``root``. Every segment, literal ones included, is matched against the names
    in the directory listing, case-sensitively on every platform: ``Path.glob`` folds case on Windows, and 3.11 has no
    ``case_sensitive`` switch, so a suite would differ between OSes and from impact. ``**`` is a whole segment matching
    zero or more directories. It does not recurse through a symlinked directory: the link counts as one directory for the
    segments after it, so ``**/x`` reaches ``link/x`` but not ``link/sub/x``; a second ``**`` placed after a link can still
    descend it, so ``**/**/x`` is not guaranteed to match as ``**/x`` does. Unlike ``Path.glob`` on some versions, a
    linked directory is matched by ``**`` itself on every version. A directory that cannot be listed contributes no
    matches."""
    found: set[Path] = set()
    _glob_walk(root, tuple(pattern.split("/")), 0, found)
    return sorted(found)


def _suite_files(root: Path, globs: tuple[str, ...]) -> dict[str, Path]:
    """The suite's matches by repository-relative path. A match under another match is left out: that match's walk
    already covers it, so ``{skill}/evals/**`` hashes the same whether ``**`` yields files too (3.13+) or not (3.11/3.12)."""
    found: dict[str, Path] = {}
    for pattern in globs:
        for match in _glob_paths(root, pattern):
            found.setdefault(match.relative_to(root).as_posix(), match)
    return {rel: path for rel, path in found.items() if not any(other != rel and _within(rel, other) for other in found)}


def _digest_files(files: dict[str, Path]) -> str:
    """One digest over the suite's files. A directory is walked file by file; a symlink counts by its target text
    (as tree_digest does) and by what it points to, so retargeting a link moves the hash even between identical trees."""
    h = hashlib.sha256()
    for rel in sorted(files):
        for name, path in _walk(files[rel], rel):
            if path.is_symlink():
                h.update(os.fsencode(f"{name}\0-> {os.readlink(path)}\n"))  # names may hold undecodable bytes
            if path.is_dir():
                continue  # a linked directory: its files are walked and hashed one by one
            target = path.resolve() if path.exists() else path
            h.update(os.fsencode(f"{name}\0{tree_digest(target)}\n"))
    return "sha256:" + h.hexdigest()


def _real(path: Path) -> Path | None:
    """The resolved path, or None when it cannot be resolved: a symlink loop (3.11 and 3.12 raise RuntimeError for it)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _walk(path: Path, rel: str, *, dirs: bool = False) -> list[tuple[str, Path]]:
    """Entries under ``path`` (or ``path`` itself): regular files and symlinks, and with ``dirs`` the directories
    too, so a deleted file under a directory that still exists maps to it. Symlinks are followed into their
    directories; a directory is visited once by its resolved path, so a cycle ends."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def visit(entry: Path, name: str) -> None:
        if entry.is_symlink():
            found.append((name, entry))
        if entry.is_dir():
            if dirs:
                found.append((name, entry))
            real = _real(entry)
            if real is None or real in seen:
                return
            seen.add(real)
            for child in sorted(entry.iterdir()):
                visit(child, f"{name}/{child.name}")
        elif entry.is_file() and not entry.is_symlink():
            found.append((name, entry))

    visit(path, rel)
    return sorted(found)


def _within(rel: str, base: str) -> bool:
    """True when ``rel`` is ``base`` or sits under it (repository-relative paths)."""
    return rel == base or rel.startswith(base + "/")


def _check_suite(subject: Subject, root: Path) -> None:
    """A skill's suite claims whole directories of its skill, never single files of it (SKILL.md, scripts, references),
    and no link in it reaches into its skill or to an ancestor of it. Declared paths and resolved links are both checked."""
    if subject.kind != "skill":
        return
    matches = _suite_files(root, subject.suite_globs)
    real_skill = _real(root / subject.path)
    if real_skill is None:
        raise ConfigError(f"subject {subject.id}: {subject.path} is a link that does not resolve")
    for rel, path in matches.items():
        _check_match(subject, rel, path, real_skill)
    _check_claims(subject, matches, real_skill)


def _into_skill(path: Path, real_skill: Path) -> bool:
    real = _real(path)
    return real is not None and real.is_relative_to(real_skill)


def _check_match(subject: Subject, rel: str, path: Path, real_skill: Path) -> None:
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

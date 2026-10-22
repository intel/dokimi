# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skills of the ``repo run`` arms: what step 5 of workspace preparation installs (contract §3).

The arm grammar is :class:`dokimi.repos.workspace.Arm`; a guidance part may be joined with skill parts:

- ``skill:<path>``: one Claude Code skill directory (it holds ``SKILL.md``),
  installed at ``.claude/skills/<dir name>/``;
- ``skill-catalog:<dir>``: every subdirectory of ``<dir>`` with a ``SKILL.md``,
  each installed the same way; the agent has to pick the one the task needs.

A relative skill path is relative to the manifest's ``eval_dir`` (the exported
``{eval_dir}`` on the host) and must stay inside it; an absolute path is a host
path. The directory name of a skill is its name, and the name in its ``SKILL.md``
frontmatter has to be the same.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from dokimi import process

SKILL_KINDS = ("skill", "skill-catalog")
SKILLS_DIR = ".claude/skills"


def slug(text: str) -> str:
    """``text`` as a file-system-safe name: the result directory of a cell, the cell name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "x"


class ArmError(ValueError):
    """An arm that does not parse, or whose skills cannot be found."""


@dataclass(frozen=True)
class Skill:
    """One skill to install: its directory name (the name Claude Code knows it by) and host directory."""

    name: str
    dir: Path

    @property
    def target(self) -> str:
        return f"{SKILLS_DIR}/{self.name}"


def resolve_skills(arm: Any, content_dir: Path) -> list[Skill]:
    """The skills ``arm`` (a :class:`.workspace.Arm`) installs, found on the host; two with one name are refused."""
    found: list[Skill] = []
    for kind, path in arm.skills:
        root = _skill_path(path, content_dir)
        if PurePosixPath(path).name != root.name:  # the arm is named from the written leaf, the install from root
            raise ArmError(f"arm {arm.spec!r}: {path} resolves to a directory named {root.name!r}, not "
                           f"{PurePosixPath(path).name!r}; point at the directory itself, as its name names the arm")
        if kind == "skill":
            if not (root / "SKILL.md").is_file():
                raise ArmError(f"arm {arm.spec!r}: no SKILL.md in {path}")
            found.append(_named(arm, root, path))
            continue
        if not root.is_dir():
            raise ArmError(f"arm {arm.spec!r}: skill-catalog {path} is not a directory")
        subs = []
        for d in sorted(root.iterdir()):
            if not d.is_dir() or not (d / "SKILL.md").is_file():
                continue
            if not d.resolve().is_relative_to(root.resolve()):
                raise ArmError(f"arm {arm.spec!r}: {d.name} in {path} links outside it")
            subs.append(_named(arm, d, path))
        if not subs:
            raise ArmError(f"arm {arm.spec!r}: no subdirectory of {path} holds a SKILL.md")
        found += subs
    for skill in found:
        bad = _uncopyable(skill.dir)
        if bad:
            path, why = bad
            raise ArmError(f"arm {arm.spec!r}: {path.relative_to(skill.dir)} in skill {skill.name} {why}")
    names = [s.name for s in found]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ArmError(f"arm {arm.spec!r}: more than one skill named {', '.join(dupes)}")
    return found


def _named(arm: Any, directory: Path, written: str) -> Skill:
    """The skill in ``directory``; its ``SKILL.md`` ``name`` (when it has one) has to be the directory's name,
    as it is the name the agent's ``Skill`` call carries and the one the install path and usage match on."""
    declared = _frontmatter_name(directory / "SKILL.md")
    if declared is not None and declared != directory.name:
        raise ArmError(f"arm {arm.spec!r}: the SKILL.md in {written} is named {declared!r}, but its directory "
                       f"is {directory.name!r}; rename one, as the agent calls the skill by its name")
    return Skill(directory.name, directory)


def _frontmatter_name(skill_md: Path) -> str | None:
    """Top-level ``name`` in the YAML frontmatter of ``skill_md``, ``None`` without one (or unreadable YAML)."""
    lines = skill_md.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    block: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block.append(line)
    try:
        meta = yaml.safe_load("\n".join(block))
    except yaml.YAMLError:
        return None
    name = meta.get("name") if isinstance(meta, dict) else None
    return str(name).strip() or None if name is not None else None


def _uncopyable(skill_dir: Path) -> tuple[Path, str] | None:
    """What under ``skill_dir`` the copy into the workspace cannot take, with the reason: a link out of the
    skill (the copy would take the host's bytes), a dangling or looping link, a link to a directory (the copy
    would recurse), or a file that is not regular (a FIFO would block the copy)."""
    root = skill_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        for name in dirnames + filenames:
            path = Path(dirpath, name)
            if path.is_symlink():
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    return path, "is a dangling or looping link"
                if not target.is_relative_to(root):
                    return path, "links outside it"
                if target.is_dir():
                    return path, "links to a directory"
            elif not path.is_file() and not path.is_dir():
                return path, "is not a regular file"
    return None


def _skill_path(path: str, content_dir: Path) -> Path:
    if Path(path).is_absolute():
        return Path(path).resolve()
    root = content_dir.resolve()
    full = (root / PurePosixPath(path)).resolve()
    if not full.is_relative_to(root):
        raise ArmError(f"skill path {path} leaves eval_dir")
    return full


def _base_skill_files(git_dir: Path, base: str) -> list[str]:
    """The files the commit ``base`` tracks under ``.claude/skills`` (one ``git ls-tree`` per base)."""
    proc = process.run(["git", "-C", str(git_dir), "ls-tree", "-r", "-z", "--name-only", base, "--", ".claude/skills"],
                       env=process.GIT_ENV, timeout=60, capture_limit=None)
    return [p for p in proc.stdout.split("\0") if p] if proc.returncode == 0 else []


def check_arms(arms: list[Any], content_dir: Path, tasks: list[Any], git_dir: Path | None = None,
               guidance_globs: tuple[str, ...] | list[str] = ()) -> None:
    """Find the skills of every parsed arm before anything runs; a catalog must hold each task's
    ``expected_skill``. Two arms that would share a name or a result directory are refused, and so is a skill
    the base of a task (in ``git_dir``) already ships, which preparing the workspace would refuse cell by cell.
    As preparation does, a ``guidance:none`` or pinned-revision arm first drops the base's files that match
    ``guidance_globs``: only what is left under the skill's directory counts as shipped. A revision arm then
    writes back its revision's files that match them, so those count as shipped too. The files of a ``patch:``
    arm's patch and of a task's ``setup_patches`` are not read: a skill directory they add is refused only in
    preparation."""
    from dokimi.repos.manifest import match_glob

    cells: dict[str, str] = {}  # cell-name slug of an arm -> the arm spec that owns it
    shipped: dict[str, list[str]] = {}  # task base -> tracked files under .claude/skills
    for arm in arms:
        cell = slug(str(arm).replace(":", "-"))
        if cells.setdefault(cell, arm.spec) != arm.spec:
            raise ArmError(f"arms {cells[cell]!r} and {arm.spec!r} would share one result directory ({cell})")
        skills = resolve_skills(arm, content_dir)
        names = {s.name for s in skills}
        drops = arm.patch is None and (arm.base == "guidance:none" or arm.rev is not None)
        rev = (arm.rev_sha or arm.rev) if arm.patch is None else None
        for task in tasks if skills else ():
            if git_dir is None:
                break
            for commit in (task.base, rev):
                if commit is not None and commit not in shipped:
                    shipped[commit] = _base_skill_files(git_dir, commit)
            left = [p for p in shipped[task.base]
                    if not (drops and any(match_glob(g, p) for g in guidance_globs))]
            written = [p for p in shipped[rev] if any(match_glob(g, p) for g in guidance_globs)] if rev else []
            for skill in skills:
                for where, files in ((f"the base of task {task.id}", left), (f"guidance revision {rev}", written)):
                    if any(p.startswith(skill.target.rstrip("/") + "/") or p == skill.target for p in files):
                        raise ArmError(f"arm {arm.spec!r}: {where} already has {skill.target}")
        if any(kind == "skill-catalog" for kind, _ in arm.skills):
            for task in tasks:
                expected = task.raw.get("expected_skill")
                if expected and expected not in names:
                    raise ArmError(f"arm {arm.spec!r}: task {task.id}'s expected_skill {expected!r} is not installed "
                                   f"(installed: {', '.join(sorted(names))})")


def tree_sha256(root: Path) -> str:
    """sha256 over the relative paths and bytes of the files under ``root``."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def skill_unmeasured(installed: list[str], expected: str | None) -> dict[str, Any]:
    """The usage of a cell whose trace could not be read: unknown, not "did not use the skill" (``None``)."""
    return {"measured": False, "skills_installed": installed, "skills_used": [], "expected_skill": expected,
            "skill_invoked": None, "skill_selected_correct": None}


def skill_usage(events: list[dict[str, Any]], installed: list[str], expected: str | None) -> dict[str, Any]:
    """Which installed skills the agent used: a ``Skill`` tool call naming it, or a read of its ``SKILL.md``.

    ``skill_invoked``: the expected skill was used (any installed one when the task names none);
    ``skill_selected_correct``: the expected skill was the first one used, ``None`` without one.
    """
    used: list[str] = []
    for e in events:
        name = None
        if e.get("kind") == "tool" and e.get("name") == "Skill":
            name = str(e.get("skill") or "").split(":")[-1]  # a plugin skill is plugin:name
        elif e.get("kind") == "read":
            parts = PurePosixPath(str(e.get("path") or "")).parts
            if len(parts) == 4 and "/".join(parts[:2]) == SKILLS_DIR and parts[3] == "SKILL.md":
                name = parts[2]
        if name in installed and name not in used:
            used.append(name)
    out: dict[str, Any] = {"measured": True, "skills_installed": installed, "skills_used": used,
                           "expected_skill": expected,
                           "skill_invoked": (expected in used) if expected else bool(used),
                           "skill_selected_correct": (used[:1] == [expected]) if expected else None}
    return out

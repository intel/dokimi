# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arms of ``repo run``: what step 5 of workspace preparation does to the workspace (contract §3).

An arm is one or more parts joined with ``+``, as in the Q&A ``--arms``
(:mod:`dokimi.treatments.specs`):

- ``guidance:none`` | ``guidance:raw`` | ``guidance:bridge``: the repository's
  own guidance files, removed, kept or bridged. At most one; ``guidance:raw``
  when the arm names none;
- ``skill:<path>``: one Claude Code skill directory (it holds ``SKILL.md``),
  installed at ``.claude/skills/<dir name>/``;
- ``skill-catalog:<dir>``: every subdirectory of ``<dir>`` with a ``SKILL.md``,
  each installed the same way; the agent has to pick the one the task needs.

A relative skill path is relative to the manifest's ``eval_dir`` (the exported
``{eval_dir}`` on the host) and must stay inside it; an absolute path is a host
path. The arm string is recorded as it was written: ``guidance:raw+skill:x`` and
``skill:x`` are two arms with the same workspace.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

GUIDANCE = ("guidance:none", "guidance:raw", "guidance:bridge")
SKILL_KINDS = ("skill", "skill-catalog")
SKILLS_DIR = ".claude/skills"
COMBINE = "+"


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


@dataclass(frozen=True)
class RepoArm:
    spec: str
    guidance: str
    skills: tuple[tuple[str, str], ...] = ()  # (kind, path) as written

    @property
    def catalog(self) -> bool:
        return any(kind == "skill-catalog" for kind, _ in self.skills)


def parse_arm(spec: str) -> RepoArm:
    """``spec`` as a :class:`RepoArm`. A ``+`` starts a new part only before a known kind, so a path may hold one."""
    parts: list[str] = []
    for piece in spec.split(COMBINE):
        if parts and not _starts_kind(piece):
            parts[-1] += COMBINE + piece
        else:
            parts.append(piece)
    guidance, skills = [], []
    for part in parts:
        if part in GUIDANCE:
            guidance.append(part)
            continue
        kind, sep, path = part.partition(":")
        if kind not in SKILL_KINDS or not sep:
            raise ArmError(f"unknown arm {spec!r} (known: {', '.join(GUIDANCE)}, skill:<path>, "
                           f"skill-catalog:<dir>, joined with {COMBINE!r})")
        if not path:
            raise ArmError(f"arm {spec!r}: {kind}: needs a path")
        skills.append((kind, path))
    if len(guidance) > 1:
        raise ArmError(f"arm {spec!r}: more than one guidance part")
    return RepoArm(spec, guidance[0] if guidance else "guidance:raw", tuple(skills))


def _starts_kind(text: str) -> bool:
    return text in GUIDANCE or any(text.startswith(k + ":") for k in SKILL_KINDS)


def resolve_skills(arm: RepoArm, content_dir: Path) -> list[Skill]:
    """The skills ``arm`` installs, found on the host; two with one name are refused."""
    found: list[Skill] = []
    for kind, path in arm.skills:
        root = _skill_path(path, content_dir)
        if kind == "skill":
            if not (root / "SKILL.md").is_file():
                raise ArmError(f"arm {arm.spec!r}: no SKILL.md in {path}")
            found.append(Skill(root.name, root))
            continue
        if not root.is_dir():
            raise ArmError(f"arm {arm.spec!r}: skill-catalog {path} is not a directory")
        subs = []
        for d in sorted(root.iterdir()):
            if not d.is_dir() or not (d / "SKILL.md").is_file():
                continue
            if not d.resolve().is_relative_to(root.resolve()):
                raise ArmError(f"arm {arm.spec!r}: {d.name} in {path} links outside it")
            subs.append(Skill(d.name, d))
        if not subs:
            raise ArmError(f"arm {arm.spec!r}: no subdirectory of {path} holds a SKILL.md")
        found += subs
    for skill in found:
        link = _escaping_link(skill.dir)
        if link:
            raise ArmError(f"arm {arm.spec!r}: {link.relative_to(skill.dir)} in skill {skill.name} links outside it")
    names = [s.name for s in found]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ArmError(f"arm {arm.spec!r}: more than one skill named {', '.join(dupes)}")
    return found


def _escaping_link(skill_dir: Path) -> Path | None:
    """A link under ``skill_dir`` whose target is outside it: copying would take the host's bytes."""
    root = skill_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        for name in dirnames + filenames:
            link = Path(dirpath, name)
            if link.is_symlink() and not link.resolve().is_relative_to(root):
                return link
    return None


def _skill_path(path: str, content_dir: Path) -> Path:
    if Path(path).is_absolute():
        return Path(path).resolve()
    root = content_dir.resolve()
    full = (root / PurePosixPath(path)).resolve()
    if not full.is_relative_to(root):
        raise ArmError(f"skill path {path} leaves eval_dir")
    return full


def check_arms(arms: list[str], content_dir: Path, tasks: list[Any]) -> list[RepoArm]:
    """Parse every arm and find its skills before anything runs; a catalog must hold each task's
    ``expected_skill``. Returns the parsed arms."""
    from .runner import slug  # runner imports this module

    parsed = []
    cells: dict[str, str] = {}  # cell-name slug of an arm -> the arm string that owns it
    for spec in arms:
        arm = parse_arm(spec)
        cell = slug(spec.replace(":", "-"))
        if cells.setdefault(cell, spec) != spec:
            raise ArmError(f"arms {cells[cell]!r} and {spec!r} would share one result directory ({cell})")
        names = {s.name for s in resolve_skills(arm, content_dir)}
        if arm.catalog:
            for task in tasks:
                expected = task.raw.get("expected_skill")
                if expected and expected not in names:
                    raise ArmError(f"arm {spec!r}: task {task.id}'s expected_skill {expected!r} is not installed "
                                   f"(installed: {', '.join(sorted(names))})")
        parsed.append(arm)
    return parsed


def tree_sha256(root: Path) -> str:
    """sha256 over the relative paths and bytes of the files under ``root``."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


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
    out: dict[str, Any] = {"skills_installed": installed, "skills_used": used, "expected_skill": expected,
                           "skill_invoked": (expected in used) if expected else bool(used),
                           "skill_selected_correct": (used[:1] == [expected]) if expected else None}
    return out

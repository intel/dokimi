# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``guidance:format=<fmt>`` and ``guidance:as-skill`` arms (workspace step 5, :mod:`.workspace`): the guidance
files rendered in place, or removed and packaged as one skill."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .arms import SKILLS_DIR, guidance_skill_name, tree_sha256
from .errors import PrepError
from .guidance_format import FormatError, to_format
from .manifest import RepoManifest

#: The description of the skill ``guidance:as-skill`` packages the guidance into: the same for every repository.
GUIDANCE_SKILL_DESCRIPTION = ("Repository guidance for working in this codebase, its conventions, build and test "
                              "commands and rules. Load it before changing its code.")  # no ": " (YAML plain scalar)


def _apply_format(ws: Path, matched: list[str], fmt: str) -> tuple[list[str], dict[str, Any]]:
    """Each guidance file rewritten in ``fmt`` in place. A symlink is left alone: its target is rendered
    once if it is a guidance file itself, and ``meta.json`` names the links that keep their raw bytes."""
    if not matched:
        raise PrepError(f"guidance:format={fmt}: the base has no guidance file to render")
    written, links = [], []
    for rel in matched:
        if (ws / rel).is_symlink():
            _link_file(ws, rel, matched)  # the link must reach a file that is rendered, not raw guidance
            links.append(rel)
            continue
        try:
            rendered = to_format(_guidance_text(ws, rel, f"guidance:format={fmt}"), fmt)
        except FormatError as exc:
            raise PrepError(f"guidance:format={fmt}: {rel}: {exc}") from exc
        (ws / rel).write_bytes(rendered.encode("utf-8"))  # no newline translation: the restore check holds on disk
        written.append(rel)
    return written, ({"guidance_links_kept": links} if links else {})


def _link_file(ws: Path, rel: str, matched: list[str]) -> None:
    """The regular file a guidance link of the workspace reaches, resolved as the filesystem resolves it (a linked
    directory on the way counts). PrepError when the link dangles or cycles, or reaches a file outside the workspace
    or outside the matched guidance files: that file would be dropped or left unrendered."""
    try:
        target = (ws / rel).resolve(strict=True)
    except (OSError, RuntimeError) as exc:  # RuntimeError: a symlink loop, before Python 3.13
        raise PrepError(f"guidance link {rel} does not reach a file (a dangling link or a cycle): {exc}") from exc
    root = ws.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise PrepError(f"guidance link {rel} reaches {target}, which is outside the workspace or not a regular file")
    reached = target.relative_to(root).as_posix()
    if reached not in matched:
        raise PrepError(f"guidance link {rel} reaches {reached}, which is not a guidance file")


def _guidance_text(ws: Path, rel: str, arm: str) -> str:
    """The text of a guidance file of the workspace; one that is not UTF-8 is a prep error of the cell."""
    try:
        return (ws / rel).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrepError(f"{arm}: {rel} is not UTF-8: {exc}") from exc


def _apply_as_skill(ws: Path, manifest: RepoManifest, matched: list[str]) -> tuple[list[str], dict[str, Any]]:
    """The guidance files removed (as ``guidance:none`` removes them) and their text packaged as one skill,
    ``.claude/skills/<repo>-guidance/SKILL.md``: each file under a ``## File: <path>`` line, its text verbatim."""
    if not matched:
        raise PrepError("guidance:as-skill: the base has no guidance file to package")
    name = guidance_skill_name(manifest.id)
    skill_dir = ws / SKILLS_DIR / name
    for part in (ws / ".claude", ws / SKILLS_DIR):  # a repository link would write to the host
        if part.is_symlink():
            raise PrepError(f"guidance:as-skill: {part.relative_to(ws)} is a symlink in the repository")
    if skill_dir.exists() or skill_dir.is_symlink():
        raise PrepError(f"guidance:as-skill: the workspace already has {SKILLS_DIR}/{name}")
    parts = []
    for rel in matched:
        if (ws / rel).is_symlink():  # packaged once under its target's name; a link to anything else would be lost
            _link_file(ws, rel, matched)
            continue
        text = _guidance_text(ws, rel, "guidance:as-skill")
        parts.append(f"## File: {rel}\n\n" + (text if text.endswith("\n") else text + "\n"))
    for rel in matched:
        (ws / rel).unlink()
    skill_dir.mkdir(parents=True)
    frontmatter = f"---\nname: {name}\ndescription: {GUIDANCE_SKILL_DESCRIPTION}\n---\n\n"
    (skill_dir / "SKILL.md").write_bytes((frontmatter + "\n".join(parts)).encode("utf-8"))  # verbatim: no newline translation
    return matched, {"guidance_skill": {"name": name, "path": f"{SKILLS_DIR}/{name}",
                                        "sha256": tree_sha256(skill_dir)}}

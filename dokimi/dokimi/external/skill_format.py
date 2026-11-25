# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which skill format a ``SKILL.md`` is written for, and the rules its checks follow.

Several hosts read a ``SKILL.md`` but do not share one spec. The Agent Skills spec
(https://agentskills.io/specification) makes ``name`` and ``description`` required, ties ``name``
to the directory, and caps both lengths. OpenCode reads the same file under
``.opencode/skills`` with looser rules (https://opencode.ai/docs/skills/, fetched 2026-11-16):
all frontmatter is optional at runtime, the ID comes from the path, ``name`` is a display label
that need not match the directory, and no description length is documented. A skill with no
description is loaded but "not advertised", so the model never picks it. OpenAI Codex requires
``name`` and ``description`` and scans ``.agents/skills`` (https://learn.chatgpt.com/docs/build-skills,
redirected from developers.openai.com/codex/skills, fetched 2026-11-16).

Formats:

- ``agent-skill``: the Agent Skills spec, for the hosts that follow it (the default, and any
  ``SKILL.md`` with no host directory in its path, such as ``skills/<name>/SKILL.md``);
- ``opencode``: OpenCode's rules above. The corpus also holds ``.opencode/skill/`` (singular), which
  the docs do not list; it is read as OpenCode;
- ``unsupported``: a skills directory under one of the hosts in :data:`UNSUPPORTED_HOST_DIRS`, which
  dokimi holds no spec for. Its spec checks are not run and one ``unsupported-skill-format`` note says
  so, instead of an error the host may not raise. Safety and content checks still run.

The host is read from the path relative to the scanned root, and only a directory named in one of the
sets below is a host. Any other hidden directory, such as ``~/.cache`` or ``~/.config`` above a
``skills`` root, is not, so its skills get the spec checks. A skill reviewed alone is read
by its absolute path, since no root stands above it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from .findings import Finding

FORMAT_AGENT_SKILL = "agent-skill"
FORMAT_OPENCODE = "opencode"
FORMAT_UNSUPPORTED = "unsupported"

# Dot-directories whose ``skills/`` folder follows the Agent Skills spec (see the module doc).
SPEC_HOST_DIRS = frozenset({".claude", ".codex", ".gemini", ".github", ".agents"})
# Dot-directories whose ``skills/`` folder dokimi holds no spec for: the hosts seen in the corpus with a
# skills folder. A host not listed here gets the spec checks, as any hidden directory does.
UNSUPPORTED_HOST_DIRS = frozenset({
    ".amp", ".augment", ".cline", ".clinerules", ".continue", ".cursor", ".goose", ".kiro", ".qwen", ".roo",
    ".trae", ".windsurf",
})
OPENCODE_DIR = ".opencode"
CONFIG_DIR = ".config"  # OpenCode's global skills directory is ~/.config/opencode/skills
GLOBAL_OPENCODE_DIR = "opencode"
HOST_DIRS = frozenset({OPENCODE_DIR}) | UNSUPPORTED_HOST_DIRS
SKILL_DIR_NAMES = ("skill", "skills")

UNSUPPORTED_CODE = "unsupported-skill-format"
OPENCODE_NO_DESCRIPTION_CODE = "opencode-no-description"
OPENCODE_NO_DESCRIPTION_MESSAGE = (
    "OpenCode loads a SKILL.md without a description, but does not advertise it to the model, so it is "
    "never picked; dokimi reads only a skill with a description, so it is not evaluated. Add a "
    "description to the frontmatter (https://opencode.ai/docs/skills/)")
UNSUPPORTED_MESSAGE = (
    "no spec is known for this host's skills directory, so the frontmatter and description checks were "
    "not run; the safety and content checks still ran")
UNLOADED_UNSUPPORTED_MESSAGE = (
    "no spec is known for this host's skills directory, and this SKILL.md did not load, so only its "
    "commands and its Bash grant were checked")


def _parts(path: Path, root: Optional[Path]) -> Sequence[str]:
    """The directory parts of ``path`` (its file name excluded): relative to ``root`` when it lies under it."""
    if root is not None:
        try:
            return path.relative_to(root).parts[:-1]
        except ValueError:
            pass
    return path.parts[:-1]


def _host_root(skill_md: Path, root: Optional[Path]) -> Optional[Path]:
    """The root to read the host from: ``root`` moved up to the directory above the host when the scan started inside it."""
    if root is None or root.resolve() == skill_md.parent.resolve():
        return None
    first = next(iter(_parts(skill_md, root)), "")
    if root.name in SKILL_DIR_NAMES:
        # A scan of a skills directory itself (.opencode/skills): the host directory above it is the format's marker.
        root = root.parent.parent
    elif root.name in HOST_DIRS and first in SKILL_DIR_NAMES:
        root = root.parent  # a scan of a host directory itself (.opencode, .kiro): the host is the root's own name
    elif root.name == GLOBAL_OPENCODE_DIR and root.parent.name == CONFIG_DIR and first in SKILL_DIR_NAMES:
        root = root.parent.parent  # a scan of ~/.config/opencode itself
    if root.name == CONFIG_DIR and tuple(_parts(skill_md, root)[:1]) == (GLOBAL_OPENCODE_DIR,):
        root = root.parent  # a scan of ~/.config, or of ~/.config/opencode/skills: keep ``.config`` as the first part
    return root


def skill_format(skill_md: Path, root: Optional[Path] = None) -> str:
    """The format of the ``SKILL.md`` at ``skill_md``, read from its directories below ``root``.

    ``root`` is None, or the skill's own directory (a skill reviewed alone): then the whole path is read.
    """
    # Both paths absolute and normalised, so the verdict does not depend on how the root was spelled
    # (`--root skills`, a full path, or one with `..`).
    skill_md = Path(os.path.normpath(skill_md.absolute()))
    root = None if root is None else Path(os.path.normpath(Path(root).absolute()))
    root = _host_root(skill_md, root)
    dirs = _parts(skill_md, root)
    for i, part in enumerate(dirs):
        if i + 1 < len(dirs) and dirs[i + 1] in SKILL_DIR_NAMES and (
                part == OPENCODE_DIR or (part == GLOBAL_OPENCODE_DIR and i > 0 and dirs[i - 1] == CONFIG_DIR)):
            return FORMAT_OPENCODE
    for i, part in enumerate(dirs):
        if part in UNSUPPORTED_HOST_DIRS and i + 1 < len(dirs) and dirs[i + 1] in SKILL_DIR_NAMES:
            return FORMAT_UNSUPPORTED
    return FORMAT_AGENT_SKILL


def format_findings(fmt: str, meta: Optional[dict] = None, *, loaded: bool = True) -> list[Finding]:
    """The findings for a SKILL.md that the frontmatter rules do not cover, under ``fmt``.

    ``meta`` is None for a file with no frontmatter. A host with no known spec gets one note, whatever
    its frontmatter. OpenCode loads a skill with no frontmatter, or with no description, but does not
    advertise it, so that is a warning. Any other format gets no finding here: the spec applies.
    """
    if fmt == FORMAT_UNSUPPORTED:
        # An unloaded file was never read by the content checks, so its note must not say they ran.
        return [Finding("note", UNSUPPORTED_CODE, UNSUPPORTED_MESSAGE if loaded else UNLOADED_UNSUPPORTED_MESSAGE)]
    if fmt == FORMAT_OPENCODE and (meta is None or not str(meta.get("description") or "").strip()):
        return [Finding("warn", OPENCODE_NO_DESCRIPTION_CODE, OPENCODE_NO_DESCRIPTION_MESSAGE)]
    return []


def opencode_hidden(meta: dict, disabled: bool) -> bool:
    """Whether an OpenCode skill is kept from the model: ``metadata: {opencode/autoinvoke: ...}`` wins over
    ``disable-model-invocation`` (``disabled``, already read) when it is set to a boolean."""
    extra = meta.get("metadata")
    value = str(extra.get("opencode/autoinvoke", "")).strip().lower() if isinstance(extra, dict) else ""
    return True if value in ("false", "0", "no", "off") else False if value in ("true", "1", "yes", "on") else disabled


def not_a_failed_load(codes: Sequence[str]) -> bool:
    """Whether an unloaded skill's review makes it a warning or a note, not a failed load: an OpenCode skill
    loaded as plain text but not advertised, or a host with no known spec (one ``unsupported`` note)."""
    return OPENCODE_NO_DESCRIPTION_CODE in codes or UNSUPPORTED_CODE in codes


NOT_CHECKED_CODES = ("description-when-not-checked", UNSUPPORTED_CODE)  # the notes that say a skill was not read


def not_checked(row: dict) -> bool:
    """Whether an ``ok`` row is only ok because the check did not read it (a ``NOT_CHECKED_CODES`` note)."""
    return row["state"] == "ok" and any(f.get("code") in NOT_CHECKED_CODES for f in row["findings"])

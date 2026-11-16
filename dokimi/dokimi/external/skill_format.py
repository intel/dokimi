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
- ``unsupported``: a ``.<tool>/skills/`` directory dokimi holds no spec for. Its spec checks are not
  run and one ``unsupported-skill-format`` note says so, instead of an error the host may not raise.
  Safety and content checks still run.

The host is read from the path relative to the scanned root, so a checkout that lives under a
hidden directory such as ``~/.cache`` is not mistaken for a host. A skill reviewed alone is read
by its absolute path, since no root stands above it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from .findings import Finding

FORMAT_AGENT_SKILL = "agent-skill"
FORMAT_OPENCODE = "opencode"
FORMAT_UNSUPPORTED = "unsupported"

# Dot-directories whose ``skills/`` folder follows the Agent Skills spec (see the module doc).
SPEC_HOST_DIRS = frozenset({".claude", ".codex", ".gemini", ".github", ".agents"})
OPENCODE_DIR = ".opencode"
SKILL_DIR_NAMES = ("skill", "skills")

UNSUPPORTED_CODE = "unsupported-skill-format"
OPENCODE_NO_DESCRIPTION_CODE = "opencode-no-description"
OPENCODE_NO_DESCRIPTION_MESSAGE = (
    "OpenCode loads a SKILL.md without a description, but does not advertise it to the model, so it is "
    "never picked; dokimi reads only a skill with a description, so it is not evaluated. Add a "
    "description to the frontmatter (https://opencode.ai/docs/skills/)")
UNSUPPORTED_MESSAGE = (
    "no spec is known for this host's skills directory, so the frontmatter and description checks were "
    "not run; the safety and content checks were")


def _parts(path: Path, root: Optional[Path]) -> Sequence[str]:
    """The directory parts of ``path`` (its file name excluded): relative to ``root`` when it lies under it."""
    if root is not None:
        try:
            return path.relative_to(root).parts[:-1]
        except ValueError:
            pass
    return path.parts[:-1]


def skill_format(skill_md: Path, root: Optional[Path] = None) -> str:
    """The format of the ``SKILL.md`` at ``skill_md``, read from its directories below ``root``.

    ``root`` is None, or the skill's own directory (a skill reviewed alone): then the whole path is read.
    """
    # Both paths absolute, so the verdict does not depend on how the root was spelled (`--root skills` or a full path).
    skill_md = skill_md.absolute()
    if root is not None:
        root = Path(root).absolute()
    if root is not None and root.resolve() == skill_md.parent.resolve():
        root = None
    if root is not None and Path(root).name in SKILL_DIR_NAMES:
        # A scan of a skills directory itself (.opencode/skills): the host directory above it is the format's marker.
        root = Path(root).parent.parent
    elif root is not None and Path(root).name == OPENCODE_DIR and \
            next(iter(_parts(skill_md, root)), "") in SKILL_DIR_NAMES:
        # A scan of the OpenCode host directory itself (.opencode): the host is the root's own name, read from its parent.
        root = Path(root).parent
    dirs = _parts(skill_md, root)
    for i, part in enumerate(dirs):
        if part == OPENCODE_DIR and i + 1 < len(dirs) and dirs[i + 1] in SKILL_DIR_NAMES:
            return FORMAT_OPENCODE
    for i, part in enumerate(dirs):
        if part.startswith(".") and part not in SPEC_HOST_DIRS and i + 1 < len(dirs) \
                and dirs[i + 1] in SKILL_DIR_NAMES:
            return FORMAT_UNSUPPORTED
    return FORMAT_AGENT_SKILL


def format_findings(fmt: str, meta: Optional[dict] = None) -> list[Finding]:
    """The findings for a SKILL.md that the frontmatter rules do not cover, under ``fmt``.

    ``meta`` is None for a file with no frontmatter. A host with no known spec gets one note, whatever
    its frontmatter. OpenCode loads a skill with no frontmatter, or with no description, but does not
    advertise it, so that is a warning. Any other format gets no finding here: the spec applies.
    """
    if fmt == FORMAT_UNSUPPORTED:
        return [Finding("note", UNSUPPORTED_CODE, UNSUPPORTED_MESSAGE)]
    if fmt == FORMAT_OPENCODE and (meta is None or not str(meta.get("description") or "").strip()):
        return [Finding("warn", OPENCODE_NO_DESCRIPTION_CODE, OPENCODE_NO_DESCRIPTION_MESSAGE)]
    return []


def not_a_failed_load(codes: Sequence[str]) -> bool:
    """Whether an unloaded skill's review makes it a warning or a note, not a failed load: an OpenCode skill
    loaded as plain text but not advertised, or a host with no known spec (one ``unsupported`` note)."""
    return OPENCODE_NO_DESCRIPTION_CODE in codes or UNSUPPORTED_CODE in codes

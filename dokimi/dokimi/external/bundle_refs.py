# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``bundle-file-unreferenced``: bundled scripts and documents that ``SKILL.md`` never names.

An agent reads ``SKILL.md`` and opens the files it names; a bundled file it is not pointed at is
found only by browsing the directory. One ``warn`` per skill in :mod:`dokimi.external.validate`,
with the count and the first :data:`EXAMPLES_SHOWN` paths. In the GitSkills corpus, of 5.9M files
bundled next to a ``SKILL.md``, 63% of the ``.py``, 45% of the ``.sh`` and 47% of the ``.md`` files
are never named in it.

Only code and documents count (:data:`COUNTED_SUFFIXES`): data, images and fonts are read by the
scripts, not by the agent. A file is named when ``SKILL.md`` holds its path relative to the skill,
its file name, or the path of a folder above it named alone (``references/`` covers every file under
it; ``references/a.md`` covers only ``a.md``), case ignored; a named document is read too, so the
files it names count as named. Licence, readme and changelog files, ``AGENTS.md``, ``__init__.py``
and other ``_``-prefixed helpers, tests, hidden and vendored folders, and a nested skill's folder
are left out. A skill at the root of a scanned checkout is not
checked, since its bundle is the rest of that checkout.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from dokimi.skills.loader import VENDORED_DIR_NAMES

from .command_checks import _is_skill_dir

COUNTED_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".ps1", ".js", ".mjs", ".cjs", ".ts", ".rb", ".md", ".mdx", ".txt"}
)
# Files no agent is meant to open for the task: licence and project paperwork, package markers.
SKIPPED_STEMS = frozenset({"license", "licence", "readme", "changelog", "notice", "copying", "authors", "contributing"})
SKIPPED_NAMES = frozenset({"__init__.py", "conftest.py", "requirements.txt", "agents.md", "claude.md"})
SKIPPED_DIRS = frozenset({"tests", "test", "__tests__", "evals"})
WALK_MAX_FILES = 2000  # a skill with more files is a checkout, not a bundle: the rest are not listed
EXAMPLES_SHOWN = 3
DOC_SUFFIXES = frozenset({".md", ".mdx", ".txt"})
DOC_MAX_BYTES = 256 * 1024
DOCS_READ_MAX = 50


def _bundled(skill_dir: Path) -> list[str]:
    """The counted files under ``skill_dir``, relative and sorted, at most :data:`WALK_MAX_FILES`."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in VENDORED_DIR_NAMES and d.lower() not in SKIPPED_DIRS
            and not _is_skill_dir(here / d)
        )
        for name in sorted(filenames):
            if len(found) >= WALK_MAX_FILES:
                return found
            stem, suffix = os.path.splitext(name)
            if (
                name.startswith((".", "_"))  # hidden, or a private helper (``_common.py``) scripts import
                or suffix.lower() not in COUNTED_SUFFIXES
                or stem.lower() in SKIPPED_STEMS
                or name.lower() in SKIPPED_NAMES
                or (here == skill_dir and name == "SKILL.md")
            ):
                continue
            found.append((here / name).relative_to(skill_dir).as_posix())
    return found


def _named(rel: str, text: str, links: frozenset[str]) -> bool:
    """Whether ``text`` names the file ``rel``: its path, its file name, or a folder above it named alone.

    Case is ignored (``FORMS.md`` names ``forms.md``). ``links`` are the link destinations of
    ``SKILL.md`` as files (escapes and percent-encoding decoded), lowercased.
    """
    parts = rel.lower().split("/")
    if "/".join(parts) in links or "/".join(parts) in text:
        return True
    if re.search(r"(?<![\w.-])" + re.escape(parts[-1]) + r"(?![\w-])", text):
        return True
    # A folder named alone (``references/``), not as the start of another file's path (``scripts/a.py``).
    return any(
        re.search(r"(?<![\w./-])" + re.escape("/".join(parts[:i])) + r"/(?![\w.-])", text)
        for i in range(1, len(parts))
    )


def _read_doc(path: Path) -> str:
    """The lowercased text of a named document the agent may read on, or "" when it is not one or cannot be read."""
    if path.suffix.lower() not in DOC_SUFFIXES:
        return ""
    try:
        if path.is_symlink() or path.stat().st_size > DOC_MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8").lower()
    except (OSError, UnicodeDecodeError):
        return ""


def unreferenced_message(skill_path: Path, links: Iterable[Optional[str]], *, at_root: bool) -> Optional[str]:
    """The ``bundle-file-unreferenced`` message for the skill whose ``SKILL.md`` is ``skill_path``, or None.

    ``links``: the files its Markdown links name (None for a link that names no file). A document
    ``SKILL.md`` names is read too, and so on from there (at most :data:`DOCS_READ_MAX`): a script
    that only ``forms.md`` names is still found by an agent that follows ``SKILL.md`` to ``forms.md``.
    """
    if at_root:
        return None
    try:
        text = skill_path.read_text(encoding="utf-8").lower()
        files = _bundled(skill_path.parent)
    except (OSError, UnicodeDecodeError):
        return None
    linked = frozenset(PurePosixPath(link).as_posix().removeprefix("./").lower() for link in links if link)
    missing, docs_read = files, 0
    while True:
        named = [rel for rel in missing if _named(rel, text, linked)]
        if not named:
            break
        missing = [rel for rel in missing if rel not in set(named)]
        for rel in named:
            if docs_read < DOCS_READ_MAX and (more := _read_doc(skill_path.parent / rel)):
                docs_read += 1
                text += "\n" + more
    if not missing:
        return None
    shown = ", ".join(missing[:EXAMPLES_SHOWN]) + (", ..." if len(missing) > EXAMPLES_SHOWN else "")
    return (
        f"{len(missing)} of {len(files)} bundled scripts and documents are never named in SKILL.md "
        f"or a document it names ({shown}); the agent finds them only by browsing. Name each one "
        "where it is used, or remove it"
    )

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
import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

from dokimi.skills.loader import VENDORED_DIR_NAMES

from .command_checks import _is_skill_dir

COUNTED_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".ps1", ".js", ".mjs", ".cjs", ".ts", ".rb", ".md", ".mdx", ".txt"}
)
# Files no agent is meant to open for the task: licence and project paperwork, package markers.
SKIPPED_STEMS = frozenset({"license", "licence", "readme", "changelog", "notice", "copying", "authors", "contributing"})
SKIPPED_NAMES = frozenset({"__init__.py", "conftest.py", "requirements.txt", "agents.md", "claude.md"})
SKIPPED_DIRS = frozenset({"tests", "test", "__tests__", "evals"})
TEST_STEM = re.compile(r"test_.*|.*_test|.*\.(?:test|spec)")  # test_build.py, build_test.py, build.test.js
WALK_MAX_FILES = 2000  # a skill with more files is a checkout, not a bundle: the rest are not listed
EXAMPLES_SHOWN = 3
DOC_SUFFIXES = frozenset({".md", ".mdx", ".txt"})
DOC_MAX_BYTES = 256 * 1024
DOCS_READ_MAX = 50
# A path prefix that names the skill root or the current folder: ``./scripts/``, ``../scripts/``, ``${CLAUDE_SKILL_DIR}/scripts/``.
_PATH_PREFIX = re.compile(r"(?<![\w.-])(?:\$\{claude_skill_dir\}/|\.\.?/)")


def _walk(skill_dir: Path, *, counted: bool) -> list[str]:
    """The files under ``skill_dir`` an agent may open, relative and sorted, at most :data:`WALK_MAX_FILES`.

    ``counted``: the files the warning counts. Otherwise every document, the skipped ones too (a
    readme that names a script leads the agent to it).
    """
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
                name.startswith(".")  # hidden
                or suffix.lower() not in COUNTED_SUFFIXES
                or (here == skill_dir and name == "SKILL.md")
                or (counted and (
                    name.startswith("_")  # a private helper (``_common.py``) scripts import
                    or stem.lower() in SKIPPED_STEMS
                    or name.lower() in SKIPPED_NAMES
                    or TEST_STEM.fullmatch(stem.lower())
                ))
            ):
                continue
            found.append((here / name).relative_to(skill_dir).as_posix())
    return found


def _whole(name: str) -> str:
    """A regex for ``name`` as a whole path or file name in a text: not a part of a longer one (``build.py`` in ``rebuild.py``)."""
    # Not followed by a dotted suffix either: ``build.py`` does not name ``build.py.bak``.
    return r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.\w)"


def _target(folder: str, link: str) -> str:
    """The lowercased path, from the skill root, that a link in a document in ``folder`` names."""
    return posixpath.normpath(posixpath.join(folder, link)).lower()


def _named(rel: str, text: str, links: set[str]) -> bool:
    """Whether ``text`` names the file ``rel``: its path, its file name, or a folder above it named alone.

    Case is ignored (``FORMS.md`` names ``forms.md``). ``links`` are the link destinations of
    ``SKILL.md`` and of the documents read so far, as files from the skill root (escapes and
    percent-encoding decoded), lowercased.
    """
    parts = rel.lower().split("/")
    if "/".join(parts) in links or re.search(_whole("/".join(parts)), text):
        return True
    if any("/".join(parts[:i]) in links for i in range(1, len(parts))):  # a folder link to an ancestor
        return True
    if re.search(_whole(parts[-1]), text):
        return True
    # A folder named alone (``references/``), not as the start of another file's path (``scripts/a.py``).
    return any(
        re.search(r"(?<![\w./-])" + re.escape("/".join(parts[:i])) + r"/(?![\w.-])", text)
        for i in range(1, len(parts))
    )


def _matchable(text: str) -> str:
    """The text the names are looked up in, with the ``./``-style prefixes dropped: ``./scripts/`` names ``scripts/``."""
    return _PATH_PREFIX.sub("", text)


def _read_doc(path: Path) -> str:
    """The text of a named document the agent may read on, or "" when it is not one or cannot be read."""
    if path.suffix.lower() not in DOC_SUFFIXES:
        return ""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > DOC_MAX_BYTES:  # a FIFO blocks the read
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def unreferenced_message(
    skill_path: Path,
    links: Iterable[Optional[str]],
    *,
    at_root: bool,
    doc_links: Callable[[str], Iterable[Optional[str]]],
) -> Optional[str]:
    """The ``bundle-file-unreferenced`` message for the skill whose ``SKILL.md`` is ``skill_path``, or None.

    ``links``: the files its Markdown links name (None for a link that names no file). ``doc_links``
    gives the same for the Markdown of a document. A document ``SKILL.md`` names is read too, and so
    on from there (at most :data:`DOCS_READ_MAX`): a script that only ``forms.md`` names is still
    found by an agent that follows ``SKILL.md`` to ``forms.md``.
    """
    if at_root:
        return None
    try:
        text = _matchable(skill_path.read_text(encoding="utf-8").lower())
        files = _walk(skill_path.parent, counted=True)
        docs = _walk(skill_path.parent, counted=False)
    except (OSError, UnicodeDecodeError):
        return None
    linked = {_target(".", link) for link in links if link}
    named_files: set[str] = set()
    docs_read = 0
    candidates = list(dict.fromkeys(files + docs))
    while True:
        new = [rel for rel in candidates if rel not in named_files and _named(rel, text, linked)]
        if not new:
            break
        for rel in new:
            named_files.add(rel)
            if docs_read < DOCS_READ_MAX and (more := _read_doc(skill_path.parent / rel)):
                docs_read += 1
                text += "\n" + _matchable(more.lower())
                folder = PurePosixPath(rel).parent.as_posix()
                linked.update(_target(folder, link) for link in doc_links(more) if link)
    missing = [rel for rel in files if rel not in named_files]
    if not missing:
        return None
    shown = ", ".join(missing[:EXAMPLES_SHOWN]) + (", ..." if len(missing) > EXAMPLES_SHOWN else "")
    return (
        f"{len(missing)} of {len(files)} bundled scripts and documents are never named in SKILL.md "
        f"or a document it names ({shown}); the agent finds them only by browsing. Name each one "
        "where it is used, or remove it"
    )

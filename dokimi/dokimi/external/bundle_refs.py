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
files it names count as named, unless it is a changelog, contributing or notice file, which names
nothing here. Licence, readme and changelog files, ``AGENTS.md``, ``__init__.py`` and other
``_``-prefixed helpers, tests, hidden and vendored folders, and a nested skill's folder are left out
of the count; a document in a test or hidden folder that ``SKILL.md`` names is still read. A skill at
the root of a scanned checkout is not checked, since its bundle is the rest of that checkout.
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
WALK_MAX_FILES = 2000  # a skill with more files is a checkout, not a bundle: it is not checked
EXAMPLES_SHOWN = 3
DOC_SUFFIXES = frozenset({".md", ".mdx", ".txt"})
DOC_MAX_BYTES = 256 * 1024
DOCS_READ_MAX = 50
# Documents whose text names no file the agent needs for the task: a changelog or a notice that mentions a script does not.
NON_REFERENCE_STEMS = frozenset({"changelog", "contributing", "notice"})
# The destination of an inline folder link (``[x](scripts/)``): its folder is in ``links``, so the text is no mention.
_FOLDER_DEST = re.compile(r"\]\([^)\n]*/\)")
SKILL_ROOT_LINK = "${claude_skill_dir}/"  # lowercased: a link through it names a file from the skill root
# A path prefix that names the skill root or the current folder: ``./scripts/``, ``../scripts/``, ``${CLAUDE_SKILL_DIR}/scripts/``.
_PATH_PREFIX = re.compile(r"(?<![\w.-])(?:\$\{claude_skill_dir\}/|\.\.?/)")


def _walk(skill_dir: Path, *, counted: bool) -> list[str]:
    """The files under ``skill_dir`` an agent may open, relative and sorted, at most :data:`WALK_MAX_FILES` + 1.

    One more than the cap tells the caller the bundle is over it. Past the cap the walk goes on
    without collecting, since a stopped ``os.walk`` leaves its directory handles open.

    ``counted``: the files the warning counts. Otherwise every document, the skipped ones too (a
    readme that names a script leads the agent to it).
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in VENDORED_DIR_NAMES and not _is_skill_dir(here / d)
            and (not counted or (not d.startswith(".") and d.lower() not in SKIPPED_DIRS))
        )
        for name in sorted(filenames):
            if len(found) > WALK_MAX_FILES:
                continue
            stem, suffix = os.path.splitext(name)
            if (
                (counted and name.startswith("."))  # a hidden file is no counted file; a document may be one
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


_WORD = re.compile(r"\w")


def _mentions(needle: str, text: str, before: str) -> bool:
    """Whether ``needle`` occurs in ``text`` as a whole path or file name: not preceded by a word character or a
    character of ``before``, and not followed by a word character, ``-``, or a dot and a word character (so
    ``build.py`` is not a part of ``rebuild.py``, and does not name ``build.py.bak``).

    A search with ``str.find`` per occurrence, not a regex over the whole text for each candidate: a bundle's
    documents can be megabytes long.
    """
    i = text.find(needle)
    while i != -1:
        end = i + len(needle)
        after = text[end:end + 1]
        if (i == 0 or not (_WORD.match(text[i - 1]) or text[i - 1] in before)) and not (
            _WORD.match(after) or after == "-" or (after == "." and _WORD.match(text[end + 1:end + 2]))
        ):
            return True
        i = text.find(needle, i + 1)
    return False


def _target(folder: str, link: str) -> str:
    """The lowercased path, from the skill root, that a link in a document in ``folder`` names.

    A link through ``${CLAUDE_SKILL_DIR}/`` is from the skill root whatever the document's folder.
    """
    if link.lower().startswith(SKILL_ROOT_LINK):
        link, folder = link[len(SKILL_ROOT_LINK):], "."
    return posixpath.normpath(posixpath.join(folder, link)).lower()


def _named(rel: str, text: str, links: set[str]) -> bool:
    """Whether ``text`` names the file ``rel``: its path, its file name, or a folder above it named alone.

    Case is ignored (``FORMS.md`` names ``forms.md``). ``links`` are the link destinations of
    ``SKILL.md`` and of the documents read so far, as files from the skill root (escapes and
    percent-encoding decoded), lowercased.
    """
    parts = rel.lower().split("/")
    if "/".join(parts) in links or any("/".join(parts[:i]) in links for i in range(1, len(parts))):
        return True  # a link to the file, or a folder link to an ancestor
    if _mentions("/".join(parts), text, ".-") or _mentions(parts[-1], text, ".-"):
        return True
    # A folder named alone (``references/``), not as the start of another file's path (``scripts/a.py``).
    return any(_mentions("/".join(parts[:i]) + "/", text, "./-") for i in range(1, len(parts)))


_RELATIVE = re.compile(r"(?<![\w./-])((?:\.\.?/)+)")


def _from_folder(text: str, folder: str) -> str:
    """``text`` with its ``./`` and ``../`` paths resolved from the skill root, for a document in ``folder``.

    In ``references/guide.md`` the words ``./scripts/`` name ``references/scripts/``, not the root's.
    """
    if folder == ".":
        return text

    def resolve(match: re.Match[str]) -> str:
        base = folder
        for part in match.group(1).split("/")[:-1]:
            if part == "..":
                base = posixpath.dirname(base) or "."
        return "" if base == "." else base + "/"

    return _RELATIVE.sub(resolve, text)


def _matchable(text: str) -> str:
    """The text the names are looked up in: folder link destinations and the ``./``-style prefixes dropped.

    A folder destination is in ``links`` as its folder, so ``[scripts](scripts/)`` in a nested document
    does not name the skill-root ``scripts/``; ``./scripts/`` and ``${CLAUDE_SKILL_DIR}/scripts/`` in
    the text name ``scripts/``. A file's name in a destination still names it, as in the text.
    """
    return _PATH_PREFIX.sub("", _FOLDER_DEST.sub("]", text))


def _read_doc(path: Path) -> Optional[str]:
    """The text of a named document: "" for a file that is not a document, None for a document that cannot be
    read (a symlink, a FIFO or another non-file, over :data:`DOC_MAX_BYTES`, not UTF-8), whose names are unknown."""
    if path.suffix.lower() not in DOC_SUFFIXES:
        return ""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > DOC_MAX_BYTES:  # a FIFO blocks the read
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _named_closure(
    skill_dir: Path,
    files: list[str],
    docs: list[str],
    text: str,
    linked: set[str],
    doc_links: Callable[[str], Iterable[Optional[str]]],
) -> tuple[set[str], bool]:
    """The bundled files that ``SKILL.md`` or a document it names mentions, followed from there, and whether
    the search was incomplete: the document walk was truncated, a named document was past the read cap, or a
    named document could not be read (its names are unknown).

    ``text`` holds the names of ``SKILL.md``; ``linked`` the names of its links and of each document read. Each
    pass checks the candidates against the text not yet checked (``unseen``), and each document read adds to it.
    """
    named_files: set[str] = set()
    docs_read = 0
    incomplete = len(docs) > WALK_MAX_FILES
    candidates = list(dict.fromkeys(files + docs))
    unseen = text  # the text no candidate has been checked against yet: a candidate is only new against it
    while True:
        new = [rel for rel in candidates if rel not in named_files and _named(rel, unseen, linked)]
        if not new:
            break
        unseen = ""
        for rel in new:
            named_files.add(rel)
            if PurePosixPath(rel).stem.lower() in NON_REFERENCE_STEMS:
                continue
            if docs_read >= DOCS_READ_MAX:
                incomplete = incomplete or PurePosixPath(rel).suffix.lower() in DOC_SUFFIXES
                continue
            more = _read_doc(skill_dir / rel)
            if more is None:  # named, but its text cannot be read: what it names is unknown
                incomplete = True
                continue
            if more:
                docs_read += 1
                folder = PurePosixPath(rel).parent.as_posix()
                unseen += "\n" + _matchable(_from_folder(more.lower(), folder))
                linked.update(_target(folder, link) for link in doc_links(more) if link)
    return named_files, incomplete


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
    if len(files) > WALK_MAX_FILES:  # a checkout, not a bundle: a partial list would be a wrong count
        return None
    linked = {_target(".", link) for link in links if link}
    named_files, incomplete = _named_closure(skill_path.parent, files, docs, text, linked, doc_links)
    if incomplete:  # a name the search cannot see may be the one a script relies on: claim nothing
        return None
    missing = [rel for rel in files if rel not in named_files]
    if not missing:
        return None
    shown = ", ".join(missing[:EXAMPLES_SHOWN]) + (", ..." if len(missing) > EXAMPLES_SHOWN else "")
    return (
        f"{len(missing)} of {len(files)} bundled scripts and documents are never named in SKILL.md "
        f"or a document it names ({shown}); the agent finds them only by browsing. Name each one "
        "where it is used, or remove it"
    )

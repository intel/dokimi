# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``path-missing``: a code span naming a bundled file the skill does not have.

A code span that names a file under one of the bundle folders (:data:`BUNDLE_DIRS`: ``scripts/``,
``references/``, ``assets/``, ``templates/``, ``examples/``, ``resources/``), alone or as a word of
a command (``python scripts/run.py``), is a ``warn`` in :mod:`dokimi.external.validate` when that
file is not in the skill directory. Only a path with a folder and a file name is read: a folder
(``assets/``), a flat name (``run.py``), a placeholder part (``scripts/<name>.py``, ``scripts/x.py``)
and a ``#anchor`` are not files to check. A repo-root path such as ``src/app.py`` names no bundled
file and is not read: in the GitSkills corpus 72.6% of those look missing, mostly files to create
or files in the user's project. Backticked bundle paths there miss 23% of the time, against 8% for
Markdown links (``link-missing``); a hand audit put the precision of a hit at 53-80%, hence a warn.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Optional

SKILL_DIR_VAR = "${CLAUDE_SKILL_DIR}/"
# After a file, a line or a symbol (``run.py:42``, ``run.py:main``): read as the file's name.
LOCATION_SUFFIX_RE = re.compile(r":(?:\d+(?:[-:]\d+)?|[A-Za-z_]\w*)$")
# The folders a skill bundles files in: a code span naming a file under one of them is checked (``path-missing``).
BUNDLE_DIRS = ("scripts", "references", "assets", "templates", "examples", "resources")
# A path part that stands for a name the reader fills in, not a file (``scripts/x.py``, ``assets/xxx.png``).
PLACEHOLDER_PARTS = frozenset({"x", "xx", "xxx", "n/a", "na", "foo", "bar", "example", "name", "file", "filename", "your-file", "...", "…"})


def _resolved(path: Path) -> Optional[Path]:
    """``path`` resolved, or None when the OS refuses the name (a NUL byte, a symlink loop)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _exists(path: Path) -> bool:
    """``path.exists()``, where a name the OS refuses (over 255 bytes in one part, say) is absent."""
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _inside(path: Path, base: Path) -> bool:
    """Whether ``path`` lies under ``base`` by its written name, with ``..`` taken away. Reads no file."""
    return Path(os.path.abspath(path)).is_relative_to(os.path.abspath(base))


def _in_checkout(path: Path, source_root: Path) -> Optional[Path]:
    """``path`` resolved, or None unless it lies in the checkout both as written and where it resolves."""
    target = _resolved(path) if _inside(path, source_root) else None
    return target if target is not None and target.is_relative_to(source_root.resolve()) else None


def _bundle_path(word: str) -> Optional[str]:
    """The skill-relative file a code span word names under a bundle folder, or None.

    ``word`` is one whitespace-separated word of a code span, quotes and a trailing ``,;:.)``
    taken away. None for a word outside :data:`BUNDLE_DIRS`, a folder (a trailing ``/`` or no
    file extension), a placeholder part, or a template variable.
    """
    text = word.rstrip(",;:.)").strip("'\"").rstrip(",;:.)")
    if text.startswith("./"):
        text = text[2:]
    if text.startswith(SKILL_DIR_VAR):
        text = text[len(SKILL_DIR_VAR) :]
    text = text.split("#", 1)[0]
    text = LOCATION_SUFFIX_RE.sub("", text)
    if not text or text.endswith("/") or any(c in text for c in "${}<>*?[]|=") or "://" in text:
        return None
    parts = text.split("/")
    if len(parts) < 2 or parts[0] not in BUNDLE_DIRS or any(not p or p in (".", "..") for p in parts):
        return None
    stem, dot, ext = parts[-1].rpartition(".")
    if not dot or not stem or not ext.isalnum():
        return None  # no file extension: read as a folder or a command name
    if any(p.lower() in PLACEHOLDER_PARTS for p in parts[1:-1]) or stem.lower() in PLACEHOLDER_PARTS:
        return None
    return text


def _words(span: str) -> list[str]:
    """The words of a code span as a shell reads them: a quoted argument with a space is one word.

    A span whose quotes do not balance is split on whitespace.
    """
    try:
        return shlex.split(span)
    except ValueError:
        return span.split()


def _missing(skill_dir: Path, text: str, real_dir: Path) -> bool:
    """Whether the skill has no file ``text``. A name that resolves outside the skill is not read, so it is not missing."""
    target = _resolved(skill_dir / text)
    return target is not None and target.is_relative_to(real_dir) and not _exists(target)


def path_missing(spans: list[str], skill_dir: Path, source_root: Path, real_dir: Path) -> list[str]:
    """The ``path-missing`` message for each code span word that names a bundled file the skill does not have.

    A note is added when the file exists from the catalog root: the skill works from a checkout of
    its repository only, not once it is installed alone.
    """
    findings: list[str] = []
    seen: set[str] = set()
    for raw in spans:
        whole = raw.strip()
        if " " in whole and not any(q in whole for q in "'\""):
            text = _bundle_path(whole)
            if text is not None and not _missing(skill_dir, text, real_dir):
                continue  # a path with a space in its name that the skill has, read as one path
        for word in _words(raw):
            text = _bundle_path(word)
            if text is None or text in seen:
                continue
            seen.add(text)
            if not _missing(skill_dir, text, real_dir):
                continue  # an existing file is fine; a symlink out of the skill is not followed
            rooted = _in_checkout(source_root / text, source_root)
            from_root = (
                " (it exists from the catalog root: the skill works only from a checkout of its repository)"
                if rooted is not None and _exists(rooted)
                else ""
            )
            findings.append(
                f"{text!r} (in `{raw}`) names no file in the skill directory; the agent cannot "
                f"open it once the skill is installed{from_root}"
            )
    return findings

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The file a Markdown link or code span names inside a skill: :func:`_file_ref`, :func:`_link_files`.

Kept apart from :mod:`dokimi.external.validate`, which these were moved out of to stay under the
file-length cap (``scripts/growth_baseline.json``).
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote

from .bundle_paths import LOCATION_SUFFIX_RE, SKILL_DIR_VAR
from .skill_markdown import link_pieces, outside_fences


# A backslash before ASCII punctuation is an escape in a link destination (``guide\_notes.md``).
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
# RFC 3986 scheme, dots included; a dotted one with a line or symbol after it is a file (_file_ref).
URI_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+\-.]*:")
# After a file, a line or a symbol (``run.py:42``, ``run.py:10-20``, ``run.py:main``): read as the file's name.
FILE_SUFFIX_RE = re.compile(r"\d+(?:[-:]\d+)?|[A-Za-z_]\w*")


def _link_files(markdown: str) -> list[Optional[str]]:
    """The file each Markdown link of ``markdown`` names (see :func:`_file_ref`), None for a link that names none.

    A link through ``${CLAUDE_SKILL_DIR}/`` keeps that prefix: it is from the skill root, not from the
    folder of the document it is in (:func:`bundle_refs._target` resolves it).
    """
    files: list[Optional[str]] = []
    for raw in link_pieces(outside_fences(markdown))[0]:
        ref = _file_ref(raw, link=True)
        if ref is not None and _through_skill_dir(raw):
            ref = SKILL_DIR_VAR + ref
        elif _is_skill_dir_root(raw):  # ``${CLAUDE_SKILL_DIR}/`` alone names the skill root, as ``./`` does
            ref = SKILL_DIR_VAR
        files.append(ref)
    return files


def _is_skill_dir_root(raw: str) -> bool:
    """Whether a link destination is the skill root through the variable, with or without its slash."""
    text = raw.strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    text = unquote(_ESCAPE_RE.sub(r"\1", text)).split("#", 1)[0].split("?", 1)[0].strip()
    return text in (SKILL_DIR_VAR, SKILL_DIR_VAR.rstrip("/"))


def _through_skill_dir(raw: str) -> bool:
    """Whether a link destination starts with ``${CLAUDE_SKILL_DIR}/`` (decoded as :func:`_file_ref` decodes it)."""
    text = raw.strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    return unquote(_ESCAPE_RE.sub(r"\1", text)).startswith(SKILL_DIR_VAR)


def _file_ref(raw: str, link: bool = False) -> Optional[str]:
    """The skill-relative file a reference names, or None if it names no file to check.

    None for a URL or any other URI (``tel:``, ``urn:``, ``MAILTO:``), a bare anchor, an
    absolute path, a placeholder (``URL``, ``TBD``, ``...``), or a template variable (not ``${CLAUDE_SKILL_DIR}``). A
    ``<...>`` destination (CommonMark) is read as the path inside the brackets. A link
    destination (``link``) is a URL: its ``#fragment`` and ``?query`` are not part of the path,
    its backslash escapes of ASCII punctuation (``guide\\_notes.md``) are decoded, and its
    percent-encoded bytes name the file (``guide%20notes.md``). A code span is literal: a ``#``
    or a ``%`` in it is in the file's name.
    """
    text = raw.strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    if link:
        text = _ESCAPE_RE.sub(r"\1", text)
        text = text.split("#", 1)[0].split("?", 1)[0].strip()
        text = unquote(text)
    if not text or "://" in text or text.startswith("/"):
        return None
    scheme = URI_SCHEME_RE.match(text)
    if scheme and not ("." in scheme.group() and FILE_SUFFIX_RE.fullmatch(text[scheme.end() :])):
        return None
    text = LOCATION_SUFFIX_RE.sub("", text)  # the file is named before its line or symbol suffix
    if not text or text.lower() in {"url", "link", "path", "uri", "href", "todo", "tbd", "..."}:  # a stand-in
        return None
    if text.startswith(SKILL_DIR_VAR):
        text = text[len(SKILL_DIR_VAR) :]
        if not text:
            return None
    if any(c in text for c in "${}<>*"):
        return None
    return text

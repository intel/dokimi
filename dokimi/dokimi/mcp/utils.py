# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shared utilities for MCP doc-source clients."""

import contextlib
import logging
import os
import re
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    """Collect the text of a page, dropping script/style bodies; every tag becomes a space."""

    _SKIPPED = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIPPED:
            self._skip_depth += 1
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self._skip_depth:
            self._skip_depth -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Text of an HTML page with tags, comments and script/style blocks removed.

    A parser, not a regex: a regex misses end tags such as ``</script >`` or
    ``</script foo>`` that browsers accept, and leaks the script body into the text.
    Whitespace is left as found.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def strip_html(text: str) -> str:
    """Remove HTML tags, strip script/style blocks, collapse whitespace."""
    return re.sub(r"\s+", " ", html_to_text(text)).strip()


def score_chunk(query: str, content: str) -> float:
    """Simple keyword overlap score (0–1)."""
    query_tokens = set(re.findall(r"\w+", query.lower()))
    if not query_tokens:
        return 0.0
    content_lower = content.lower()
    hits = sum(1 for tok in query_tokens if tok in content_lower)
    return hits / len(query_tokens)


def split_paragraphs(text: str, min_len: int = 80) -> List[str]:
    """Split text on blank lines; discard very short chunks."""
    chunks = re.split(r"\n{2,}|\r\n{2,}", text)
    return [c.strip() for c in chunks if len(c.strip()) >= min_len]


def read_cache_text(path: Path) -> Optional[str]:
    """Text of the cache entry at *path*, or ``None`` for a miss.

    An entry that cannot be read (not UTF-8, a directory, no permission) is a miss too, with a
    warning that names it, so the caller fetches again and overwrites it.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Ignoring unreadable cache entry %s (%s); fetching again", path, exc)
        return None


def write_cache_text(path: Path, text: str) -> bool:
    """Store a cache entry; a reader never sees half of it. Returns whether it was stored.

    A failed write is a warning, not an error: the caller already has what it fetched.

    The tmp name is per process and thread, so concurrent writers of one entry do not share it.
    """
    # Move to dokimi.atomic_write once https://github.com/intel/dokimi/pull/697 is on main.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):  # a directory refusing the write may refuse this too
            tmp.unlink(missing_ok=True)
        logger.warning("Could not write cache entry %s: %s", path, exc)
        return False
    return True

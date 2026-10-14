# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shared utilities for MCP doc-source clients."""

import re
from html.parser import HTMLParser
from typing import List


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

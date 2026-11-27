# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How a scan names its own repository: the ``source.url`` blob/tree link pattern and the ``owner/name`` slug."""

from __future__ import annotations

import re
from collections.abc import Callable

_GITHUB_SLUG = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)", re.I)


def patterns(repo_url: str | None) -> tuple[str | None, re.Pattern[str] | None]:
    """``(slug, link)`` of ``source.url``: the lower-cased ``owner/name`` of a GitHub URL, and the blob/tree link regex."""
    url = (repo_url or "").rstrip("/").removesuffix(".git")
    slug = _GITHUB_SLUG.fullmatch(url)
    return (slug.group(1).lower() if slug else None,
            re.compile(re.escape(url) + r"/(blob|tree)/(.+)") if url else None)


def is_url_start(span: str, slug: str | None, prose: bool) -> bool:
    """A span starting with ``http`` is no path claim, unless a ``prose`` span is this repository's ``owner/name``."""
    return span.startswith("http") and not (prose and span.lower() == slug)


def verdict(slug: str | None, span: str, cands: list[str], exists: Callable[[str], bool]) -> tuple[str, str] | None:
    """``ok`` for a span equal to this repository's ``owner/name`` that no path candidate holds, else ``None``."""
    if span.lower() == slug and not any(exists(c) for c in cands):
        return "ok", "this repository (source.url)"
    return None

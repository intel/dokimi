# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""MkDocs hooks: point links that leave ``docs/`` at GitHub, and check lists.

The pages link into the source tree with relative paths (``../dokimi/eval/judge.py``,
``../../README.md``), which resolve when the Markdown is read on GitHub. The site
only contains ``docs/``, so those links would 404 there; this rewrites them to
``<repo_url>/blob/main/<path>`` (``tree`` for directories, ``raw`` for images) before
the page is rendered. Links inside ``docs/``, absolute URLs and links whose target
does not exist are left alone, so mkdocs reports a missing target and ``--strict``
fails on it. An anchor into a file outside ``docs/`` is not checked.

The pages are written to read on GitHub too, and Python-Markdown parses lists
differently from GitHub: a nested item needs 4 spaces of indentation, and a list
right after a paragraph line needs a blank line before it. A page whose list items
nest differently on the site than under CommonMark gets a warning naming the first
item that differs, which fails ``--strict``.

The generated ``reference/*/SUMMARY.md`` nav files are kept out of the built site.
"""

from __future__ import annotations

import logging
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path

from markdown_it import MarkdownIt

log = logging.getLogger("mkdocs.hooks.dokimi")

# Inline links `[text](target)` and reference definitions `[id]: target`.
_INLINE = re.compile(r"(\]\()([^)\s]+)(\))")
_REFDEF = re.compile(r"^(\s*\[[^\]]+\]:\s*)(\S+)", re.MULTILINE)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _is_image(markdown: str, close: int) -> bool:
    """Whether the `]` at ``close`` ends the text of an image, `![...]`."""
    depth = 0
    for i in range(close, -1, -1):
        if markdown[i] == "]":
            depth += 1
        elif markdown[i] == "[":
            depth -= 1
            if depth == 0:
                return i > 0 and markdown[i - 1] == "!"
    return False


def on_page_markdown(markdown, page, config, files):
    docs_dir = Path(config["docs_dir"]).resolve()
    repo_root = Path(config["config_file_path"]).resolve().parents[1]
    base = config["repo_url"].rstrip("/")
    page_dir = posixpath.dirname(page.file.src_uri)

    def rewrite(target: str, image: bool = False) -> str:
        if target.startswith(("#", "/")) or _SCHEME.match(target):
            return target
        path, sep, frag = target.partition("#")
        resolved = (docs_dir / page_dir / path).resolve()
        if resolved.is_relative_to(docs_dir) or not resolved.exists():
            return target
        if not resolved.is_relative_to(repo_root):
            log.warning("Doc file '%s' links to '%s', which is outside the repository",
                        page.file.src_uri, target)
            return target
        rel = resolved.relative_to(repo_root).as_posix()
        kind = "tree" if resolved.is_dir() else "raw" if image else "blob"
        return f"{base}/{kind}/main/{rel}{sep}{frag}"

    markdown = _INLINE.sub(
        lambda m: m[1] + rewrite(m[2], _is_image(m.string, m.start())) + m[3], markdown)
    return _REFDEF.sub(lambda m: m[1] + rewrite(m[2]), markdown)


class _Items(HTMLParser):
    """The list items of an HTML fragment: (nesting depth, start of the item's text)."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.items: list[list] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("ul", "ol"):
            self.depth += 1
        elif tag == "li":
            self.items.append([self.depth, ""])

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self.depth -= 1

    def handle_data(self, data):
        if self.items and len(self.items[-1][1]) < 60:
            self.items[-1][1] += data


def list_items(html: str) -> list[tuple[int, str]]:
    parser = _Items()
    parser.feed(html)
    return [(d, " ".join(text.split())[:60]) for d, text in parser.items]


_COMMONMARK = MarkdownIt("commonmark").enable("table")
# YAML front matter (a page's `depends_on`): mkdocs drops it and GitHub shows it as
# a table, so its list is neither side's list items.
_FRONT_MATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*\n", re.DOTALL)


def list_mismatch(source: str, html: str) -> str | None:
    """How the site's lists differ from CommonMark's for ``source``; None if they match."""
    github = list_items(_COMMONMARK.render(_FRONT_MATTER.sub("", source, count=1)))
    site = list_items(html)
    for i, (g, s) in enumerate(zip(github, site, strict=False)):
        if g[1][:25] != s[1][:25]:
            return f"list item {i + 1} on GitHub, {g[1]!r}, is not a list item on the site"
        if g[0] != s[0]:
            return (f"list item {i + 1}, {g[1]!r}, is at depth {g[0]} on GitHub "
                    f"but {s[0]} on the site")
    if len(github) > len(site):
        n = len(site)
        return f"list item {n + 1} on GitHub, {github[n][1]!r}, is not a list item on the site"
    if len(github) < len(site):
        return f"{len(github)} list items on GitHub but {len(site)} on the site"
    return None


def on_nav(nav, config, files):
    # literate-nav has read the generated reference/*/SUMMARY.md by now (on_files);
    # without this they are also published as pages, in the sitemap and in search.
    from mkdocs.structure.files import InclusionLevel  # only here: the tests run without mkdocs

    for file in files:
        if file.generated_by and posixpath.basename(file.src_uri) == "SUMMARY.md":
            file.inclusion = InclusionLevel.EXCLUDED
    return nav


def on_page_content(html, page, config, files):
    if page.file.generated_by is None:  # the generated reference pages are not read on GitHub
        problem = list_mismatch(page.file.content_string, html)
        if problem:
            log.warning("Doc file '%s': %s. Indent nested items 4 spaces and put a blank "
                        "line before a list.", page.file.src_uri, problem)
    return html

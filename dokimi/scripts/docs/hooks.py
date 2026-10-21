# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""MkDocs hooks: point links that leave ``docs/`` at GitHub, and check lists.

The pages link into the source tree with relative paths (``../dokimi/eval/judge.py``,
``../../README.md``), which resolve when the Markdown is read on GitHub. The site
only contains ``docs/``, so those links would 404 there. A treeprocessor rewrites
``a[href]`` and ``img[src]`` to ``<repo_url>/blob/main/<path>`` (``tree`` for
directories, ``raw`` for images) before mkdocs resolves them. Code is never
rewritten, because it is not a link element. Links inside ``docs/``, absolute URLs
and links whose target does not exist are left alone, so mkdocs reports a missing
target and ``--strict`` fails on it. An anchor into a file outside ``docs/`` is
not checked. A relative ``href`` or ``src`` in raw HTML cannot be rewritten here
(the parser stashes it); that page gets a warning instead.

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
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt

log = logging.getLogger("mkdocs.hooks.dokimi")

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# Set in on_page_markdown, read by the treeprocessor during the following convert,
# cleared in on_page_content. MkDocs renders one page at a time, after that hook
# returns; Markdown rendered outside a page body is not rewritten.
_page = None
_config = None


def _rewrite(target: str, *, image: bool, docs_dir: Path, repo_root: Path, base: str,
             page_dir: str, page_uri: str) -> str:
    if target.startswith(("#", "/")) or _SCHEME.match(target):
        return target
    rest, hsep, frag = target.partition("#")
    path, qsep, query = rest.partition("?")
    resolved = (docs_dir / page_dir / unquote(path)).resolve()
    if resolved.is_relative_to(docs_dir) or not resolved.exists():
        return target
    if not resolved.is_relative_to(repo_root):
        log.warning("Doc file '%s' links to '%s', which is outside the repository",
                    page_uri, target)
        return target
    rel = resolved.relative_to(repo_root).as_posix()
    kind = "tree" if resolved.is_dir() else "raw" if image else "blob"
    return f"{base}/{kind}/main/{quote(rel)}{qsep}{query}{hsep}{frag}"


def rewrite_tree(root, page, config) -> None:
    """Rewrite link and image targets on a parsed element tree. Code is not an element."""
    docs_dir = Path(config["docs_dir"]).resolve()
    repo_root = Path(config["config_file_path"]).resolve().parents[1]
    base = config["repo_url"].rstrip("/")
    page_dir = posixpath.dirname(page.file.src_uri)
    page_uri = page.file.src_uri
    for el in root.iter():
        if el.tag == "a" and el.get("href"):
            el.set("href", _rewrite(el.get("href"), image=False, docs_dir=docs_dir,
                                    repo_root=repo_root, base=base, page_dir=page_dir,
                                    page_uri=page_uri))
        elif el.tag == "img" and el.get("src"):
            el.set("src", _rewrite(el.get("src"), image=True, docs_dir=docs_dir,
                                   repo_root=repo_root, base=base, page_dir=page_dir,
                                   page_uri=page_uri))


class _RawHtmlLinks(HTMLParser):
    """The relative ``href``/``src`` targets in the start tags of an HTML fragment."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value:
                if not value.startswith(("#", "/")) and not _SCHEME.match(value):
                    self.found.append((name, value))


def _warn_raw_html(fragments, page) -> None:
    for fragment in fragments:
        parser = _RawHtmlLinks()
        parser.feed(fragment)
        for attr, url in parser.found:
            log.warning(
                "Doc file '%s' has a relative %s in raw HTML (%r); the site does not rewrite it",
                page.file.src_uri, attr, url,
            )


def on_config(config, **kwargs):
    """Register the link rewriter above mkdocs' ``relpath`` treeprocessor (priority 0)."""
    try:
        from markdown.extensions import Extension
        from markdown.treeprocessors import Treeprocessor
    except ImportError:
        log.warning("Python-Markdown is not installed; links that leave docs/ will 404 on the site")
        return config

    class _GithubLinks(Treeprocessor):
        def run(self, root):
            if _page is not None and _config is not None:
                rewrite_tree(root, _page, _config)
                _warn_raw_html(self.md.htmlStash.rawHtmlBlocks, _page)

    class _GithubLinksExtension(Extension):
        dokimi_github_links = True

        def extendMarkdown(self, md):
            md.treeprocessors.register(_GithubLinks(md), "dokimi_github_links", 15)

    extensions = config.setdefault("markdown_extensions", [])
    if not any(getattr(item, "dokimi_github_links", False) for item in extensions):
        extensions.append(_GithubLinksExtension())
    return config


def on_page_markdown(markdown, page, config, files):
    global _page, _config
    _page, _config = page, config
    return markdown


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
# a table, so its list is neither side's list items. Supports optional UTF-8 BOM,
# flexible line endings (LF/CRLF), and handles pages ending without a final newline.
_FRONT_MATTER = re.compile(r"\A\ufeff?---[ \t]*\r?\n.*?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)


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
    global _page, _config
    _page, _config = None, None
    if page.file.generated_by is None:  # the generated reference pages are not read on GitHub
        problem = list_mismatch(page.file.content_string, html)
        if problem:
            log.warning("Doc file '%s': %s. Indent nested items 4 spaces and put a blank "
                        "line before a list.", page.file.src_uri, problem)
    return html

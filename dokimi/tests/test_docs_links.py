# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Links between the docs resolve, and the index lists every doc.

A dead cross-link in a walkthrough is the cheapest possible defect to catch and the most
annoying one to hit: the reader is mid-task. The docs are also the only navigation surface
the framework has, so a doc nobody links to is a doc nobody finds.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DOCS = PROJECT / "docs"
#: The package's PyPI page. PyPI resolves no relative link, so it links other files by
#: their URL on GitHub (`REPO_LINK`); a same-page `#anchor` works there as on GitHub.
PYPI_README = PROJECT / tomllib.loads(
    (PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["readme"]
#: Link *sources*: the docs plus the project README, which is the entry point most readers
#: arrive through and links into `docs/` ten times.
SOURCES = sorted(DOCS.glob("*.md")) + sorted(DOCS.glob("user-journeys/*.md")) + [PROJECT / "README.md"]
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
#: A reference-style definition, `[label]: target`, which an inline-only pattern never sees.
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.M)
#: A file of this repository on `main`, checked against this checkout like a relative link.
REPO_LINK = re.compile(r"https://github\.com/intel/dokimi/(?:blob|tree)/main/([^#?]+)(?:#(.*))?")
_FENCE = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.M | re.S)
_INLINE_CODE = re.compile(r"`[^`\n]+`")


def _prose(text: str) -> str:
    """The text with code removed: a link inside a code example is not a link."""
    # A placeholder, not "": a link whose text is only code must still have text.
    return _INLINE_CODE.sub("x", _FENCE.sub("", text))


def _relative_links(path: Path) -> list[str]:
    """Inline and reference-style link targets that are not external, `#x` included."""
    text = _prose(path.read_text(encoding="utf-8"))
    return [
        target for target in LINK.findall(text) + REFERENCE.findall(text)
        if not target.startswith(("http://", "https://", "mailto:"))
    ]


def test_every_relative_doc_link_resolves():
    broken: list[str] = []
    for path in SOURCES:
        for target in _relative_links(path):
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            if not (path.parent / file_part).resolve().exists():
                broken.append(f"{path.name} -> {target}")
    assert broken == []


def _repo_links(path: Path) -> list[tuple[str, Path, str]]:
    """Links to a file of this repository on GitHub: (target, that file here, anchor)."""
    text = _prose(path.read_text(encoding="utf-8"))
    return [
        (target, PROJECT.parent / match[1], match[2] or "")
        for target in LINK.findall(text) + REFERENCE.findall(text)
        if (match := REPO_LINK.fullmatch(target))
    ]


def test_every_link_to_a_repository_file_resolves():
    broken: list[str] = []
    for path in SOURCES:
        for target, file, anchor in _repo_links(path):
            if not file.exists() or (anchor and file.is_file() and anchor not in _slugs(file)):
                broken.append(f"{path.name} -> {target}")
    assert broken == []


def test_the_pypi_readme_links_files_by_their_github_url():
    relative = [target for target in _relative_links(PYPI_README) if not target.startswith("#")]
    assert relative == [], (
        f"{PYPI_README.name} is the PyPI page, where these links 404: write them as "
        "https://github.com/intel/dokimi/blob/main/<path from the repository root>")


def test_repo_link_maps_to_the_checkout_file_and_anchor(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("[a](https://github.com/intel/dokimi/blob/main/dokimi/README.md#ci), "
                   "[b](https://github.com/intel/dokimi/tree/main/dokimi/docs), "
                   "[c](https://github.com/intel/dokimi/blob/v0.1.0/README.md), "
                   "[d](https://example.com/README.md)\n", encoding="utf-8")
    assert _repo_links(doc) == [
        ("https://github.com/intel/dokimi/blob/main/dokimi/README.md#ci",
         PROJECT / "README.md", "ci"),
        ("https://github.com/intel/dokimi/tree/main/dokimi/docs", DOCS, ""),
    ]


def _slugs(path: Path) -> set[str]:
    """GitHub-style anchor slugs of a doc's headings, including duplicate suffixes.

    GitHub disambiguates repeated headings with `-1`, `-2`, …; without those a link to the
    second occurrence of a heading would be reported as broken. GitHub keeps underscores
    (`guidance_reach.v1` slugs to `guidance_reachv1`), and a `# comment` inside a code
    fence is not a heading, so it neither adds a slug nor shifts the duplicate counter.
    """
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, re.M):
        base = re.sub(r"[^a-z0-9_\- ]", "", heading.strip().lower()).replace(" ", "-")
        count = seen.get(base, 0)
        slugs.add(base if not count else f"{base}-{count}")
        seen[base] = count + 1
    slugs.update(re.findall(r'<a id="([^"]+)"></a>', text))  # explicit row anchors
    return slugs


def test_every_anchor_link_points_at_a_real_heading():
    """Cross-file anchors too: a renamed heading in another doc is the common break."""
    broken: list[str] = []
    for path in SOURCES:
        for target in _relative_links(path):
            if "#" not in target:
                continue
            file_part, _, anchor = target.partition("#")
            if not anchor:
                continue
            target_path = path if not file_part else (path.parent / file_part)
            if not target_path.exists():
                continue                      # the missing-file test owns that failure
            if anchor not in _slugs(target_path):
                broken.append(f"{path.name} -> {target}")
    assert broken == []


def test_duplicate_headings_resolve_to_their_github_suffix(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("## Limits\n\ntext\n\n## Limits\n\nmore\n", encoding="utf-8")
    assert _slugs(doc) == {"limits", "limits-1"}


def test_slugs_keep_underscores_and_skip_code_fences(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("## Reach (`guidance_reach.v1`)\n\n```bash\n# Limits\nrun\n```\n\n"
                   "## Limits\n", encoding="utf-8")
    assert _slugs(doc) == {"reach-guidance_reachv1", "limits"}


def test_the_index_links_every_doc_in_the_directory():
    index = DOCS / "README.md"
    linked = {
        target.split("#", 1)[0] for target in _relative_links(index)
    }
    missing = sorted(
        path.name for path in DOCS.glob("*.md")
        if path.name not in {"README.md"} and path.name not in linked
    )
    assert missing == []


def _broken_anchors(path: Path) -> list[str]:
    broken = []
    for target in _relative_links(path):
        file_part, _, anchor = target.partition("#")
        target_path = path if not file_part else path.parent / file_part
        if anchor and target_path.exists() and anchor not in _slugs(target_path):
            broken.append(target)
    return broken


def test_same_page_anchor_is_checked(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("## Real\n\n[ok](#real) and [see](#not-a-heading)\n\n"
                   "```md\n[example](#inside-a-code-block)\n```\n", encoding="utf-8")
    assert _broken_anchors(doc) == ["#not-a-heading"]


def test_reference_style_link_is_checked(tmp_path):
    doc = tmp_path / "d.md"
    (tmp_path / "there.md").write_text("# There\n", encoding="utf-8")
    doc.write_text("# D\n\nSee [b] and [c].\n\n[b]: missing.md\n[c]: there.md#nowhere\n",
                   encoding="utf-8")
    assert _relative_links(doc) == ["missing.md", "there.md#nowhere"]
    assert _broken_anchors(doc) == ["there.md#nowhere"]


def test_link_whose_text_is_inline_code_is_checked(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("# D\n\n[`cmd`](missing.md) and [`a`](d.md#nope)\n\n"
                   "`[not](a-link.md)`\n", encoding="utf-8")
    assert _relative_links(doc) == ["missing.md", "d.md#nope"]
    assert _broken_anchors(doc) == ["d.md#nope"]

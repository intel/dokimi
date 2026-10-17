# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/docs/hooks.py, the mkdocs hook; mkdocs itself is not needed."""

from __future__ import annotations

import importlib.util
import logging
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("markdown_it")

_spec = importlib.util.spec_from_file_location(
    "docs_hooks", Path(__file__).resolve().parents[1] / "scripts" / "docs" / "hooks.py"
)
assert _spec and _spec.loader
hooks = importlib.util.module_from_spec(_spec)
sys.modules["docs_hooks"] = hooks
_spec.loader.exec_module(hooks)

BASE = "https://github.com/o/r"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """repo/proj/{mkdocs.yml,docs/{a.md,sub/b.md},pkg/{m.py,x.png}}, plus a sibling outside."""
    proj = tmp_path / "repo" / "proj"
    (proj / "docs" / "sub").mkdir(parents=True)
    (proj / "pkg").mkdir()
    for f in ("mkdocs.yml", "docs/a.md", "docs/sub/b.md", "pkg/m.py", "pkg/x.png"):
        (proj / f).write_text("")
    (tmp_path / "repo" / "README.md").write_text("")
    (tmp_path / "other.md").write_text("")
    return proj


def _config(tree: Path) -> dict:
    return {"docs_dir": str(tree / "docs"), "config_file_path": str(tree / "mkdocs.yml"),
            "repo_url": BASE + "/", "markdown_extensions": ["fenced_code"]}


def _page(page: str = "a.md"):
    return SimpleNamespace(file=SimpleNamespace(src_uri=page))


def _render(tree: Path, markdown: str, page: str = "a.md") -> str:
    return hooks.on_page_markdown(markdown, _page(page), _config(tree), None)


def _element(tree: Path, tag: str, attr: str, target: str, page: str = "a.md"):
    # Literal fixture built in this test, not parsed untrusted XML.
    root = ET.fromstring(f'<div><{tag} {attr}="{target}">x</{tag}></div>')
    hooks.rewrite_tree(root, _page(page), _config(tree))
    return root.find(tag)


def site_html(tree: Path, source: str, page: str = "a.md") -> str:
    markdown = pytest.importorskip("markdown")
    config = _config(tree)
    hooks.on_config(config)
    source = hooks.on_page_markdown(source, _page(page), config, None)
    return markdown.markdown(source, extensions=list(config["markdown_extensions"]))


@pytest.mark.parametrize(("tag", "attr", "target", "out"), [
    ("a", "href", "../pkg/m.py#L3", f"{BASE}/blob/main/proj/pkg/m.py#L3"),
    ("a", "href", "../pkg/", f"{BASE}/tree/main/proj/pkg"),
    ("img", "src", "../pkg/x.png", f"{BASE}/raw/main/proj/pkg/x.png"),
    ("a", "href", "../pkg/x.png", f"{BASE}/blob/main/proj/pkg/x.png"),
    ("a", "href", "../../README.md", f"{BASE}/blob/main/README.md"),
    ("a", "href", "sub/b.md#h", "sub/b.md#h"),
    ("a", "href", "https://e.com/a", "https://e.com/a"),
    ("a", "href", "#h", "#h"),
    ("a", "href", "../pkg/gone.py", "../pkg/gone.py"),
])
def test_links_out_of_docs_point_at_github(
        tree: Path, tag: str, attr: str, target: str, out: str) -> None:
    el = _element(tree, tag, attr, target)
    assert el is not None and el.get(attr) == out


def test_a_title_on_a_link_is_kept(tree: Path) -> None:
    # Literal fixture, not parsed untrusted XML.
    root = ET.fromstring('<div><a href="../pkg/m.py" title="title">x</a></div>')
    hooks.rewrite_tree(root, _page(), _config(tree))
    link = root.find("a")
    assert link is not None
    assert link.get("href") == f"{BASE}/blob/main/proj/pkg/m.py"
    assert link.get("title") == "title"


def test_a_link_to_a_missing_file_is_left_for_mkdocs_to_report(tree: Path) -> None:
    """Rewriting it would hide the broken link from --strict."""
    assert 'href="../pkg/gone.py"' in site_html(tree, "[x](../pkg/gone.py)")


def test_a_link_out_of_the_repository_warns_with_the_page(
        tree: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = ET.fromstring('<div><a href="../../../../other.md">x</a></div>')
    with caplog.at_level(logging.WARNING):
        hooks.rewrite_tree(root, _page("sub/b.md"), _config(tree))
    link = root.find("a")
    assert link is not None and link.get("href") == "../../../../other.md"
    assert "'sub/b.md'" in caplog.text and "outside the repository" in caplog.text


def test_nested_list_with_two_spaces_is_flagged() -> None:
    src = "- a\n  - b\n"
    site = "<ul><li>a</li><li>b</li></ul>"  # what Python-Markdown makes of it
    assert "depth 2 on GitHub but 1" in hooks.list_mismatch(src, site)


def test_list_run_into_a_paragraph_is_flagged() -> None:
    src = "Text:\n- a\n- b\n"
    site = "<p>Text:\n- a\n- b</p>"
    assert "'a', is not a list item on the site" in hooks.list_mismatch(src, site)


def test_matching_lists_pass() -> None:
    src = "Text:\n\n- a\n    - b\n"
    site = "<p>Text:</p><ul><li>a<ul><li>b</li></ul></li></ul>"
    assert hooks.list_mismatch(src, site) is None


def test_a_link_inside_a_fence_is_not_rewritten(tree: Path) -> None:
    src = "```\n[x](../pkg/m.py)\n```\n"
    assert "github.com" not in _render(tree, src)
    html = site_html(tree, src)
    assert "github.com" not in html and "../pkg/m.py" in html


def test_a_link_inside_inline_code_is_not_rewritten(tree: Path) -> None:
    src = "See `[x](../pkg/m.py)` before editing.\n"
    assert "github.com" not in _render(tree, src)
    assert "github.com" not in site_html(tree, src)


def test_a_link_inside_an_indented_code_block_is_not_rewritten(tree: Path) -> None:
    src = "    [x](../pkg/m.py)\n"
    assert "github.com" not in _render(tree, src)
    assert "github.com" not in site_html(tree, src)


def test_a_reference_definition_inside_a_fence_is_not_rewritten(tree: Path) -> None:
    src = "```toml\n[tool]: ../pkg/m.py\n```\n"
    assert _render(tree, src) == src
    assert "github.com" not in site_html(tree, src)


def test_a_titled_link_is_rewritten(tree: Path) -> None:
    html = site_html(tree, '[x](../pkg/m.py "title")')
    assert f'href="{BASE}/blob/main/proj/pkg/m.py"' in html
    assert 'title="title"' in html


def test_an_angle_bracket_link_is_rewritten(tree: Path) -> None:
    html = site_html(tree, "[x](<../pkg/m.py>)")
    assert f'href="{BASE}/blob/main/proj/pkg/m.py"' in html


def test_a_reference_image_uses_raw_not_blob(tree: Path) -> None:
    html = site_html(tree, "![x][pic]\n\n[pic]: ../pkg/x.png\n")
    assert f'src="{BASE}/raw/main/proj/pkg/x.png"' in html
    assert "/blob/" not in html


def test_a_linked_image_uses_raw_for_the_image_and_blob_for_the_link(tree: Path) -> None:
    html = site_html(tree, "[![x](../pkg/x.png)](../pkg/m.py)")
    assert f'src="{BASE}/raw/main/proj/pkg/x.png"' in html
    assert f'href="{BASE}/blob/main/proj/pkg/m.py"' in html


def test_a_relative_link_in_raw_html_warns(
        tree: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        _render(tree, '<a href="../pkg/m.py">x</a>\n')
    assert "raw HTML" in caplog.text and "'a.md'" in caplog.text


def test_the_rewriter_is_registered_above_mkdocs_relpath(tree: Path) -> None:
    markdown = pytest.importorskip("markdown")
    config = _config(tree)
    hooks.on_config(config)
    md = markdown.Markdown(extensions=list(config["markdown_extensions"]))
    priority = next(item.priority for item in md.treeprocessors._priority
                    if item.name == "dokimi_github_links")
    assert priority > 0  # mkdocs registers relpath at 0; higher runs first


def test_raw_html_inside_a_fence_does_not_warn(
        tree: Path, caplog: pytest.LogCaptureFixture) -> None:
    src = "```\n<a href=\"../pkg/m.py\">x</a>\n```\n"
    with caplog.at_level(logging.WARNING):
        assert _render(tree, src) == src
    assert "raw HTML" not in caplog.text

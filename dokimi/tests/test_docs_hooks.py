# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/docs/hooks.py, the mkdocs hook; mkdocs itself is not needed."""

from __future__ import annotations

import importlib.util
import logging
import sys
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


def _render(tree: Path, markdown: str, page: str = "a.md") -> str:
    config = {"docs_dir": str(tree / "docs"), "config_file_path": str(tree / "mkdocs.yml"),
              "repo_url": BASE + "/"}
    p = SimpleNamespace(file=SimpleNamespace(src_uri=page))
    return hooks.on_page_markdown(markdown, p, config, None)


@pytest.mark.parametrize(("link", "out"), [
    ("[x](../pkg/m.py#L3)", f"[x]({BASE}/blob/main/proj/pkg/m.py#L3)"),
    ("[x](../pkg/)", f"[x]({BASE}/tree/main/proj/pkg)"),
    ("![x](../pkg/x.png)", f"![x]({BASE}/raw/main/proj/pkg/x.png)"),
    ("[![x](../pkg/x.png)](../pkg/m.py)",
     f"[![x]({BASE}/raw/main/proj/pkg/x.png)]({BASE}/blob/main/proj/pkg/m.py)"),
    ("[r]: ../../README.md", f"[r]: {BASE}/blob/main/README.md"),
    ("[x](sub/b.md#h)", "[x](sub/b.md#h)"),
    ("[x](https://e.com/a)", "[x](https://e.com/a)"),
    ("[x](#h)", "[x](#h)"),
])
def test_links_out_of_docs_point_at_github(tree: Path, link: str, out: str) -> None:
    assert _render(tree, link) == out


def test_a_link_to_a_missing_file_is_left_for_mkdocs_to_report(tree: Path) -> None:
    """Rewriting it would hide the broken link from --strict."""
    assert _render(tree, "[x](../pkg/gone.py)") == "[x](../pkg/gone.py)"


def test_a_link_out_of_the_repository_warns_with_the_page(
        tree: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert _render(tree, "[x](../../../../other.md)", "sub/b.md") == "[x](../../../../other.md)"
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


def test_front_matter_list_is_not_a_page_list() -> None:
    src = "---\ndepends_on:\n  - a/b.py\n---\n<!-- header -->\n\n- a\n"
    assert hooks.list_mismatch(src, "<ul><li>a</li></ul>") is None

@pytest.mark.parametrize("text", [
    "---\r\ndepends_on:\r\n  - a/**\r\n---\r\n- a\r\n",
    "\ufeff---\ndepends_on:\n  - a/**\n---\n- a\n",
    "---\ndepends_on:\n  - a/**\n---\n- a",
    "\ufeff---\r\ndepends_on:\r\n  - a/**\r\n---\r\n- a\r\n",
])
def test_front_matter_with_crlf_or_bom_is_not_a_page_list(text) -> None:
    assert hooks.list_mismatch(text, "<ul><li>a</li></ul>") is None

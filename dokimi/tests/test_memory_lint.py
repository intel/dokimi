# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi memory lint: the reader and every rule on small memory directories (offline)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from dokimi.memory import MemoryStoreError, read_claude_memory
from dokimi.memory.lint import lint


def _entry(name: str, kind: str = "project", body: str = "fact") -> str:
    return f"---\nname: {name}\ndescription: about {name}\ntype: {kind}\n---\n\n{body}\n"


@pytest.fixture
def mem(tmp_path):
    (tmp_path / "MEMORY.md").write_text("# Index\n- [A](a.md) — hook\n- [B](b.md) — hook\n")
    (tmp_path / "a.md").write_text(_entry("a", body="See [[b]]."))
    (tmp_path / "b.md").write_text(_entry("b", "feedback"))
    return tmp_path


def _rules(path, **kw):
    return [(f.rule, f.severity, f.file) for f in lint(read_claude_memory(path), **kw)]


def test_clean_store_has_no_findings(mem):
    assert _rules(mem) == []


def test_dead_link_fails_and_unlinked_file_is_orphan(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md)\n- [Gone](gone.md)\n")
    assert ("index-dead-link", "fail", "MEMORY.md") in _rules(mem)
    assert ("index-orphan", "warn", "b.md") in _rules(mem)


def test_archive_is_read_but_never_orphan(mem):
    (mem / "archive").mkdir()
    (mem / "archive" / "old.md").write_text(_entry("old"))
    store = read_claude_memory(mem)
    assert [e.path for e in store.entries if e.archived] == ["archive/old.md"]
    assert _rules(mem) == []


def test_frontmatter_rules(mem):
    (mem / "c.md").write_text("no frontmatter\n")
    (mem / "d.md").write_text("---\nname: d\ndescription: x: y: z\n---\n")
    (mem / "e.md").write_text("---\nname: a\ndescription: dup\nmetadata:\n  type: diary\n---\n")
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [C](c.md) [D](d.md) [E](e.md)\n")
    rules = {(r, f) for r, _, f in _rules(mem)}
    assert {("frontmatter-missing", "c.md"), ("frontmatter-invalid", "d.md"), ("type-unknown", "e.md"),
            ("name-duplicate", "a.md"), ("name-duplicate", "e.md")} <= rules


def test_wikilink_folds_dash_and_underscore(mem):
    (mem / "a.md").write_text(_entry("a", body="[[B]] [[project-a]] [[nobody]]"))
    (mem / "project_a.md").write_text(_entry("pa"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [P](project_a.md)\n")
    dangling = [f.message for f in lint(read_claude_memory(mem)) if f.rule == "wikilink-dangling"]
    assert len(dangling) == 1 and "nobody" in dangling[0]


def test_relative_date_flagged_unless_dated(mem):
    (mem / "a.md").write_text(_entry("a", body="Merged today.\nDone yesterday (as of ≤2026-11-01).\nСделано вчера."))
    found = [f for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"]
    assert [f.line for f in found] == [7, 9]


def test_index_limit_and_stray_separator(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md), [B](b.md)\n- Group:, [x](a.md)\n" + "line\n" * 10)
    rules = _rules(mem, max_lines=10)
    assert ("index-over-limit", "fail", "MEMORY.md") in rules
    assert [r for r in rules if r[0] == "index-stray-separator"] == [("index-stray-separator", "note", "MEMORY.md")]
    assert ("index-near-limit", "warn", "MEMORY.md") in _rules(mem, max_lines=14)


def test_missing_index_warns(tmp_path):
    (tmp_path / "a.md").write_text(_entry("a"))
    assert _rules(tmp_path) == [("index-missing", "warn", "MEMORY.md")]


def test_not_a_memory_dir(tmp_path):
    with pytest.raises(MemoryStoreError):
        read_claude_memory(tmp_path)
    with pytest.raises(MemoryStoreError):
        read_claude_memory(tmp_path / "nope")


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "dokimi.cli", "memory", "lint", *args],
                          capture_output=True, text=True, check=False)


def test_cli_exit_codes_and_json(mem):
    out = _cli(str(mem), "--json")
    doc = json.loads(out.stdout)
    assert out.returncode == 0 and doc["kind"] == "memory lint" and doc["data"]["counts"]["fail"] == 0
    (mem / "c.md").write_text("orphan, no frontmatter\n")
    assert _cli(str(mem)).returncode == 0
    assert _cli(str(mem), "--fail-on", "warn").returncode == 1
    (mem / "MEMORY.md").write_text("- [Gone](gone.md)\n")
    out = _cli(str(mem))
    assert out.returncode == 1 and "index-dead-link" in out.stdout
    bad = _cli(str(mem / "nope"), "--json")
    assert bad.returncode == 2 and json.loads(bad.stdout)["error"]["code"] == "input"

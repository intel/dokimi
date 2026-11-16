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
    severities = {(r, sev) for r, sev, _ in _rules(mem)}
    assert {("type-unknown", "warn"), ("name-duplicate", "warn")} <= severities


@pytest.mark.parametrize("value", ["modified: 2026-02-30", "x: !!float abc", "x: !!timestamp abc",
                                   "x: " + "[" * 3000 + "]" * 3000])
def test_bad_frontmatter_value_is_a_finding_and_the_run_goes_on(mem, value):
    (mem / "b.md").write_text(f"---\nname: b\ndescription: d\ntype: project\n{value}\n---\n")
    (mem / "c.md").write_text(_entry("c"))
    rules = _rules(mem)
    assert ("frontmatter-invalid", "warn", "b.md") in rules and ("index-orphan", "warn", "c.md") in rules


def test_links_in_code_and_comments_are_not_index_links(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md), write `[x](span.md)`\n<!-- [old](c1.md)\n"
                                   "[old](c2.md) -->\n```md\n[x](fenced.md)\n~~~\n```\n- [Gone](gone.md)\n")
    dead = [(f.line, f.message) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"]
    assert dead == [(8, "links gone.md, which is not a file")]


def test_parenthesised_targets_and_reference_definitions_are_links(mem):
    (mem / "topic(v2).md").write_text(_entry("t"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [T](topic(v2).md) [B][b] [G][g]\n\n[b]: b.md\n[g]: <gone.md>\n")
    dead = [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.severity != "note"]
    assert dead == [("index-dead-link", 4)]


def test_containment_is_judged_after_resolving(tmp_path):
    mem = tmp_path / "m"
    mem.mkdir()
    (tmp_path / "outside.md").write_text(_entry("o"))
    (mem / "a.md").write_text(_entry("a"))
    (mem / "esc.md").symlink_to(tmp_path / "outside.md")
    (mem / "up").symlink_to(tmp_path, target_is_directory=True)
    (mem / "loop1.md").symlink_to(mem / "loop2.md")
    (mem / "loop2.md").symlink_to(mem / "loop1.md")
    (mem / "MEMORY.md").write_text(f"- [A]({mem / 'a.md'})\n- [E](esc.md)\n- [U](up/outside.md)\n"
                                   f"- [O]({tmp_path / 'outside.md'})\n- [L](loop1.md)\n")
    found = [(f.rule, f.severity, f.file, f.line) for f in lint(read_claude_memory(mem))
             if f.rule.startswith("index")]
    assert found == [("index-dead-link", "fail", "MEMORY.md", n) for n in (2, 3, 4, 5)] + [
        ("index-orphan", "warn", "esc.md", None)]


def test_wikilink_folds_dash_and_underscore(mem):
    (mem / "a.md").write_text(_entry("a", body="[[B]] [[project-a]] [[nobody]]"))
    (mem / "project_a.md").write_text(_entry("pa"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [P](project_a.md)\n")
    dangling = [(f.severity, f.message) for f in lint(read_claude_memory(mem)) if f.rule == "wikilink-dangling"]
    assert len(dangling) == 1 and dangling[0][0] == "note" and "nobody" in dangling[0][1]


def test_relative_date_flagged_unless_dated(mem):
    (mem / "a.md").write_text(_entry("a", body="Merged today.\nDone yesterday (as of ≤2026-11-01).\nСделано вчера."))
    found = [f for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"]
    assert [(f.line, f.severity) for f in found] == [(7, "warn"), (9, "warn")]


def test_index_limit_and_stray_separator(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md), [B](b.md)\n- Group:, [x](a.md)\n" + "line\n" * 10)
    rules = _rules(mem, max_lines=10)
    assert ("index-over-limit", "fail", "MEMORY.md") in rules
    assert [r for r in rules if r[0] == "index-stray-separator"] == [("index-stray-separator", "note", "MEMORY.md")]
    assert ("index-near-limit", "warn", "MEMORY.md") in _rules(mem, max_lines=14)


def test_load_limit_edges_anchors_mailto_and_code_span_dates(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md#why) [B](b.md) [M](mailto:x@example.com)\n" + "x\n" * 199)
    assert [r[0] for r in _rules(mem)] == ["index-near-limit"]  # 200 lines load
    (mem / "MEMORY.md").write_text("- [A](a.md#why) [B](b.md)\n" + "x\n" * 200)
    assert [r[0] for r in _rules(mem)] == ["index-over-limit"]
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n" + "x" * 900 + "\n")
    assert [r[0] for r in _rules(mem, max_bytes=1000)] == ["index-near-limit"]
    (mem / "a.md").write_text(_entry("a", body="Run `make today` first."))
    assert _rules(mem) == []


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


def test_link_forms_resolve_and_escapes_are_dead(mem):
    (mem / "my file.md").write_text(_entry("mf"))
    (mem / "sub").mkdir()
    (mem / "MEMORY.md").write_text('- [A](a.md "title") [B](<b.md>) [M](my%20file.md) [N](./sub/../a.md)\n'
                                   "- [Up](../x.md) [Dir](sub)\n")
    found = _rules(mem)
    assert [r for r in found if r[0] == "index-orphan"] == []
    assert [r[0] for r in found].count("index-dead-link") == 2


def test_index_size_counts_crlf_bytes_on_disk(mem):
    (mem / "MEMORY.md").write_bytes(b"- [A](a.md) [B](b.md)\r\n" + b"x" * 98 + b"\r\n")
    store = read_claude_memory(mem)
    on_disk = (mem / "MEMORY.md").stat().st_size
    assert lint(store, max_bytes=on_disk - 1)[0].rule == "index-over-limit"  # LF text is 2 bytes shorter
    assert lint(store, max_bytes=on_disk - 1)[0].message.startswith(f"2 lines / {on_disk} bytes")


def test_bom_alias_archive_and_date_false_positives(mem):
    (mem / "a.md").write_text("﻿" + _entry("a", body="[[b|alias]] [[b#Why]]\nFixed 2026-11-13 (today).\n"
                                                 "See `today()` and yesterday.md."))
    (mem / "archive").mkdir()
    (mem / "archive" / "a.md").write_text(_entry("a", body="done today"))
    assert _rules(mem) == []


def test_unreadable_file_is_a_finding(mem):
    import os
    bad = mem / "b.md"
    bad.chmod(0)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("running as root: every file is readable")
        assert ("frontmatter-invalid", "warn", "b.md") in _rules(mem)
    finally:
        bad.chmod(0o644)


def test_unreadable_index_is_an_input_error(mem):
    import os
    index = mem / "MEMORY.md"
    index.chmod(0)
    try:
        if os.access(index, os.R_OK):
            pytest.skip("running as root: every file is readable")
        out = _cli(str(mem), "--json")
        assert out.returncode == 2 and json.loads(out.stdout)["error"]["code"] == "input"
    finally:
        index.chmod(0o644)


def test_json_status_is_failed_on_a_fail(mem):
    (mem / "MEMORY.md").write_text("- [Gone](gone.md)\n")
    doc = json.loads(_cli(str(mem), "--json").stdout)
    assert doc["status"] == "failed" and "1 fail" in doc["reason"]


def test_empty_frontmatter_stops_at_its_own_closing_line(mem):
    (mem / "a.md").write_text("---\n---\nSaw it today.\n\n---\nname: later\n---\n")
    found = [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.file == "a.md"]
    assert ("relative-date", 3) in found and ("frontmatter-invalid", 1) not in found


def test_external_links_are_not_checked(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [D](https://example.com/gone.md)\n")
    assert _rules(mem) == []


def test_index_byte_limit_edge_with_default_limits(mem):
    line = "- [A](a.md) [B](b.md) "
    (mem / "MEMORY.md").write_text(line + "x" * (25600 - len(line) - 1) + "\n")  # exactly 25600 bytes loads
    assert [r[0] for r in _rules(mem)] == ["index-near-limit"]
    (mem / "MEMORY.md").write_text(line + "x" * (25601 - len(line) - 1) + "\n")
    assert [r[0] for r in _rules(mem)] == ["index-over-limit"]


def test_escaped_destination_characters_are_decoded(mem):
    (mem / "topic(v2).md").write_text(_entry("t"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [T](topic\\(v2\\).md)\n")
    assert _rules(mem) == []


def test_unused_reference_definition_is_not_a_link(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n[old]: deleted.md\n")
    assert _rules(mem) == []
    (mem / "MEMORY.md").write_text("- [A][t] [B](b.md)\n\n[t]: gone.md\n[u]: deleted.md\n")
    assert [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [
        ("index-dead-link", 3)]


def test_nul_in_a_link_is_a_dead_link_not_a_crash(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [N](x%00.md)\n")
    assert [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [
        ("index-dead-link", 1)]


def test_relative_date_in_fence_or_comment_is_not_flagged(mem):
    (mem / "a.md").write_text(_entry("a", body="```\nrun it tomorrow\n```\n<!--\nsee today\n-->\nDone."))
    assert [f for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == []


def test_shortcut_reference_followed_by_colon_is_a_use(mem):
    (mem / "MEMORY.md").write_text("- [A]: the a file\n- [B](b.md)\n\n[A]: gone.md\n")
    assert [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [
        ("index-dead-link", 4)]


def test_unpaired_escaped_paren_is_read_as_a_target(mem):
    (mem / "topic(v2.md").write_text(_entry("t"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [T](topic\\(v2.md)\n")
    assert _rules(mem) == []


def test_reference_labels_fold_case_as_markdown_does(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [X][STRASSE]\n\n[straße]: gone.md\n")
    assert [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [
        ("index-dead-link", 3)]


def test_bare_line_with_words_after_destination_is_not_a_definition(mem):
    (mem / "MEMORY.md").write_text("[A]: the a file\n- [B](b.md)\n\n[A]: gone.md\n")
    assert [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [
        ("index-dead-link", 4)]


def test_inline_link_with_invalid_destination_leaves_the_label_for_a_shortcut(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n- see [t](more below)\n\n[t]: gone.md\n")
    assert [(f.rule, f.line) for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [
        ("index-dead-link", 4)]


def test_backslash_space_is_not_a_link_destination(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [W](notes\\ x.md)\n")
    assert _rules(mem) == []
def test_empty_inline_destination_leaves_the_label_as_a_use(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [t]()\n\n[t]: gone.md\n")
    assert [f for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_first_definition_of_a_label_is_the_one_checked(mem):
    (mem / "MEMORY.md").write_text("- [A][t] [B](b.md)\n\n[t]: a.md\n[t]: gone.md\n")
    assert [f for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_escaped_hash_is_part_of_the_name_not_an_anchor(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [S](a.md\\#sec)\n")
    assert [f for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_label_inside_a_link_title_is_not_a_use(mem):
    (mem / "MEMORY.md").write_text('- [A](a.md "see [t]") [B](b.md)\n\n[t]: gone.md\n')
    assert [f for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []
def test_fence_opener_with_a_backtick_in_its_info_is_a_code_span(mem):
    (mem / "a.md").write_text(_entry("a", body="```make ci``` runs the gate.\nShip it tomorrow.\n"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [8]


def test_definition_title_may_hold_an_escaped_quote(mem):
    (mem / "MEMORY.md").write_text("- [B](b.md) [T][t]\n\n[t]: gone.md \"a \\\" b\"\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [3]
def test_definition_title_may_run_onto_the_next_line(mem):
    (mem / "MEMORY.md").write_text('- [A](a.md) [B](b.md) [T][t]\n\n[t]: gone.md "a long\ntitle"\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [3]


def test_backslash_space_inside_a_definition_title(mem):
    (mem / "MEMORY.md").write_text('- [A](a.md) [B](b.md) [T][t]\n\n[t]: gone.md "C:\\ dir"\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [3]


def test_label_wrapped_across_a_line_break_is_a_use(mem):
    (mem / "MEMORY.md").write_text("- see [some\nlabel] [A](a.md) [B](b.md)\n\n[some label]: gone.md\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [4]


def test_definitions_only_index_links_its_targets(mem):
    (mem / "MEMORY.md").write_text("[a]: a.md\n[b]: b.md\n")
    assert [r for r in _rules(mem) if r[0] == "index-orphan"] == []


def test_comment_text_inside_a_fence_is_not_a_comment(mem):
    (mem / "a.md").write_text(_entry("a", body="```html\n<!-- start\n```\nShip it tomorrow.\nend -->\n"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [10]


def test_a_fence_inside_a_comment_does_not_hide_the_rest_of_the_index(mem):
    (mem / "MEMORY.md").write_text("<!-- old deploy notes\n```bash\nmake deploy\n```-->\n- [A](a.md) [G](gone.md)\n")
    (mem / "a.md").write_text(_entry("a"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [5]


def test_a_comment_that_never_closes_hides_nothing(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) <!-- aside\n- [G](gone.md)\n")
    (mem / "a.md").write_text(_entry("a"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [2]


def test_labels_made_of_code_spans_are_told_apart(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) see [`x`][] and [`y`][]\n\n[`x`]: a.md\n[`y`]: gone.md\n")
    (mem / "a.md").write_text(_entry("a"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [4]


def test_an_inline_comment_ends_at_a_blank_line(mem):
    (mem / "a.md").write_text(_entry("a", body="Use <!-- to open\n\nShip it tomorrow.\n\nflow: a --> b\n"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [9]


def test_an_inline_comment_ends_at_a_fence_it_does_not_hide_the_text_after_it(mem):
    (mem / "a.md").write_text(_entry("a", body="note <!-- aside\n```mermaid\nA --> B\n```\nShip it tomorrow.\n"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [11]


def test_an_empty_comment_closes_on_its_own_line(mem):
    (mem / "a.md").write_text(_entry("a", body="<!-->\nShip it tomorrow.\nflow: a --> b\n"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [8]


def test_a_fence_inside_a_comment_does_not_hide_the_rest_of_a_topic(mem):
    (mem / "a.md").write_text(_entry("a", body="<!-- notes\n```bash\nx\n```-->\nShip it tomorrow.\n"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [11]


def test_escaped_parentheses_and_null_byte_targets(mem):
    (mem / "topic(v2).md").write_text(_entry("t"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [T](topic\\(v2\\).md)\n- [N](x%00.md)\n")
    found = [(f.rule, f.line) for f in lint(read_claude_memory(mem))]
    assert found == [("index-dead-link", 2)]


def test_code_spans_and_comments_hide_links_by_the_backtick_run_length(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n``Use `x` and [example](missing.md)``\n"
                                   "`<!--` [Gone](gone.md) `-->`\n")
    dead = [(f.rule, f.line) for f in lint(read_claude_memory(mem))]
    assert dead == [("index-dead-link", 3)]


def test_a_backtick_inside_a_comment_does_not_pair_with_one_after_it(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md)\n<!-- a ` --> [X](missing.md) `\n- [Y](gone.md)\n<!-- z -->\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [2, 3]


def test_a_comment_closes_at_its_first_arrow_even_inside_backticks(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md)\n<!-- a `-->` [X](missing.md) -->\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [2]


def test_relative_date_skips_fences_and_comments(mem):
    (mem / "a.md").write_text(_entry("a", body="```\nrun it tomorrow\n```\n<!-- today -->\nDo it tomorrow."))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [11]


def test_outside_link_fails_urls_are_skipped_and_byte_limit_edge(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [U](https://example.com/x.md) [Up](../x.md)\n")
    assert [(f.rule, f.severity) for f in lint(read_claude_memory(mem))] == [("index-dead-link", "fail")]
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n")
    size = (mem / "MEMORY.md").stat().st_size
    assert "index-over-limit" not in [r[0] for r in _rules(mem, max_bytes=size)]
    assert ("index-over-limit", "fail", "MEMORY.md") in _rules(mem, max_bytes=size - 1)


def test_comment_opener_in_a_fence_does_not_hide_the_prose_after_it(mem):
    (mem / "a.md").write_text(_entry("a", body="```html\n<!--\n```\nDo it tomorrow.\n<!-- end -->"))
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == [10]


def test_a_store_with_only_notes_passes_under_fail_on_warn(mem):
    (mem / "a.md").write_text(_entry("a", body="See [[nowhere]]."))
    assert [(r, sev) for r, sev, _ in _rules(mem)] == [("wikilink-dangling", "note")]
    assert _cli(str(mem), "--fail-on", "warn").returncode == 0


def _dead(mem, text):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n" + text)
    return [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"]


@pytest.mark.parametrize("target", ["tel:+123456789", "MAILTO:x@example.com", "HTTPS:example.com/x.md", "data:text/plain,x"])
def test_uri_schemes_are_not_checked_whatever_their_case(mem, target):
    assert _dead(mem, f"[C]({target})\n") == []


def test_a_one_letter_scheme_is_a_drive_path(mem):
    assert _dead(mem, "[C](C:/dir/x.md)\n") == [2]


def test_character_references_in_a_destination_are_decoded(mem):
    (mem / "a&b.md").write_text(_entry("ab"))
    assert _rules_after(mem, "[T](a&amp;b.md)\n") == []
    assert _rules_after(mem, "[T][t]\n\n[t]: a&amp;b.md\n") == []


def _rules_after(mem, text):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n" + text)
    return [(f.rule, f.line) for f in lint(read_claude_memory(mem))]


def test_explicit_reference_uses_only_its_label(mem):
    assert _dead(mem, "[Display][a]\n\n[a]: a.md\n[Display]: missing.md\n") == []
    assert _dead(mem, "[Display][]\n\n[Display]: missing.md\n") == [4]
    assert _dead(mem, "[Display]\n\n[Display]: missing.md\n") == [4]


def test_unterminated_definition_title_is_not_a_definition(mem):
    assert _dead(mem, '[t]\n\n[t]: missing.md "unterminated\n') == []
    assert _dead(mem, "[t]\n\n[t]: missing.md (unterminated\n") == []
    assert _dead(mem, '[t]\n\n[t]: missing.md "closed on\nthe next line"\n') == [4]


def test_link_destination_on_the_next_line_is_checked(mem):
    assert _dead(mem, "[Missing](\nmissing.md)\n") == [2]
    assert _dead(mem, "[Missing](missing.md\n)\n") == [2]
    assert _dead(mem, "[Missing](\n\nmissing.md)\n") == []


def test_indented_code_block_hides_its_link(mem):
    assert _dead(mem, "# Notes\n\n    [Example](missing.md)\n") == []
    assert _dead(mem, "# Notes\n\n\t[Example](missing.md)\n\n    [Again](missing.md)\n") == []
    assert _dead(mem, "# Notes\ntext\n    [Example](missing.md)\n") == [4]  # a paragraph continues
    assert _dead(mem, "\n- item\n\n    [Example](missing.md)\n") == [5]  # list item content
    assert _dead(mem, "# Notes\n\n    [Example](missing.md)\n\ntext [Real](missing.md)\n") == [6]


def test_an_escaped_opener_is_literal_text(mem):
    assert _dead(mem, "\\[Example](missing.md)\n") == []
    assert _dead(mem, "\\\\[Example](missing.md)\n") == [2]  # an escaped backslash leaves the bracket live
    assert _dead(mem, "[Example\\](missing.md)\n") == []
    assert _dead(mem, "[a [b]](missing.md)\n") == [2]


def test_code_span_across_a_line_break_hides_its_link(mem):
    assert _dead(mem, "Example: `start\n[Example](missing.md)\nend`\n") == []
    assert _dead(mem, "``start\n[x](missing.md)\nend``\n") == []
    assert _dead(mem, "`start\n[x](missing.md)\n\nend`\n") == [3]  # a blank line ends the paragraph
    assert _dead(mem, "`start\n[x](missing.md)\n") == [3]
    (mem / "a.md").write_text(_entry("a", body="Run `start\ntomorrow\nend` first."))
    assert [f for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == []


def test_a_comment_opening_a_line_with_no_close_hides_the_rest(mem):
    assert _dead(mem, "\n<!-- retired\n[Old](missing.md)\n") == []
    assert _dead(mem, "\n<!--\n- [Old](missing.md)\n\n[Old](missing.md)\n") == []


def test_an_inline_comment_ends_at_the_next_list_item(mem):
    (mem / "MEMORY.md").write_text(
        "- [A](a.md) <!-- see\n- [D](dead1.md)\n- [E](dead2.md)\n\ntext\n\n<!-- other -->\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [2, 3]
    (mem / "MEMORY.md").write_text("- [A](a.md) <!-- see\ntext [D](dead1.md) -->\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_an_undefined_second_label_leaves_the_definition_unused(mem):
    assert _dead(mem, "[t][undefined]\n\n[t]: missing.md\n") == []  # a renderer prints it as text


def test_inline_destination_on_the_next_line_is_not_a_label_use(mem):
    assert _dead(mem, "[t](\na.md)\n\n[t]: missing.md\n") == []
    assert _dead(mem, "[t]\n\n[t]: missing.md\n") == [4]


def test_definition_title_may_run_over_many_lines(mem):
    assert _dead(mem, '[t]\n\n[t]: missing.md "title\\ncontinues\\nhere"\n'.replace("\\n", "\n")) == [4]
    assert _dead(mem, '[t]\n\n[t]: missing.md "title\n\nhere"\n') == []


def test_an_entity_without_a_semicolon_is_literal(mem):
    (mem / "notes&copy.md").write_text(_entry("n"))
    assert _rules_after(mem, "[Notes](notes&copy.md)\n") == []
    assert _dead(mem, "[N](notes&copy;.md)\n") == [2]  # with the semicolon it is a copyright sign


def test_an_escaped_backtick_opens_no_code_span(mem):
    assert _dead(mem, "Literal \\`start\n[Broken](missing.md)\nend`\n") == [3]
    assert _dead(mem, "Literal \\`[Broken](missing.md)` text\n") == [2]
    assert _dead(mem, "Literal \\\\`start\n[Broken](missing.md)\nend`\n") == []  # an escaped backslash leaves it live


def test_link_title_in_parentheses_is_part_of_the_link(mem):
    assert _dead(mem, "[Gone](missing.md (retired))\n") == [2]


def test_definition_destination_may_be_on_the_next_line(mem):
    assert _dead(mem, "[Gone][g]\n\n[g]:\n  missing.md\n") == [4]


def test_four_space_indented_list_item_after_a_blank_line_is_code(mem):
    (mem / "MEMORY.md").write_text("[A](a.md) [B](b.md)\n\n    - [Example](missing.md)\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_definition_on_two_lines_with_an_unterminated_title_is_no_definition(mem):
    (mem / "MEMORY.md").write_text('[A](a.md) [B](b.md) [g]\n\n[g]:\n  missing.md "unterminated\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_label_in_the_title_of_a_two_line_definition_is_not_a_use(mem):
    (mem / "MEMORY.md").write_text('[A](a.md) [B](b.md) [G][g]\n\n[g]:\n  a.md "see [t]"\n[t]: missing.md\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_link_in_a_definition_title_is_text(mem):
    (mem / "MEMORY.md").write_text('[A][a]\n\n[a]: a.md "[Example](missing.md)"\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_backticks_in_an_angle_destination_are_literal(mem):
    (mem / "topic`v2`.md").write_text(_entry("t"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md) [Topic](<topic`v2`.md>)\n")
    assert _rules(mem) == []


def test_backtick_in_an_angle_destination_opens_no_span_on_the_next_line(mem):
    (mem / "topic`v2`.md").write_text(_entry("t"))
    (mem / "MEMORY.md").write_text("- [A](a.md)\n- [Topic](<topic`v2`.md>)\nsee `[Y](missing.md)` ok\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_code_span_after_an_angle_destination_is_still_code(mem):
    (mem / "a`b.md").write_text(_entry("ab"))
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](<a`b.md>) see `[X](missing.md)`\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_a_span_in_a_destination_that_is_no_link_is_still_code(mem):
    (mem / "MEMORY.md").write_text("- [A](a.md) [B](b.md)\n- [T](<a `[Y](missing.md)` b> c\n")
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_text_after_a_closed_title_leaves_the_link_live(mem):
    (mem / "MEMORY.md").write_text('- [A](a.md) [B](b.md) [a]\n\n[a]: a.md "x" see [Y](missing.md)\nsaid "hi"\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == [3]


def test_link_in_a_title_on_the_next_line_is_text(mem):
    (mem / "MEMORY.md").write_text('[A][g]\n\n[g]:\n  a.md "[X](missing.md)"\n')
    assert [f.line for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_backticks_after_an_angle_without_an_opener_are_code(mem):
    (mem / "a.md").write_text(_entry("a", body="x](<q `tomorrow` r>) z"))
    assert [f.rule for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == []


def test_backticks_in_an_angle_destination_stay_literal_when_the_opener_is_above(mem):
    (mem / "MEMORY.md").write_text("- [a\n  b](<topic`v2`.md>)\n")
    (mem / "topic`v2`.md").write_text(_entry("topic"))
    assert [f.rule for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_an_opener_inside_a_code_span_above_does_not_open_the_destination(mem):
    (mem / "a.md").write_text(_entry("a", body="`[`\nx](<q `tomorrow` r>) z"))
    assert [f.rule for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == []


def test_a_whitespace_line_ends_the_paragraph_for_an_angle_opener(mem):
    (mem / "a.md").write_text(_entry("a", body="[\n   \nx](<q `tomorrow` r>) z"))
    assert [f.rule for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == []


def test_a_line_blanked_to_spaces_does_not_end_the_angle_paragraph(mem):
    (mem / "MEMORY.md").write_text("- [\n  `x`\n  b](<topic`v2`.md>)\n")
    (mem / "topic`v2`.md").write_text(_entry("topic"))
    assert [f.rule for f in lint(read_claude_memory(mem)) if f.rule == "index-dead-link"] == []


def test_a_fence_ends_the_paragraph_of_an_angle_opener(mem):
    (mem / "a.md").write_text(_entry("a", body="[\n```\nc\n```\nx](<q `tomorrow` r>) z"))
    assert [f.rule for f in lint(read_claude_memory(mem)) if f.rule == "relative-date"] == []

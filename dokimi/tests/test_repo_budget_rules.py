# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget` and `.claude/rules`: when each rule loads, and what of it is counted."""

from __future__ import annotations

import pytest

from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return WorkTree(tmp_path)


def _by_path(payload):
    return {(e["path"], e["kind"]): e for e in payload["entries"]}


@pytest.mark.outcome("cjm-repo-budget")
def test_rules_start_at_the_root_unless_scoped_and_lazily_in_a_subdirectory(tmp_path):
    # Measured on Claude Code 2.1.304: a root rule raised the first prompt by its size; a scoped one,
    # a nested one and a rule's @path did not (scratch probe, haiku, first-call prompt tokens).
    scoped = "---\npaths:\n  - \"src/**\"\n---\nscoped body\n"
    payload = budget(_tree(tmp_path, {
        ".claude/rules/plain.md": "plain body\n",
        ".claude/rules/deep/x.md": "deep body\n",
        ".claude/rules/scoped.md": scoped,
        ".claude/rules/bad.md": "---\npaths: [unclosed\n---\nbad yaml body\n",
        ".claude/rules/imp.md": "See @extra.md\n",
        "extra.md": "never loaded\n",
        ".claude/rules/notes.txt": "not a rule\n",
        "sub/.claude/rules/n.md": "nested body\n",
    }), NAMES, rules=True)
    got = _by_path(payload)
    rules = {p: (e["when"], e["chars"]) for (p, k), e in got.items() if k == "rule"}
    assert rules == {
        ".claude/rules/plain.md": ("start", len("plain body\n")),
        ".claude/rules/deep/x.md": ("start", len("deep body\n")),
        ".claude/rules/imp.md": ("start", len("See @extra.md\n")),
        # the frontmatter is removed before the rule loads; `paths` holds it back even unparsed
        ".claude/rules/scoped.md": ("lazy", len("scoped body\n")),
        ".claude/rules/bad.md": ("lazy", len("bad yaml body\n")),
        "sub/.claude/rules/n.md": ("lazy", len("nested body\n")),
    }
    assert ("extra.md", "import") not in got
    assert payload["totals"]["start"] == len("plain body\n") + len("deep body\n") + len("See @extra.md\n")
    assert payload["warnings"] == []


def test_a_rule_does_not_turn_agents_md_off(tmp_path):
    got = _by_path(budget(_tree(tmp_path, {"AGENTS.md": "root\n", ".claude/rules/r.md": "rule\n"}), NAMES, rules=True))
    assert got[("AGENTS.md", "guidance")]["when"] == "start"
    assert got[(".claude/rules/r.md", "rule")]["when"] == "start"


def test_a_rule_a_root_claude_md_imports_is_one_start_entry(tmp_path):
    got = _by_path(budget(_tree(tmp_path, {
        "CLAUDE.md": "@.claude/rules/api.md\n",
        ".claude/rules/api.md": "---\npaths: src/**\n---\napi\n",
    }), NAMES, rules=True))
    assert got[(".claude/rules/api.md", "rule")]["when"] == "start"
    assert (".claude/rules/api.md", "import") not in got


def test_a_root_rule_linked_to_an_off_agents_md_loads_it_at_start(tmp_path):
    # a root CLAUDE.md turns AGENTS.md off, but the rule links to the same bytes and loads them at start
    _tree(tmp_path, {"CLAUDE.md": "claude\n", "AGENTS.md": "agents\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/agents.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[("AGENTS.md", "guidance")]["when"] == "start"
    assert not [e for e in payload["entries"] if e["when"] == "off"]
    assert payload["warnings"] == []
    assert payload["totals"]["start"] == len("claude\n") + len("agents\n")


def test_a_root_rule_linked_to_a_nested_guidance_file_loads_it_at_start(tmp_path):
    _tree(tmp_path, {"CLAUDE.md": "root\n", "sub/CLAUDE.md": "nested\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/nested.md").symlink_to(tmp_path / "sub/CLAUDE.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[("sub/CLAUDE.md", "guidance")]["when"] == "start"
    assert payload["totals"]["start"] == len("root\n") + len("nested\n")
    assert payload["totals"]["lazy"] == 0


def test_an_unreadable_rule_is_counted_as_zero_and_named(tmp_path):
    (tmp_path / ".claude/rules").mkdir(parents=True)
    (tmp_path / ".claude/rules/bin.md").write_bytes(b"\xff\xfe\x00")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/bin.md", "rule")]["chars"] == 0
    assert payload["warnings"] == [".claude/rules/bin.md: not readable (non-UTF-8 or binary), counted as 0"]


def test_rules_are_counted_only_for_a_harness_that_reads_them(tmp_path):
    from dokimi.repos.harness_profile import load_profile

    tree = _tree(tmp_path, {"AGENTS.md": "root\n", ".claude/rules/r.md": "rule\n"})
    for name in ("codex", "copilot"):
        profile = load_profile(name)
        assert not profile.reads_claude_rules
        got = budget(tree, profile.guidance_names, profile.import_max_depth, profile.reads_claude_rules)
        assert not [e for e in got["entries"] if e["kind"] == "rule"], name
    assert load_profile("claude-code").reads_claude_rules


def test_a_profile_that_does_not_read_rules_still_counts_a_rules_file_as_guidance_and_imports(tmp_path):
    # Without rules, .claude/rules/AGENTS.md is an ordinary guidance file, and an @import through a
    # .claude/rules file is followed (the rule exclusions apply only to a harness that reads rules).
    from dokimi.repos.harness_profile import load_profile

    profile = load_profile("codex")
    tree = _tree(tmp_path, {
        "AGENTS.md": "root @.claude/rules/mid.md\n",
        ".claude/rules/AGENTS.md": "nested guidance\n",
        ".claude/rules/mid.md": "mid @deep.md\n",
        ".claude/rules/deep.md": "deep body\n",
    })
    got = _by_path(budget(tree, profile.guidance_names, profile.import_max_depth, False))
    assert ((".claude/rules/AGENTS.md", "guidance") in got)
    assert ((".claude/rules/mid.md", "import") in got)
    assert ((".claude/rules/deep.md", "import") in got)


@pytest.mark.parametrize(("front", "when"), [
    ('paths:\n  - "src/**"\n', "lazy"),
    ("paths: src/**, lib/**\n", "lazy"),
    ('paths: "src/**"\nfoo: [unclosed\n', "lazy"),  # measured: the key is read line by line
    ('paths: [\n"src/**"]\nunused: [unclosed\n', "lazy"),  # an unindented continuation of an open flow list
    ("foo: [unclosed\n", "start"),  # measured: no paths, so it loads at start
    ("paths: []\n", "start"),  # measured: an empty list scopes nothing
    ('paths: ""\n', "start"),
    ("paths:\n", "start"),
    ("paths: null\n", "start"),
    ("paths: # none yet\n", "start"),
    ('paths: [\n  "src/**"]\n', "lazy"),
    ('paths:\n  # the api\n  - "src/**"\n', "lazy"),
    pytest.param("paths: " + "[" * 3000 + "]" * 3000 + "\n", "start", id="too-deep-for-yaml"),
    pytest.param("unused: " + "9" * 5000 + "\n", "start", id="oversized-integer-unrelated-to-paths"),
    # the same for YAML that does not parse: the fallback reads the key line by line
    pytest.param("paths: # none yet\nfoo: [unclosed\n", "start", id="unparsed-comment-only-value"),
    pytest.param("paths: [] # none\nfoo: [unclosed\n", "start", id="unparsed-empty-list-and-comment"),
    pytest.param('paths:\n  # the api\n  - "src/**"\nfoo: [unclosed\n', "lazy",
                 id="unparsed-comment-line-before-item"),
    pytest.param('paths:\n\n  - "src/**"\nfoo: [unclosed\n', "lazy", id="unparsed-blank-line-before-item"),
    pytest.param('paths: "src/#x" # the api\nfoo: [unclosed\n', "lazy", id="unparsed-quoted-hash-kept"),
    # null and empty items name no pattern, with or without unrelated malformed YAML
    pytest.param("paths: null\nunused: [unclosed\n", "start", id="unparsed-null-unrelated-malformed"),
    pytest.param('paths: ["", ""]\nunused: [unclosed\n', "start", id="unparsed-empty-items-unrelated-malformed"),
    pytest.param('paths: ["", ""]\n', "start", id="parsed-empty-items"),
    # a value that is not a string names no pattern (not measured either way)
    pytest.param("paths: true\n", "start", id="paths-boolean"),
    pytest.param("paths: 5\n", "start", id="paths-integer"),
    pytest.param("paths: {a: 1}\n", "start", id="paths-mapping"),
    # the key is read on its own when the rest of the frontmatter does not parse
    pytest.param("paths: true\nunused: [unclosed\n", "start", id="unparsed-boolean-unrelated-malformed"),
    pytest.param("paths: 5\nunused: [unclosed\n", "start", id="unparsed-integer-unrelated-malformed"),
    pytest.param('paths: [\n  "src/**"]\nunused: [unclosed\n', "lazy", id="unparsed-flow-list-over-lines"),
    pytest.param('paths: [\n  ""]\nunused: [unclosed\n', "start", id="unparsed-flow-list-empty-over-lines"),
    # a bracket in a comment is not part of the flow list, so the next key is not pulled into the paths block
    pytest.param("paths: [] # [optional\nunused: [unclosed\n", "start", id="unparsed-bracket-in-comment"),
    pytest.param("paths: []\n# see [docs\nunused: [unclosed\n", "start", id="unparsed-comment-line-with-bracket"),
])
def test_only_a_paths_value_that_names_a_pattern_scopes_a_rule(tmp_path, front, when):
    payload = budget(_tree(tmp_path, {".claude/rules/r.md": f"---\n{front}---\nbody\n"}), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")] == {
        "path": ".claude/rules/r.md", "kind": "rule", "when": when, "chars": len("body\n")}


def test_a_cut_import_of_a_counted_rule_does_not_warn(tmp_path):
    files = {"CLAUDE.md": "@c1.md\n", "c1.md": "@c2.md\n", "c2.md": "@c3.md\n", "c3.md": "@c4.md\n",
             "c4.md": "@.claude/rules/r.md\n", ".claude/rules/r.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=4, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"
    assert payload["warnings"] == []


def test_a_linked_rules_directory_is_named_not_silently_skipped(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/style.md").write_text("style\n", encoding="utf-8")
    (tmp_path / ".claude/rules").mkdir(parents=True)
    (tmp_path / ".claude/rules/own.md").write_text("own\n", encoding="utf-8")
    try:
        (tmp_path / ".claude/rules/shared").symlink_to(tmp_path / "shared", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert [e["path"] for e in payload["entries"] if e["kind"] == "rule"] == [".claude/rules/own.md"]
    assert payload["warnings"] == [".claude/rules/shared: a link to a directory; the rules behind it are not counted"]


def test_a_rules_link_out_of_the_tree_is_named(tmp_path):
    shared = tmp_path / "shared"  # the docs' `ln -s ~/shared-claude-rules .claude/rules/shared`
    shared.mkdir()
    (shared / "style.md").write_text("style\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".claude/rules").mkdir(parents=True)
    try:
        (repo / ".claude/rules/shared").symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(repo), NAMES, rules=True)
    assert not [e for e in payload["entries"] if e["kind"] == "rule"]
    assert payload["warnings"] == [
        ".claude/rules/shared: a link that does not resolve in the tree; any rules behind it are not counted"]


def test_a_rule_with_a_utf8_bom_reads_its_frontmatter(tmp_path):
    payload = budget(_tree(tmp_path, {".claude/rules/r.md": "\ufeff---\npaths: src/**\n---\nbody\n"}),
                     NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")] == {
        "path": ".claude/rules/r.md", "kind": "rule", "when": "lazy", "chars": len("body\n")}


def test_a_paths_value_is_never_stringified(monkeypatch):
    # a YAML alias chain expands on str(); only strings are patterns, so a container is never converted
    from dokimi.repos import budget as module

    class Bomb:
        def __str__(self):
            raise AssertionError("a paths value was stringified")

    monkeypatch.setattr(module.yaml, "safe_load", lambda text: {"paths": [Bomb()]})
    assert module._has_paths("paths: x\n") is False


def test_a_root_rule_linked_to_an_off_agents_md_is_charged_its_body(tmp_path):
    # the rule route strips the frontmatter, so the body is what loads at start
    _tree(tmp_path, {"CLAUDE.md": "claude\n", "AGENTS.md": "---\npaths: []\n---\nagents\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/agents.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[("AGENTS.md", "guidance")] == {
        "path": "AGENTS.md", "kind": "guidance", "when": "start", "chars": len("agents\n")}
    assert payload["totals"]["start"] == len("claude\n") + len("agents\n")


def test_a_root_rule_linked_to_a_nested_rule_that_sorts_first_still_loads_at_start(tmp_path):
    # "!" sorts before "." : the nested real file is listed first, its root alias must still promote it
    _tree(tmp_path, {"!sub/.claude/rules/r.md": "rule body\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/r.md").symlink_to(tmp_path / "!sub/.claude/rules/r.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[("!sub/.claude/rules/r.md", "rule")]["when"] == "start"
    assert payload["totals"]["start"] == len("rule body\n")
    assert payload["totals"]["lazy"] == 0


def test_a_dangling_link_under_rules_that_is_not_markdown_is_named(tmp_path):
    # a link that does not resolve may be a directory of rules (`shared.rules`), which the tree cannot tell apart
    (tmp_path / ".claude/rules").mkdir(parents=True)
    (tmp_path / ".claude/rules/own.md").write_text("own\n", encoding="utf-8")
    try:
        (tmp_path / ".claude/rules/shared.rules").symlink_to(tmp_path / "nowhere")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert payload["warnings"] == [
        ".claude/rules/shared.rules: a link that does not resolve in the tree; any rules behind it are not counted"]


def test_a_rule_does_not_expand_its_at_imports_even_when_claude_md_imports_it(tmp_path):
    files = {"CLAUDE.md": "@.claude/rules/r.md\n", ".claude/rules/r.md": "rule @../../extra.md\n",
             "extra.md": "extra\n"}
    payload = budget(_tree(tmp_path, files), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"
    assert [e["path"] for e in payload["entries"] if e["path"] == "extra.md"] == []


def test_a_file_named_claude_md_under_rules_is_a_rule_not_guidance(tmp_path):
    files = {"CLAUDE.md": "root\n", ".claude/rules/CLAUDE.md": "@extra.md\n", ".claude/rules/extra.md": "extra\n"}
    payload = budget(_tree(tmp_path, files), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/CLAUDE.md", "rule")]["when"] == "start"
    assert not [e for e in payload["entries"] if e["kind"] == "guidance" and e["path"].startswith(".claude/rules/")]


def test_a_linked_directory_named_like_a_rule_is_named_not_counted_as_a_rule(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/style.md").write_text("style\n", encoding="utf-8")
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/shared.md").symlink_to(tmp_path / "shared", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert not [e for e in payload["entries"] if e["kind"] == "rule"]
    assert payload["warnings"] == [".claude/rules/shared.md: a link to a directory; the rules behind it are not counted"]


def test_a_dangling_link_to_a_markdown_rule_is_named_and_counted_as_zero(tmp_path):
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/api.md").symlink_to(tmp_path / "missing.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/api.md", "rule")]["chars"] == 0
    assert payload["warnings"] == [".claude/rules/api.md: not readable (not found, broken link, or outside tree), "
                                   "counted as 0"]


def test_an_alias_of_a_rule_is_not_followed_as_an_import(tmp_path):
    # CLAUDE.md imports an alias that links to a rule. The rule's @ imports are not followed, so extra.md is not counted
    tree = _tree(tmp_path, {"CLAUDE.md": "@docs/memory/alias.md\n", "extra.md": "extra\n",
                            ".claude/rules/r.md": "rule @../../extra.md\n"})
    (tmp_path / "docs/memory").mkdir(parents=True)
    try:
        (tmp_path / "docs/memory/alias.md").symlink_to(tmp_path / ".claude/rules/r.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert all(e["path"] != "extra.md" for e in payload["entries"])
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"

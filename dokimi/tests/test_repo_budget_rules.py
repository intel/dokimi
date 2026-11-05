# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget` and `.claude/rules`: when each rule loads, and what of it is counted."""

from __future__ import annotations

import subprocess

import pytest

from dokimi.repos.budget import budget
from dokimi.repos.source import GitTree, WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return WorkTree(tmp_path)


def _by_path(payload):
    return {(e["path"], e["kind"]): e for e in payload["entries"]}


def _source(root, kind):
    """``root`` committed to git, read as a work tree or as its HEAD commit."""
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")
    return WorkTree(root) if kind == "worktree" else GitTree(root / ".git", git("rev-parse", "HEAD"))


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
    payload = budget(WorkTree(tmp_path), NAMES, rules=True, claude_displaces_agents=True)
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
        ".claude/rules/mid.md": "---\ndescription: see @../../extra.md\n---\nmid @deep.md\n",
        ".claude/rules/deep.md": "deep body\n",
        "extra.md": "extra body\n",
    })
    got = _by_path(budget(tree, profile.guidance_names, profile.import_max_depth, False))
    assert ((".claude/rules/AGENTS.md", "guidance") in got)
    assert ((".claude/rules/mid.md", "import") in got)
    assert ((".claude/rules/deep.md", "import") in got)
    # the frontmatter is not removed for a harness that does not read rules, so its @path is an import too
    assert ("extra.md", "import") in got


def test_a_profile_that_does_not_read_rules_keeps_the_frontmatter_of_a_rule_linked_guidance_file(tmp_path):
    # the rules-off recount of a guidance file a rule links to keeps its frontmatter: start is 52, not 33
    tree = _tree(tmp_path, {"CLAUDE.md": "@.claude/rules/link.md\n", "AGENTS.md": "---\npaths: [x]\n---\nbody body\n",
                            ".claude/rules/link.md": "placeholder\n"})
    try:
        (tmp_path / ".claude/rules/link.md").unlink()
        (tmp_path / ".claude/rules/link.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(tree, ("CLAUDE.md", "AGENTS.md"), None, False)
    assert payload["totals"]["start"] == 52
    assert _by_path(payload)[("AGENTS.md", "guidance")]["chars"] == 29


def test_a_profile_that_does_not_read_rules_counts_an_imported_rule_with_its_frontmatter(tmp_path):
    # rules off: the rule is an ordinary import, counted in full (26 chars), not as a 5-char body
    rule = "---\npaths:\n  - a\n---\nrule\n"
    tree = _tree(tmp_path, {"CLAUDE.md": "@.claude/rules/r.md\n", ".claude/rules/r.md": rule})
    payload = budget(tree, NAMES, None, False)
    assert _by_path(payload)[(".claude/rules/r.md", "import")]["chars"] == len(rule) == 26


@pytest.mark.parametrize("claude_md", ["@shared.md\n@.claude/rules/r.md\n", "@.claude/rules/r.md\n@shared.md\n"])
def test_an_ordinary_import_keeps_the_frontmatter_that_a_rule_link_to_the_same_file_removes(tmp_path, claude_md):
    # CLAUDE.md imports shared.md whole, frontmatter included; the rule link to it loads only the body.
    # Whichever comes first, the file loads at start as an import, so it is counted in full.
    text = "---\ndescription: example\n---\nbody\n"
    tree = _tree(tmp_path, {"CLAUDE.md": claude_md, "shared.md": text})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/r.md").symlink_to(tmp_path / "shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    got = _by_path(payload)
    assert got[("shared.md", "import")]["when"] == "start"
    assert got[("shared.md", "import")]["chars"] == len(text)
    assert payload["warnings"] == []


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
    # the paths key itself does not parse (an unclosed list), so its items are read as text: null and ~ are no pattern
    pytest.param("paths: [null\n", "start", id="unparsed-key-null-item"),
    pytest.param("paths: [~\n", "start", id="unparsed-key-tilde-item"),
    pytest.param("paths: [null, ~, src/**\n", "lazy", id="unparsed-key-null-tilde-and-a-pattern"),
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
    # an indented field of another key is not a continuation of the paths value, after a complete value or a list
    pytest.param("paths: []\n  unused: [unclosed\n", "start", id="unparsed-indented-field-after-empty-value"),
    pytest.param("paths: null\n  unused: [unclosed\n", "start", id="unparsed-indented-field-after-null"),
    pytest.param('paths: []\n  "unused": [unclosed\n', "start", id="unparsed-double-quoted-field-after-empty-value"),
    pytest.param("paths: []\n  'unused': [unclosed\n", "start", id="unparsed-single-quoted-field-after-empty-value"),
    pytest.param("paths: src/**\n  unused: [unclosed\n", "lazy", id="unparsed-indented-field-after-pattern"),
    pytest.param('paths:\n  - "src/**"\n  unused: [unclosed\n', "lazy", id="unparsed-indented-field-after-list"),
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


def test_a_cut_import_of_a_scoped_rule_warns(tmp_path):
    # a scoped rule loads only when a matching file is read, so the cut import is still named
    files = {"CLAUDE.md": "@c1.md\n", "c1.md": "@c2.md\n", "c2.md": "@c3.md\n", "c3.md": "@c4.md\n",
             "c4.md": "@.claude/rules/r.md\n", ".claude/rules/r.md": "---\npaths: src/**\n---\nrule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=4, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "lazy"
    assert len(payload["warnings"]) == 1
    assert "c4.md: import of .claude/rules/r.md" in payload["warnings"][0]


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


@pytest.mark.parametrize("kind", ["worktree", "git"])
@pytest.mark.parametrize(("shared", "warnings"), [
    ({"shared/.claude/rules/a.md": "rule body\n"},
     [".claude: a link to a directory; the rules behind it are not counted at start"]),
    ({"shared/.claude/skills/x/SKILL.md": "---\nname: x\ndescription: d\n---\nbody\n"}, []),
])
def test_a_root_claude_link_to_a_directory_names_the_rules_behind_it(tmp_path, kind, shared, warnings):
    # Claude Code reads the rules behind a `.claude` link at start; the tree lists the link as one entry
    _tree(tmp_path, {"CLAUDE.md": "root\n", **shared})
    try:
        (tmp_path / ".claude").symlink_to("shared/.claude", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert budget(_source(tmp_path, kind), NAMES, rules=True)["warnings"] == warnings


@pytest.mark.parametrize("kind", ["worktree", "git"])
def test_a_root_claude_link_out_of_the_tree_is_named(tmp_path, kind):
    _tree(tmp_path, {"outside/.claude/rules/a.md": "rule body\n", "repo/CLAUDE.md": "root\n"})
    repo = tmp_path / "repo"
    try:
        (repo / ".claude").symlink_to("../outside/.claude", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(_source(repo, kind), NAMES, rules=True)
    assert payload["warnings"] == [
        ".claude: a link that does not resolve in the tree; any rules behind it are not counted"]


@pytest.mark.parametrize("kind", ["worktree", "git"])
def test_a_rules_submodule_is_named(tmp_path, kind):
    # sharing one rules set across repositories as a submodule: neither tree lists the files in it
    shared = tmp_path / "shared"
    _tree(shared, {"plain.md": "plain body\n"})
    _source(shared, "git")
    repo = tmp_path / "repo"
    _tree(repo, {"CLAUDE.md": "# root\n"})
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                    str(shared), ".claude/rules"], check=True, capture_output=True)
    payload = budget(_source(repo, kind), NAMES, rules=True)
    assert [e["path"] for e in payload["entries"]] == ["CLAUDE.md"]
    assert payload["warnings"] == [".claude/rules: a git submodule; any rules in it are not counted"]


def test_only_a_submodule_that_may_hold_rules_is_named(tmp_path):
    gitmodules = "".join(f'[submodule "{p}"]\n	path = {p}\n	url = ../{i}\n'
                         for i, p in enumerate(["lib", ".claude", "sub/.claude/rules", ".claude/skills"]))
    payload = budget(_tree(tmp_path, {".gitmodules": gitmodules}), NAMES, rules=True)
    assert payload["warnings"] == [".claude: a git submodule; any rules in it are not counted",
                                   "sub/.claude/rules: a git submodule; any rules in it are not counted"]
    assert budget(WorkTree(tmp_path), NAMES, rules=False)["warnings"] == []
    crlf = '[submodule "a"]\r\n\tpath=.claude/rules/\r\n\turl = ../a\r\n'
    other = budget(_tree(tmp_path / "crlf", {".gitmodules": crlf}), NAMES, rules=True)
    assert other["warnings"] == [".claude/rules: a git submodule; any rules in it are not counted"]


def test_a_rule_with_a_utf8_bom_reads_its_frontmatter(tmp_path):
    payload = budget(_tree(tmp_path, {".claude/rules/r.md": "\ufeff---\npaths: src/**\n---\nbody\n"}),
                     NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")] == {
        "path": ".claude/rules/r.md", "kind": "rule", "when": "lazy", "chars": len("body\n")}


def test_a_paths_value_is_never_stringified(monkeypatch):
    # a YAML alias chain expands on str(); only strings are patterns, so a container is never converted
    from dokimi.repos import rule_frontmatter as module

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
    payload = budget(WorkTree(tmp_path), NAMES, rules=True, claude_displaces_agents=True)
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


def test_a_rule_a_claude_md_imports_expands_its_at_imports_transitively(tmp_path):
    # maintainer decision (intel/dokimi #326): a CLAUDE.md that imports a rule loads it at start, and the @ paths
    # in it are followed like any import, hop by hop
    files = {"CLAUDE.md": "@.claude/rules/r.md\n", ".claude/rules/r.md": "rule @../../extra.md\n",
             "extra.md": "extra @extra2.md\n", "extra2.md": "extra2\n"}
    payload = budget(_tree(tmp_path, files), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"
    assert _by_path(payload)[("extra.md", "import")]["when"] == "start"
    assert _by_path(payload)[("extra2.md", "import")]["when"] == "start"
    assert payload["warnings"] == []


def test_a_rule_discovered_as_a_rule_does_not_expand_its_at_imports(tmp_path):
    # no CLAUDE.md imports the rule: it loads as a rule, and its @ paths are not followed
    files = {".claude/rules/r.md": "rule @../../extra.md\n", "extra.md": "extra\n"}
    payload = budget(_tree(tmp_path, files), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"
    assert [e["path"] for e in payload["entries"] if e["path"] == "extra.md"] == []


def test_an_at_path_in_a_rules_frontmatter_is_not_an_import(tmp_path):
    # the frontmatter is not counted (Claude Code removes it before loading the rule), so an @ path in it is no import
    files = {"CLAUDE.md": "@.claude/rules/r.md\n",
             ".claude/rules/r.md": "---\ndescription: see @../../team.md\n---\nrule\n", "team.md": "team\n"}
    payload = budget(_tree(tmp_path, files), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"
    assert [e["path"] for e in payload["entries"] if e["path"] == "team.md"] == []


def test_an_import_chain_through_a_rule_counts_toward_the_hop_limit(tmp_path):
    files = {"CLAUDE.md": "@.claude/rules/r.md\n", ".claude/rules/r.md": "rule @../../extra.md\n",
             "extra.md": "extra @extra2.md\n", "extra2.md": "extra2\n"}
    # CLAUDE.md -> rule (hop 1) -> extra.md (hop 2) -> extra2.md (hop 3): a 2-hop limit cuts extra2.md
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=2, rules=True)
    assert _by_path(payload)[("extra.md", "import")]["when"] == "start"
    assert [e["path"] for e in payload["entries"] if e["path"] == "extra2.md"] == []
    assert payload["warnings"] == [
        "extra.md: import of extra2.md is past the harness's 2-hop import limit, not followed"]


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


def test_an_alias_of_a_rule_is_followed_as_an_import(tmp_path):
    # CLAUDE.md imports an alias that links to a rule: the rule is imported, so its @ imports are followed
    tree = _tree(tmp_path, {"CLAUDE.md": "@docs/memory/alias.md\n", "extra.md": "extra\n",
                            ".claude/rules/r.md": "rule @../../extra.md\n"})
    (tmp_path / "docs/memory").mkdir(parents=True)
    try:
        (tmp_path / "docs/memory/alias.md").symlink_to(tmp_path / ".claude/rules/r.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[("extra.md", "import")]["when"] == "start"
    assert _by_path(payload)[(".claude/rules/r.md", "rule")]["when"] == "start"


def test_an_alias_of_a_rule_drops_the_rules_frontmatter_before_its_imports_are_followed(tmp_path):
    # the alias resolves to a rule, so it loads the rule's body: an @path in the frontmatter is no import
    rule = "---\npaths: []\nnote: see @../extra.md\n---\nbody\n"
    tree = _tree(tmp_path, {"CLAUDE.md": "@docs/alias.md\n", "extra.md": "x\n", ".claude/rules/r.md": rule})
    (tmp_path / "docs").mkdir()
    try:
        (tmp_path / "docs/alias.md").symlink_to(tmp_path / ".claude/rules/r.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert not [e for e in payload["entries"] if e["path"] == "extra.md"]
    assert payload["totals"]["start"] == len("@docs/alias.md\n") + len("body\n")


def test_an_import_of_a_rule_linked_to_a_plain_file_keeps_the_rules_body_count(tmp_path):
    # the import names the rule path, so it loads the rule (body only), not the plain file it links to (18 chars)
    tree = _tree(tmp_path, {"CLAUDE.md": "@.claude/rules/r.md\n", "shared.md": "---\nk: v\n---\nrule\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/r.md").symlink_to(tmp_path / "shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[(".claude/rules/r.md", "rule")] == {
        "path": ".claude/rules/r.md", "kind": "rule", "when": "start", "chars": len("rule\n")}
    assert payload["totals"]["start"] == len("@.claude/rules/r.md\n") + len("rule\n")


@pytest.mark.parametrize("claude_md", ["@.claude/rules/r.md\n@shared.md\n", "@shared.md\n@.claude/rules/r.md\n"])
def test_the_order_of_imports_does_not_hide_what_a_rule_link_leads_to(tmp_path, claude_md):
    # .claude/rules/r.md links to shared.md, which imports extra.md. Whichever of the two imports comes first,
    # shared.md's own route (at the root) still expands extra.md
    tree = _tree(tmp_path, {"CLAUDE.md": claude_md, "shared.md": "shared @extra.md\n", "extra.md": "extra\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/r.md").symlink_to(tmp_path / "shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[("extra.md", "import")]["when"] == "start"
    assert payload["warnings"] == []


def test_a_cut_import_of_an_unscoped_nested_rule_does_not_warn(tmp_path):
    # a nested rule with no paths loads whenever the agent works in sub/, as the cut import in sub/CLAUDE.md would
    files = {"sub/CLAUDE.md": "@.claude/rules/r.md\n", "sub/.claude/rules/r.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=0, rules=True)
    assert _by_path(payload)[("sub/.claude/rules/r.md", "rule")]["when"] == "lazy"
    assert payload["warnings"] == []


def test_a_cut_import_in_a_subtree_with_an_unscoped_rule_still_warns(tmp_path):
    # the rule is not what cuts sub/notes.md: an ordinary cut import in sub/ is named even with an unscoped rule there
    files = {"sub/CLAUDE.md": "@notes.md\n", "sub/notes.md": "notes\n", "sub/.claude/rules/r.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=0, rules=True)
    assert payload["warnings"] == [
        "sub/CLAUDE.md: import of sub/notes.md is past the harness's 0-hop import limit, not followed"]


def test_a_cut_import_of_an_unscoped_nested_rule_from_outside_its_subtree_still_warns(tmp_path):
    # other/ does not load sub/'s rule: only the agent working in sub/ does, so the cut import from other/ is named
    files = {"other/CLAUDE.md": "@../sub/.claude/rules/r.md\n", "sub/.claude/rules/r.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=0, rules=True)
    assert payload["warnings"] == [
        "other/CLAUDE.md: import of sub/.claude/rules/r.md is past the harness's 0-hop import limit, not followed"]


def test_a_cut_import_of_an_unscoped_nested_rule_from_a_chain_rooted_outside_its_subtree_still_warns(tmp_path):
    # other/CLAUDE.md reaches sub/notes.md, which cuts sub/'s rule: the chain is other/'s, so the rule is not loaded
    files = {"other/CLAUDE.md": "@../sub/notes.md\n", "sub/notes.md": "@.claude/rules/x.md\n",
             "sub/.claude/rules/x.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=1, rules=True)
    assert payload["warnings"] == [
        "sub/notes.md: import of sub/.claude/rules/x.md is past the harness's 1-hop import limit, not followed"]


def test_a_cut_import_stays_named_for_every_root_that_reaches_it_through_a_shared_file(tmp_path):
    # sub/CLAUDE.md reaches sub/notes.md first and zother/CLAUDE.md reaches the same file later: the rule
    # loads for the work in sub/ only, so zother/'s route to the cut import must still be named
    files = {"sub/CLAUDE.md": "@notes.md\n", "zother/CLAUDE.md": "@../sub/notes.md\n",
             "sub/notes.md": "@.claude/rules/x.md\n", "sub/.claude/rules/x.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=1, rules=True)
    assert payload["warnings"] == [
        "sub/notes.md: import of sub/.claude/rules/x.md is past the harness's 1-hop import limit, not followed"]


def test_a_cut_import_is_not_blamed_on_a_root_that_reaches_the_shared_file_past_the_hop_limit(tmp_path):
    # sub/'s cut of the unscoped nested rule is quiet (it loads with sub/); zother/ reads mid.md past the limit,
    # so its chain must not be blamed for the cut
    files = {"sub/CLAUDE.md": "@mid.md\n", "sub/mid.md": "@.claude/rules/r.md\n", "sub/.claude/rules/r.md": "rule\n",
             "zother/CLAUDE.md": "@bridge.md\n", "zother/bridge.md": "@../sub/mid.md\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=1, rules=True)
    assert payload["warnings"] == []


def test_an_alias_cycle_back_to_agents_md_is_not_expanded_again_when_rules_are_off(tmp_path):
    # with rules off, .claude/rules/link.md is ordinary guidance: its target is AGENTS.md, already read, so the
    # traversal must not expand it again relative to .claude/rules/ (which would import .claude/rules/extra.md)
    files = {"AGENTS.md": "@.claude/rules/link.md\n@extra.md\n", "extra.md": "extra\n",
             ".claude/rules/extra.md": "rule extra\n"}
    tree = _tree(tmp_path, files)
    try:
        (tmp_path / ".claude/rules/link.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=False)

    assert not [e for e in payload["entries"] if e["path"] == ".claude/rules/extra.md"]


def test_a_cut_import_of_a_scoped_nested_rule_still_warns(tmp_path):
    # a scoped nested rule loads only when the agent reads a matching file, so the cut import is still named
    files = {"sub/CLAUDE.md": "@.claude/rules/r.md\n", "sub/.claude/rules/r.md": "---\npaths: src/**\n---\nrule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=0, rules=True)
    assert _by_path(payload)[("sub/.claude/rules/r.md", "rule")]["when"] == "lazy"
    assert payload["warnings"] == [
        "sub/CLAUDE.md: import of sub/.claude/rules/r.md is past the harness's 0-hop import limit, not followed"]


def test_a_root_claude_md_linked_to_a_rule_still_expands_its_imports(tmp_path):
    # the root is memory, not a rule: Claude Code expands its @ imports whatever file it links to
    tree = _tree(tmp_path, {"extra.md": "x" * 500 + "\n", ".claude/rules/shared.md": "shared @extra.md\n"})
    try:
        (tmp_path / "CLAUDE.md").symlink_to(tmp_path / ".claude/rules/shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[("extra.md", "import")]["when"] == "start"
    assert _by_path(payload)[("extra.md", "import")]["chars"] == 501


def test_a_scoped_rule_alias_of_agents_md_does_not_suppress_a_cut_import(tmp_path):
    # AGENTS.md is off (a root CLAUDE.md displaces it) and is reached only through an import cut at hop 4.
    # The rule link to it is scoped, so the file still does not load at start: the cut import is named.
    files = {"CLAUDE.md": "@c1.md\n", "c1.md": "@c2.md\n", "c2.md": "@c3.md\n", "c3.md": "@c4.md\n",
             "c4.md": "@AGENTS.md\n", "AGENTS.md": "---\npaths: src/**\n---\nagents\n"}
    tree = _tree(tmp_path, files)
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/agents.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, import_max_depth=4, rules=True, claude_displaces_agents=True)

    assert _by_path(payload)[("AGENTS.md", "guidance")]["when"] == "lazy"
    assert any("c4.md: import of AGENTS.md is past" in w for w in payload["warnings"])


def test_an_off_agents_md_reached_only_through_a_rule_link_is_charged_its_body(tmp_path):
    # CLAUDE.md imports the rule link and nothing else loads AGENTS.md, so the rule route's count applies
    tree = _tree(tmp_path, {"CLAUDE.md": "@.claude/rules/agents.md\n", "AGENTS.md": "---\npaths: src/**\n---\nagents\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/agents.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True, claude_displaces_agents=True)

    assert _by_path(payload)[("AGENTS.md", "guidance")] == {
        "path": "AGENTS.md", "kind": "guidance", "when": "start", "chars": len("agents\n")}


@pytest.mark.parametrize("claude_md", ["@AGENTS.md\n@.claude/rules/agents.md\n", "@.claude/rules/agents.md\n@AGENTS.md\n"])
def test_an_off_agents_md_with_an_ordinary_import_keeps_its_full_count(tmp_path, claude_md):
    agents = "---\npaths: []\n---\nagents\n"
    tree = _tree(tmp_path, {"CLAUDE.md": claude_md, "AGENTS.md": agents})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/agents.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[("AGENTS.md", "guidance")]["chars"] == len(agents)


@pytest.mark.parametrize("claude_md", ["@.claude/rules/r.md\n@shared/r.md\n", "@shared/r.md\n@.claude/rules/r.md\n"])
def test_an_ordinary_import_of_a_rule_behind_a_linked_rules_directory_keeps_its_frontmatter(tmp_path, claude_md):
    # .claude/rules is a link to a directory, so no rule entry is made for shared/r.md. The rule-path import loads
    # the file without its frontmatter, the ordinary import loads it whole: the start total counts it whole.
    text = "---\npaths: []\n---\nbody\n"
    tree = _tree(tmp_path, {"CLAUDE.md": claude_md, "shared/r.md": text})
    (tmp_path / ".claude").mkdir()
    try:
        (tmp_path / ".claude/rules").symlink_to(tmp_path / "shared", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[("shared/r.md", "import")] == {
        "path": "shared/r.md", "kind": "import", "when": "start", "chars": len(text)}
    assert payload["totals"]["start"] == len(claude_md) + len(text)
    assert payload["warnings"] == [".claude/rules: a link to a directory; the rules behind it are not counted"]


def test_an_explicit_import_of_a_rule_behind_a_linked_rules_directory_drops_its_frontmatter(tmp_path):
    tree = _tree(tmp_path, {"CLAUDE.md": "@.claude/rules/r.md\n", "shared/r.md": "---\npaths: src/**\n---\nbody\n"})
    (tmp_path / ".claude").mkdir()
    try:
        (tmp_path / ".claude/rules").symlink_to(tmp_path / "shared", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[(".claude/rules/r.md", "import")]["chars"] == len("body\n")


def test_a_root_cut_of_a_nested_rule_is_named_when_a_nested_claude_md_cuts_the_same_import(tmp_path):
    # the root chain cuts sub/mid.md's import of the rule at hop 1: the rule does not load at start. A nested
    # sub/CLAUDE.md reaches the same cut lazily, and that must not silence the start-time warning.
    files = {"CLAUDE.md": "@sub/mid.md\n", "sub/mid.md": "@.claude/rules/r.md\n", "sub/.claude/rules/r.md": "rule\n",
             "sub/CLAUDE.md": "@mid.md\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=1, rules=True)
    assert payload["warnings"] == [
        "sub/mid.md: import of sub/.claude/rules/r.md is past the harness's 1-hop import limit, not followed"]


def test_a_cut_import_of_a_nested_rule_is_named_by_a_profile_that_does_not_read_rules(tmp_path):
    # without rules the file is no rule that loads with sub/CLAUDE.md anyway, so the cut import still warns
    files = {"sub/CLAUDE.md": "@.claude/rules/r.md\n", "sub/.claude/rules/r.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=0, rules=False)
    assert payload["warnings"] == [
        "sub/CLAUDE.md: import of sub/.claude/rules/r.md is past the harness's 0-hop import limit, not followed"]


@pytest.mark.parametrize(("importer", "link"), [
    ("sub/CLAUDE.md", "@../.claude/rules/r.md\n"),
    ("sub/AGENTS.md", "@../.claude/rules/r.md\n"),
    ("AGENTS.md", "@.claude/rules/r.md\n"),
])
def test_a_rule_imported_by_guidance_does_not_import_what_its_frontmatter_names(tmp_path, importer, link):
    # a rule's frontmatter is removed before it loads, so an @path in it is not an import, whichever guidance file imports the rule
    files = {importer: link, ".claude/rules/r.md": "---\npaths: []\nnote: see @../../extra.md\n---\nbody\n", "extra.md": "x\n"}
    payload = budget(_tree(tmp_path, files), NAMES, rules=True)
    assert not [e for e in payload["entries"] if e["path"] == "extra.md"]
    assert payload["warnings"] == []


def test_a_root_claude_md_linked_to_a_rule_reads_its_frontmatter_as_memory(tmp_path):
    # the root CLAUDE.md is memory, read whole: an @ path in its frontmatter is an import, not a rule's stripped text
    tree = _tree(tmp_path, {"extra.md": "x\n", ".claude/rules/shared.md": "---\npaths: []\nnote: see @extra.md\n---\nshared\n"})
    try:
        (tmp_path / "CLAUDE.md").symlink_to(tmp_path / ".claude/rules/shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[("extra.md", "import")]["when"] == "start"


def test_a_nested_import_of_a_file_a_root_rule_links_to_keeps_the_rule_at_start(tmp_path):
    # the root rule loads the file at start (body only); the nested CLAUDE.md's import of it must not relabel that entry lazy
    shared = "---\npaths: []\n---\nshared\n"
    tree = _tree(tmp_path, {"sub/CLAUDE.md": "@../shared.md\n", "shared.md": shared})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/r.md").symlink_to(tmp_path / "shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    payload = budget(tree, NAMES, rules=True)

    assert _by_path(payload)[(".claude/rules/r.md", "rule")] == {
        "path": ".claude/rules/r.md", "kind": "rule", "when": "start", "chars": len("shared\n")}
    assert payload["totals"]["lazy"] == len("@../shared.md\n")  # the nested CLAUDE.md itself, and nothing else


@pytest.mark.parametrize("rules", [True, False])
def test_a_cut_start_import_of_a_nested_rule_warns_even_when_a_nested_claude_md_also_cuts_it(tmp_path, rules):
    # sub/notes.md is imported at start and cuts the rule import; sub/CLAUDE.md imports notes.md and cuts it lazily
    files = {"CLAUDE.md": "@sub/notes.md\n", "sub/notes.md": "@.claude/rules/r.md\n", "sub/CLAUDE.md": "@notes.md\n",
             "sub/.claude/rules/r.md": "rule\n"}
    payload = budget(_tree(tmp_path, files), NAMES, import_max_depth=1, rules=rules)
    assert payload["warnings"] == [
        "sub/notes.md: import of sub/.claude/rules/r.md is past the harness's 1-hop import limit, not followed"]


def test_the_receipt_budget_reads_rules_as_repo_budget_does(tmp_path, monkeypatch, capsys):
    # antonwolfy's tree: AGENTS.md is off, and loads only through the rule link; the receipt must not disagree
    from types import SimpleNamespace

    from dokimi.commands import repo as repo_cmd
    from dokimi.repos import receipt as receipt_module

    _tree(tmp_path, {"CLAUDE.md": "root\n", "AGENTS.md": "agents guidance\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/agents.md").symlink_to(tmp_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    seen = {}

    def fake_build_receipt(reach, budget_payload, tree):
        seen["budget"] = budget_payload
        return {}

    monkeypatch.setattr(receipt_module, "build_receipt", fake_build_receipt)
    monkeypatch.setattr("dokimi.artifacts.save_artifact", lambda name, data, path: {"path": str(path)})
    profile = SimpleNamespace(guidance_names=NAMES, import_max_depth=None, reads_claude_rules=True,
                                 claude_displaces_agents=True, follows_imports=True)

    repo_cmd._print_receipt(WorkTree(tmp_path), profile, {}, tmp_path)

    assert _by_path(seen["budget"])[("AGENTS.md", "guidance")]["when"] == "start"


def test_a_rule_import_through_a_linked_rules_directory_counts_the_body_of_the_guidance_it_reaches(tmp_path):
    # the rules dir is a link, so the tree lists no rule entry for the file; the import's rule route still strips
    # the frontmatter, and the guidance entry takes the body count at the same loading time
    _tree(tmp_path, {"shared/AGENTS.md": "---\npaths: []\n---\nbody\n", "CLAUDE.md": "@.claude/rules/AGENTS.md\n"})
    (tmp_path / ".claude").mkdir()
    try:
        (tmp_path / ".claude/rules").symlink_to(tmp_path / "shared")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[("shared/AGENTS.md", "guidance")] == {
        "path": "shared/AGENTS.md", "kind": "guidance", "when": "start", "chars": len("body\n")}
    assert payload["totals"]["start"] == len("@.claude/rules/AGENTS.md\n") + len("body\n")


def test_a_root_import_of_a_rule_linked_to_a_nested_claude_md_counts_its_body_at_start(tmp_path):
    # sub/CLAUDE.md also loads lazily as memory with its frontmatter, but the root import loads the body at start
    _tree(tmp_path, {"CLAUDE.md": "@.claude/rules/nested.md\n", "sub/CLAUDE.md": "---\npaths: []\n---\nnested\n"})
    (tmp_path / ".claude/rules").mkdir(parents=True)
    try:
        (tmp_path / ".claude/rules/nested.md").symlink_to(tmp_path / "sub/CLAUDE.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[("sub/CLAUDE.md", "guidance")] == {
        "path": "sub/CLAUDE.md", "kind": "guidance", "when": "start", "chars": len("nested\n")}


def test_a_cut_import_is_not_blamed_on_a_root_that_reaches_the_file_of_the_rule_route_by_another_path(tmp_path):
    # sub/'s rule link reads shared.md at sub/.claude/rules/link.md, so its @r.md is sub/'s rule, quiet for sub/;
    # zother/ reaches the same bytes as shared.md, where the same @r.md is no file at all
    _tree(tmp_path, {"sub/CLAUDE.md": "@.claude/rules/link.md\n", "shared.md": "@r.md\n",
                     "zother/CLAUDE.md": "@../shared.md\n", "sub/.claude/rules/r.md": "rule\n"})
    try:
        (tmp_path / "sub/.claude/rules/link.md").symlink_to(tmp_path / "shared.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    payload = budget(WorkTree(tmp_path), NAMES, import_max_depth=1, rules=True)
    assert payload["warnings"] == []

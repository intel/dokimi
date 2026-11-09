# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Spec checks of ``catalog check``: unknown frontmatter keys, allowed-tools names, body size, and load messages."""

from __future__ import annotations

import pytest

from dokimi.external import read_skill_manifest, review_skills
from dokimi.skills import load_skill


def _write(root, rel: str, text: str):
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def _skill(root, name: str, extra: str = "", body: str = "# Body\n", description: str = "Use when testing things."):
    """Write ``<root>/skills/<name>/SKILL.md`` with ``extra`` frontmatter lines and ``body``."""
    return _write(
        root,
        f"skills/{name}",
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}",
    )


def _findings(root) -> dict[str, list]:
    """Each loaded skill's findings, keyed by the skill directory name."""
    entries = read_skill_manifest(root)
    reviews = review_skills(entries)
    return {e.name: [f for f in r.findings] for e, r in zip(entries, reviews, strict=True) if r is not None}


def _codes(root, name: str) -> set[str]:
    return {f.code for f in _findings(root)[name]}


def _message(root, name: str, code: str) -> str:
    return next(f.message for f in _findings(root)[name] if f.code == code)


# ---- frontmatter-unknown-key -------------------------------------------------------

def test_unknown_frontmatter_key_is_a_warn_that_names_the_key(tmp_path):
    _skill(tmp_path, "color-skill", extra="color: red\n")

    [finding] = [f for f in _findings(tmp_path)["color-skill"] if f.code == "frontmatter-unknown-key"]
    assert finding.level == "warn"
    assert "'color'" in finding.message


def test_spec_and_claude_code_keys_are_not_unknown(tmp_path):
    extra = (
        "license: MIT\nmetadata:\n  owner: team\ncompatibility: any\n"
        "allowed-tools: Read, Grep\nargument-hint: '[file]'\ndisable-model-invocation: true\n"
        "user-invocable: false\nmodel: sonnet\ncontext: fork\nagent: Explore\nhooks: {}\n"
        "version: 1\nwhen_to_use: when reviewing\npaths: src/**\nshell: bash\neffort: low\n"
    )
    _skill(tmp_path, "all-keys", extra=extra)

    assert "frontmatter-unknown-key" not in _codes(tmp_path, "all-keys")


# ---- allowed-tools-unknown ---------------------------------------------------------

def test_allowed_tools_name_that_is_no_claude_code_tool_is_a_warn(tmp_path):
    _skill(tmp_path, "typo-tools", extra="allowed-tools: Read, Frobnicate\n")

    [finding] = [f for f in _findings(tmp_path)["typo-tools"] if f.code == "allowed-tools-unknown"]
    assert finding.level == "warn"
    assert "'Frobnicate'" in finding.message
    assert "'Read'" not in finding.message


def test_allowed_tools_mcp_and_pattern_entries_are_not_flagged(tmp_path):
    # ``Bash(git add *)`` is checked as Bash, and its pattern's words are not tool names.
    _skill(
        tmp_path,
        "patterns",
        extra="allowed-tools: Read, Grep, Bash(git add *), mcp__github__create_issue, PowerShell(Get-ChildItem:*)\n",
    )
    _skill(tmp_path, "yaml-list", extra="allowed-tools:\n  - Read\n  - Edit\n  - Bash(npm test)\n")

    assert "allowed-tools-unknown" not in _codes(tmp_path, "patterns")
    assert "allowed-tools-unknown" not in _codes(tmp_path, "yaml-list")


def test_allowed_tools_yaml_list_entry_that_is_no_tool_is_flagged(tmp_path):
    _skill(tmp_path, "list-typo", extra="allowed-tools:\n  - Read\n  - Frobnicate\n")

    assert "'Frobnicate'" in _message(tmp_path, "list-typo", "allowed-tools-unknown")


def test_allowed_tools_pattern_with_nested_or_quoted_parentheses_is_not_a_tool_name(tmp_path):
    _skill(tmp_path, "nested", extra='allowed-tools: Read(src/a(b).py), Bash(python -c "print(1)")\n')
    _skill(tmp_path, "nested-list", extra='allowed-tools:\n  - Read(src/a(b).py)\n  - Bash(python -c "print(1)")\n')

    assert "allowed-tools-unknown" not in _codes(tmp_path, "nested")
    assert "allowed-tools-unknown" not in _codes(tmp_path, "nested-list")


def test_allowed_tools_name_with_a_pattern_is_flagged_by_its_name(tmp_path):
    _skill(tmp_path, "pattern-typo", extra="allowed-tools: Frobnicate(test)\n")

    assert "'Frobnicate'" in _message(tmp_path, "pattern-typo", "allowed-tools-unknown")


def test_allowed_tools_quotes_that_alternate_with_parens_are_read_in_linear_time():
    # Each quoted scan of this value runs to its end before it fails; the quotes are then switched off once.
    import time

    from dokimi.external.validate import _unknown_tools

    value = ("A('" + ")'() ") * 4_000  # 32,000 characters
    started = time.perf_counter()
    _unknown_tools(value)
    assert time.perf_counter() - started < 2.0


def test_apostrophe_in_a_pattern_does_not_hide_the_entries_after_it():
    from dokimi.external.validate import _unknown_tools

    assert _unknown_tools("Bash(echo don't), Frobnicate") == ["Frobnicate"]


def test_closing_parenthesis_inside_quotes_does_not_end_the_pattern():
    from dokimi.external.validate import _unknown_tools

    assert _unknown_tools('Bash(echo ")") Frobnicate') == ["Frobnicate"]
    assert _unknown_tools('Bash(echo "it\'s") Frobnicate') == ["Frobnicate"]


def test_allowed_tools_pattern_that_never_closes_is_dropped_in_linear_time():
    from dokimi.external.validate import _unknown_tools

    assert _unknown_tools("Frobnicate, Bash(git add *") == ["Frobnicate"]
    assert _unknown_tools("Bash(" * 20_000) == []
    assert _unknown_tools('Read, Bash(echo "a)') == []


# ---- body-too-large ----------------------------------------------------------------

def test_body_over_500_lines_is_a_warn(tmp_path):
    _skill(tmp_path, "long-lines", body="\n".join(["line"] * 501) + "\n")

    [finding] = [f for f in _findings(tmp_path)["long-lines"] if f.code == "body-too-large"]
    assert finding.level == "warn"
    assert "501 lines" in finding.message


def test_body_over_5000_tokens_is_a_warn_even_in_few_lines(tmp_path):
    # 20,004 characters is 5,001 tokens at characters / 4.
    _skill(tmp_path, "long-words", body="x" * 20_004 + "\n")

    assert "body-too-large" in _codes(tmp_path, "long-words")


def test_body_at_the_limits_is_not_flagged(tmp_path):
    _skill(tmp_path, "at-limit", body="\n".join(["line"] * 500) + "\n")
    _skill(tmp_path, "tokens-at-limit", body="x" * 20_000)

    assert "body-too-large" not in _codes(tmp_path, "at-limit")
    assert "body-too-large" not in _codes(tmp_path, "tokens-at-limit")


# ---- name-format covers the length ------------------------------------------------

def test_name_over_64_characters_is_a_name_format_error_that_says_the_length(tmp_path):
    name = "a" * 70
    _skill(tmp_path, name)

    message = _message(tmp_path, name, "name-format")
    assert "70 characters" in message and "not kebab-case" not in message


def test_long_name_that_is_not_kebab_case_names_both_problems(tmp_path):
    name = "Bad_" + "a" * 64
    _skill(tmp_path, name)

    message = _message(tmp_path, name, "name-format")
    assert "kebab-case" in message and "characters" in message


def test_name_of_exactly_64_characters_is_ok(tmp_path):
    name = "a" * 64
    _skill(tmp_path, name)

    assert "name-format" not in _codes(tmp_path, name)


def test_name_of_65_characters_is_a_name_format_error(tmp_path):
    name = "a" * 65
    _skill(tmp_path, name)

    assert "name-format" in _codes(tmp_path, name)


def test_clean_skill_has_no_new_findings(tmp_path):
    _skill(tmp_path, "clean", extra="allowed-tools: Read, Grep\n")

    assert _findings(tmp_path)["clean"] == []


# ---- load errors: no frontmatter, unclosed frontmatter ------------------------------

def test_file_without_frontmatter_says_so_instead_of_a_missing_description(tmp_path):
    skill_dir = _write(tmp_path, "plain", "# Plain markdown\n\nNo frontmatter here.\n")

    with pytest.raises(ValueError) as info:
        load_skill(skill_dir)
    message = str(info.value)
    assert "no frontmatter" in message and "'---' line" in message
    assert "missing a 'description'" not in message


def test_unclosed_frontmatter_is_named_as_unclosed(tmp_path):
    skill_dir = _write(tmp_path, "open-block", "---\nname: open-block\ndescription: Use when open.\n# body\n")

    with pytest.raises(ValueError) as info:
        load_skill(skill_dir)
    assert "unclosed frontmatter" in str(info.value)


def test_valid_frontmatter_without_description_keeps_the_missing_description_message(tmp_path):
    skill_dir = _write(tmp_path, "no-desc", "---\nname: no-desc\n---\n\n# body\n")

    with pytest.raises(ValueError, match="missing a 'description'"):
        load_skill(skill_dir)


def test_no_frontmatter_row_is_a_load_error_in_the_manifest(tmp_path):
    _write(tmp_path, "skills/plain", "# Plain markdown\n")

    [entry] = read_skill_manifest(tmp_path)
    assert not entry.loaded
    assert "no frontmatter" in entry.error

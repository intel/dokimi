# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#943: `repo budget` and `skill_listing` count one skill's listing entry alike."""

import pytest

from dokimi.harnesses.skill_treatment import skill_listing
from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree
from dokimi.skills.listing import listing_text_chars

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


@pytest.mark.parametrize("when, chars", [(None, 5), ("", 5), ("x", 9)])
def test_the_separator_is_counted_only_for_a_non_empty_when_to_use(when, chars):
    """#943: `skill_listing` in harnesses/skill_treatment.py counts ' - ' before a non-empty when_to_use."""
    assert listing_text_chars("12345", when) == chars


@pytest.mark.parametrize("when, listed", [
    ("when_to_use: On save.\n", len("Format code. - On save.")),
    ("when_to_use: ''\n", len("Format code.")),
    ("", len("Format code.")),
])
def test_both_commands_count_the_same_entry(tmp_path, when, listed):
    d = tmp_path / ".claude/skills/a"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: a\ndescription: Format code.\n{when}---\nb", encoding="utf-8")
    got = skill_listing(d)
    entries = budget(WorkTree(tmp_path), NAMES, claude_displaces_agents=True)["entries"]
    charged = next(e["chars"] for e in entries if e["kind"] == "skill listing")
    assert got["description_chars"] == listed
    assert charged == 1 + listed


@pytest.mark.parametrize("when_chars, truncated", [(533, False), (534, True)])
def test_both_commands_cut_at_the_same_length(tmp_path, when_chars, truncated):
    d = tmp_path / ".claude/skills/a"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: a\ndescription: {'x' * 1000}\nwhen_to_use: {'w' * when_chars}\n---\nb", encoding="utf-8")
    got = skill_listing(d)
    entries = budget(WorkTree(tmp_path), NAMES, claude_displaces_agents=True)["entries"]
    charged = next(e["chars"] for e in entries if e["kind"] == "skill listing")
    assert got["truncated"] is truncated
    assert charged == 1 + min(got["description_chars"], got["max_desc_chars"])

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Progressive disclosure over nested skill layouts."""

import pytest

from dokimi.skills.loader import discover_skills, load_skill
from dokimi.treatments.tools import ViewSkillTool

SKILL_BODY = """---
name: nested-demo
description: Use when testing nested skill layouts.
---
# nested-demo
"""


@pytest.fixture
def nested_skill(tmp_path):
    """A skill that keeps its detail in subdirectories, as real skills do."""
    root = tmp_path / "nested-demo"
    (root / "references").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    (root / "references" / "topic.md").write_text("# Topic detail", encoding="utf-8")
    (root / "scripts" / "probe.py").write_text("print('probe')\n", encoding="utf-8")
    return root


def test_loader_finds_resources_in_subdirectories(nested_skill):
    skill = load_skill(nested_skill)
    found = {p.relative_to(nested_skill).as_posix() for p in skill.resources}
    assert found == {"references/topic.md", "scripts/probe.py"}


def test_file_picker_offers_nested_markdown_by_relative_path(nested_skill):
    tool = ViewSkillTool(load_skill(nested_skill))
    enum = tool.parameters["properties"]["file"]["enum"]
    assert enum == ["SKILL.md", "references/topic.md"]


def test_nested_file_loads_by_relative_path(nested_skill):
    out = ViewSkillTool(load_skill(nested_skill)).call("references/topic.md")
    assert "Topic detail" in out


def test_manifest_lists_every_bundled_file_relative_to_skill_md(nested_skill):
    manifest = load_skill(nested_skill).resource_manifest()
    assert "references/topic.md" in manifest
    assert "scripts/probe.py" in manifest
    # The judge's problem is existence, so the manifest has to assert it.
    assert "exist" in manifest
    # Paths only — the bodies would not fit the judge's budget.
    assert "Topic detail" not in manifest


def test_manifest_is_empty_for_a_skill_with_no_bundled_files(tmp_path):
    root = tmp_path / "flat-demo"
    root.mkdir()
    (root / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    assert load_skill(root).resource_manifest() == ""


def test_manifest_caps_the_list_and_says_how_many_it_dropped(nested_skill):
    manifest = load_skill(nested_skill).resource_manifest(max_files=1)
    assert "… and 1 more" in manifest


def test_path_traversal_is_refused(nested_skill):
    out = ViewSkillTool(load_skill(nested_skill)).call("../../../../etc/passwd")
    assert "outside the skill directory" in out


def test_single_file_skill_takes_no_parameters(tmp_path):
    root = tmp_path / "flat-demo"
    root.mkdir()
    (root / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    tool = ViewSkillTool(load_skill(root))
    assert tool.parameters == {"type": "object", "properties": {}}


def _write_skill(directory, name, description="Use when testing discovery."):
    directory.mkdir(parents=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory / "SKILL.md"


@pytest.mark.parametrize("dirname", ["template", "templates"])
def test_discovery_skips_a_template_directory(tmp_path, dirname):
    root = tmp_path / "catalog"
    _write_skill(root / dirname, "template-skill", "Replace with description of the skill.")
    _write_skill(root / "linux-perf", "linux-perf", "Profile Linux programs.")
    assert [s.name for s in discover_skills(root)] == ["linux-perf"]


def test_discovery_reports_a_skipped_template(tmp_path):
    root = tmp_path / "catalog"
    _write_skill(root / "template", "template-skill", "Replace with description of the skill.")
    notes: list[str] = []
    assert discover_skills(root, notes=notes) == []
    assert len(notes) == 1 and "template" in notes[0]


def test_discovery_counts_a_mirror_once(tmp_path):
    root = tmp_path / "repo"
    original = _write_skill(root / "skills" / "linux-perf", "linux-perf", "Profile Linux programs.")
    mirror = root / "plugins" / "perf" / "skills" / "linux-perf"
    mirror.mkdir(parents=True)
    (mirror / "SKILL.md").write_bytes(original.read_bytes())
    skills = discover_skills(root)
    assert [s.path for s in skills] == [original]


def test_discovery_reports_a_mirror_it_leaves_out(tmp_path):
    root = tmp_path / "repo"
    _write_skill(root / "skills" / "linux-perf", "linux-perf", "Profile Linux programs.")
    _write_skill(
        root / "plugins" / "perf" / "skills" / "linux-perf", "linux-perf", "Profile Linux programs."
    )
    notes: list[str] = []
    skills = discover_skills(root, notes=notes)
    assert len(skills) == 1
    assert len(notes) == 1
    assert "plugins" in notes[0] and "identical to" in notes[0]


def test_discovery_keeps_same_named_skills_whose_text_differs(tmp_path):
    root = tmp_path / "repo"
    _write_skill(root / "skills" / "linux-perf", "linux-perf", "Profile Linux programs.")
    _write_skill(
        root / "plugins" / "perf" / "skills" / "linux-perf",
        "linux-perf",
        "A different, older description.",
    )
    assert len(discover_skills(root)) == 2


def test_discovery_skips_a_template_directory_at_any_depth(tmp_path):
    root = tmp_path / "catalog"
    _write_skill(root / "a" / "templates" / "b", "nested", "Replace with description.")
    _write_skill(root / "real", "real", "Profile Linux programs.")
    assert [s.name for s in discover_skills(root)] == ["real"]


def test_a_root_under_a_templates_directory_is_still_discovered(tmp_path):
    root = tmp_path / "templates" / "cat"
    _write_skill(root / "linux-perf", "linux-perf", "Profile Linux programs.")
    assert [s.name for s in discover_skills(root)] == ["linux-perf"]


def test_a_directory_merely_containing_template_is_not_a_template(tmp_path):
    root = tmp_path / "catalog"
    _write_skill(root / "my-template-tool", "my-template-tool", "Tool for templates.")
    assert [s.name for s in discover_skills(root)] == ["my-template-tool"]


def test_identical_copies_at_equal_depth_keep_the_path_that_sorts_first(tmp_path):
    root = tmp_path / "repo"
    first = _write_skill(root / "a" / "perf", "perf", "Profile Linux programs.")
    _write_skill(root / "b" / "perf", "perf", "Profile Linux programs.")
    notes: list[str] = []
    assert [s.path for s in discover_skills(root, notes=notes)] == [first]
    assert notes == [f"left out {root / 'b' / 'perf' / 'SKILL.md'}: identical to {first}"]


def test_copies_differing_only_by_line_endings_are_both_kept(tmp_path):
    root = tmp_path / "repo"
    original = _write_skill(root / "a" / "perf", "perf", "Profile Linux programs.")
    crlf = root / "b" / "perf"
    crlf.mkdir(parents=True)
    (crlf / "SKILL.md").write_bytes(original.read_bytes().replace(b"\n", b"\r\n"))
    assert len(discover_skills(root)) == 2


def test_identical_text_in_differently_named_directories_is_two_skills(tmp_path):
    """With no frontmatter ``name`` the directory names the skill, so these differ."""
    root = tmp_path / "repo"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_bytes(b"---\ndescription: Use when profiling.\n---\n# b\n")
    (root / "beta" / "helper.sh").write_bytes(b"echo\n")
    skills = discover_skills(root)
    assert [(s.name, len(s.resources)) for s in skills] == [("alpha", 0), ("beta", 1)]


def test_a_mirror_in_a_renamed_directory_is_still_counted_once(tmp_path):
    root = tmp_path / "repo"
    original = _write_skill(root / "skills" / "perf", "perf", "Profile Linux programs.")
    _write_skill(root / "plugins" / "p" / "perf-copy", "perf", "Profile Linux programs.")
    assert [s.path for s in discover_skills(root)] == [original]


def test_the_mirror_note_names_the_dropped_and_the_kept_path(tmp_path):
    root = tmp_path / "repo"
    original = _write_skill(root / "skills" / "perf", "perf", "Profile Linux programs.")
    mirror = _write_skill(
        root / "plugins" / "p" / "skills" / "perf", "perf", "Profile Linux programs."
    )
    notes: list[str] = []
    discover_skills(root, notes=notes)
    assert notes == [f"left out {mirror}: identical to {original}"]


def test_the_skill_catalog_arm_prints_what_discovery_left_out_to_stderr(tmp_path, capsys):
    """A template or mirror dropped from a `skill-catalog:` arm is named on stderr."""
    from dokimi.treatments.factory import create_treatment

    real = tmp_path / "skills" / "web"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("---\nname: web\ndescription: build web pages\n---\n\n# Web\n",
                                   encoding="utf-8")
    template = tmp_path / "template"
    template.mkdir()
    (template / "SKILL.md").write_text("---\nname: blank\ndescription: scaffold\n---\n\n# B\n",
                                       encoding="utf-8")

    create_treatment(f"skill-catalog:{tmp_path}")

    err = capsys.readouterr().err
    assert f"left out {template / 'SKILL.md'}: a template, not a skill" in err

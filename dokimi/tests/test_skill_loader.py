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


@pytest.mark.parametrize("vendored", ["node_modules/pkg", ".git/modules/sub", ".venv/lib/python3.12/site-packages/pkg"])
def test_discovery_skips_vendored_copies(tmp_path, vendored):
    root = tmp_path / "repo"
    _write_skill(root / "skills" / "linux-perf", "linux-perf", "Profile Linux programs.")
    # A different version of the same skill, so the mirror rule would not drop it.
    _write_skill(root / vendored / "skills" / "linux-perf", "linux-perf", "Profile programs, v0.")
    _write_skill(root / vendored / "skills" / "other", "other", "Someone else's skill.")
    notes: list[str] = []
    assert [s.name for s in discover_skills(root, notes=notes)] == ["linux-perf"]
    assert notes == []


@pytest.mark.parametrize(
    "name",
    [
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "site-packages",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        "__pycache__",
    ],
)
def test_discovery_skips_each_vendored_name(tmp_path, name):
    root = tmp_path / "repo"
    _write_skill(root / "skills" / "a", "a", "Skill a.")
    _write_skill(root / "skills" / "a" / name / "pkg", "b", "Vendored b.")
    assert [s.name for s in discover_skills(root)] == ["a"]


def test_discovery_walks_a_root_inside_a_vendored_directory(tmp_path):
    root = tmp_path / "node_modules" / "pkg" / "skills"
    _write_skill(root / "a", "a", "Skill a.")
    assert [s.name for s in discover_skills(root)] == ["a"]


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


@pytest.mark.parametrize("change", [lambda b: b + b"\n", lambda b: b.replace(b"Linux", b"linux")])
def test_copies_differing_only_by_whitespace_or_case_are_both_kept(tmp_path, change):
    root = tmp_path / "repo"
    original = _write_skill(root / "a" / "perf", "perf", "Profile Linux programs.")
    near = root / "b" / "perf"
    near.mkdir(parents=True)
    (near / "SKILL.md").write_bytes(change(original.read_bytes()))
    assert len(discover_skills(root)) == 2


def test_discovery_returns_skills_in_path_order_not_depth_order(tmp_path):
    root = tmp_path / "catalog"
    deep = _write_skill(root / "a" / "b", "deep", "Profile Linux programs.")
    shallow = _write_skill(root / "z", "shallow", "Build web pages.")
    assert [s.path for s in discover_skills(root)] == [deep, shallow]


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


def test_a_blank_frontmatter_name_is_refused(tmp_path):
    skill = tmp_path / "perf"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        '---\nname: "  "\ndescription: Profile.\n---\n# perf\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="empty 'name'"):
        load_skill(skill)


def test_a_skill_md_that_is_a_directory_is_not_a_skill(tmp_path):
    skill = tmp_path / "perf"
    (skill / "SKILL.md").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="No SKILL.md found"):
        load_skill(skill)


def test_discovery_ignores_a_directory_named_skill_md(tmp_path):
    root = tmp_path / "catalog"
    (root / "broken" / "SKILL.md").mkdir(parents=True)
    _write_skill(root / "linux-perf", "linux-perf", "Profile Linux programs.")
    assert [s.name for s in discover_skills(root)] == ["linux-perf"]


def test_discovery_ignores_a_dangling_skill_md_symlink(tmp_path):
    root = tmp_path / "catalog"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "SKILL.md").symlink_to(tmp_path / "missing.md")
    _write_skill(root / "linux-perf", "linux-perf", "Profile Linux programs.")
    assert [s.name for s in discover_skills(root)] == ["linux-perf"]


def test_arms_run_exits_2_with_a_message_when_skill_md_is_a_directory(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    skill = tmp_path / "perf"
    (skill / "SKILL.md").mkdir(parents=True)
    questions = tmp_path / "q.json"
    questions.write_text('{"questions": [{"id": "q1", "question": "How?"}]}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "arms", "run", "--product", "lib", "--questions", str(questions),
                                     "--arms", f"skill:{skill}", "--out-json", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "No SKILL.md found" in capsys.readouterr().err


@pytest.mark.parametrize("quoted", ['"perf "', '" perf"', r'"\tperf\n"'])
def test_a_frontmatter_name_is_stripped(tmp_path, quoted):
    skill = tmp_path / "perf"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: {quoted}\ndescription: Profile.\n---\n# perf\n", encoding="utf-8"
    )
    assert load_skill(skill).name == "perf"

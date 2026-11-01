# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``guidance:format=<fmt>`` and ``guidance:as-skill`` arms (contract §3, step 5). No docker, no LLM."""

from __future__ import annotations

import types

import pytest
import yaml

from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.arms import slug, tree_sha256
from dokimi.repos.guidance_format import FormatError, restore, to_format
from dokimi.repos.workspace import (
    GUIDANCE_SKILL_DESCRIPTION,
    PrepError,
    _apply_as_skill,
    _apply_format,
    export_eval_dir,
    load_tasks,
    parse_arm,
    prepare_workspace,
    source_git_dir,
)
from repo_eval_fixture import make_repo, write

RICH = """Intro line before any heading.

# Project
Use `make check`.

## Build & test
Run `make <target>` with "quotes".

```bash
# a shell comment, not a heading
make check
```

### Deep detail
Text.

## Style
- keep it short

# Other
tail"""


@pytest.mark.parametrize("text", [
    RICH,
    "",
    "no heading at all",
    "# only\n",
    "# a\n### skipped level\n# b\n",
    "## start deeper\n# then shallower\n",
    "# no trailing newline",
    "#no-space is body\n##  two spaces is body\n####### seven is body\n",
    "# trailing spaces  \r\nbody\r\n",
    "# Title with </section> and \"quotes\" & <tags>\nbody\n",
    "~~~\n# fenced with tildes\n~~~\n# real\n",
])
def test_xml_round_trips_the_markdown_exactly(text):
    assert restore(to_format(text, "xml")) == text


def test_xml_wraps_each_heading_in_a_section_closed_before_its_peer():
    assert to_format("# A\nbody\n## B\nmore\n# C\n", "xml") == (
        "<guidance>\n"
        '<section level="1" title="A">\nbody\n'
        '<section level="2" title="B">\nmore\n</section>\n'
        "</section>\n"
        '<section level="1" title="C">\n\n'
        "</section>\n"
        "</guidance>"
    )


def test_a_fenced_hash_line_is_body_text_not_a_section():
    rendered = to_format("```\n# not a heading\n```\n", "xml")
    assert "<section" not in rendered and "# not a heading" in rendered


def test_a_heading_title_is_escaped_in_the_tag_and_restored():
    rendered = to_format('# A "b" & <c>\n', "xml")
    assert 'title="A &quot;b&quot; &amp; &lt;c&gt;"' in rendered
    assert restore(rendered) == '# A "b" & <c>\n'


@pytest.mark.parametrize("line", ["</section>", '<section level="2" title="x">'])
def test_a_body_line_that_looks_like_a_tag_is_refused(line):
    with pytest.raises(FormatError):
        to_format(f"# A\n{line}\n", "xml")


def test_an_unknown_format_is_refused():
    with pytest.raises(FormatError):
        to_format("# A\n", "poml")


def test_restore_refuses_text_that_is_not_a_rendering():
    with pytest.raises(FormatError):
        restore("# plain markdown\n")


def test_a_format_arm_parses_as_its_own_guidance_part():
    arm = parse_arm("guidance:format=xml")
    assert arm == "guidance:format=xml" and arm.base == "guidance:format=xml"
    assert parse_arm("guidance:format=xml+skill:lint").base == "guidance:format=xml"


@pytest.mark.parametrize("spec", ["guidance:format=md", "guidance:format=poml", "guidance:format=", "guidance:format=XML"])
def test_an_unknown_or_duplicate_format_is_a_prep_error(spec):
    with pytest.raises(PrepError):
        parse_arm(spec)


def test_two_guidance_parts_are_refused_for_formats_too():
    with pytest.raises(PrepError):
        parse_arm("guidance:format=xml+guidance:as-skill")


def test_as_skill_is_a_guidance_part_joined_with_skills():
    assert parse_arm("guidance:as-skill") == "guidance:as-skill"
    arm = parse_arm("guidance:as-skill+skill:lint")
    assert arm == "guidance:as-skill+skill:lint" and arm.base == "guidance:as-skill"


def _toy_files(tmp_path):
    root, _ = make_repo(tmp_path)
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    content = export_eval_dir(tree, manifest, tmp_path / "content")
    tasks = {t.id: t for t in load_tasks(content, manifest)}
    return {"git_dir": source_git_dir(source, tree), "manifest": manifest, "tasks": tasks, "tmp": tmp_path}


def _prep(toy, arm):
    meta = prepare_workspace(toy["git_dir"], toy["manifest"], toy["tasks"]["fix_add"], arm, toy["tmp"] / "rd")
    return meta, toy["tmp"] / "rd" / "workspace"


def test_a_format_arm_renders_every_guidance_file_of_the_workspace(tmp_path):
    toy = _toy_files(tmp_path)
    meta, ws = _prep(toy, "guidance:format=xml")
    assert (ws / "AGENTS.md").read_text() == to_format("# guidance\n", "xml")
    assert (ws / "sub/AGENTS.md").read_text() == to_format("# sub guidance\n", "xml")
    assert restore((ws / "AGENTS.md").read_text()) == "# guidance\n"
    assert meta["arm"] == "guidance:format=xml" and not meta.get("skills")


def test_an_as_skill_arm_moves_the_guidance_into_one_skill(tmp_path):
    toy = _toy_files(tmp_path)
    meta, ws = _prep(toy, "guidance:as-skill")
    name = slug(f"{toy['manifest'].id}-guidance")
    assert not (ws / "AGENTS.md").exists() and not (ws / "sub/AGENTS.md").exists()
    skill = (ws / ".claude/skills" / name / "SKILL.md").read_text()
    assert skill.startswith(f"---\nname: {name}\ndescription: {GUIDANCE_SKILL_DESCRIPTION}\n---\n")
    assert "## File: AGENTS.md\n\n# guidance\n" in skill and "## File: sub/AGENTS.md\n\n# sub guidance\n" in skill
    assert meta["skills"] == [{"name": name, "path": f".claude/skills/{name}",
                               "sha256": tree_sha256(ws / ".claude/skills" / name)}]
    assert "guidance_skill" not in meta
    assert sorted(meta["arm_files"]) == ["AGENTS.md", "sub/AGENTS.md"]


def test_format_keeps_a_symlink_as_a_link_and_renders_its_target_once(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# A\nbody\n")
    (ws / "CLAUDE.md").symlink_to("AGENTS.md")
    written, meta = _apply_format(ws, ["AGENTS.md", "CLAUDE.md"], "xml")
    assert written == ["AGENTS.md"] and meta == {"guidance_links_kept": ["CLAUDE.md"]}
    assert (ws / "CLAUDE.md").is_symlink()
    assert (ws / "CLAUDE.md").read_text() == to_format("# A\nbody\n", "xml")


def test_format_refuses_a_file_that_is_not_utf8(tmp_path):
    ws = tmp_path / "ws"
    (ws / "AGENTS.md").parent.mkdir(parents=True)
    (ws / "AGENTS.md").write_bytes(b"\xff\xfe# A\n")
    with pytest.raises(PrepError, match="AGENTS.md"):
        _apply_format(ws, ["AGENTS.md"], "xml")


def test_format_refuses_a_file_that_would_not_restore(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# A\n</section>\n")
    with pytest.raises(PrepError, match="AGENTS.md"):
        _apply_format(ws, ["AGENTS.md"], "xml")


def test_format_and_as_skill_need_a_guidance_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(PrepError):
        _apply_format(ws, [], "xml")
    with pytest.raises(PrepError):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), [])


def test_as_skill_refuses_a_skill_the_workspace_already_has(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# A\n")
    write(ws, ".claude/skills/toy-guidance/SKILL.md", "---\nname: toy-guidance\ndescription: x\n---\n")
    with pytest.raises(PrepError, match="already has"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])


def test_as_skill_refuses_a_claude_directory_that_is_a_link(tmp_path):
    ws, host = tmp_path / "ws", tmp_path / "host"
    write(ws, "AGENTS.md", "# A\n")
    host.mkdir()
    (ws / ".claude").symlink_to(host)
    with pytest.raises(PrepError, match="symlink"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])
    assert not any(host.iterdir())


def test_the_text_of_each_guidance_file_is_in_the_skill_verbatim(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", RICH)
    write(ws, "src/AGENTS.md", "# src\nno final newline")
    _, meta = _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md", "src/AGENTS.md"])
    skill = (ws / ".claude/skills/toy-guidance/SKILL.md").read_text()
    assert RICH in skill and "# src\nno final newline\n" in skill
    assert meta["guidance_skill"]["name"] == "toy-guidance"


def test_the_skill_frontmatter_is_valid_yaml_with_name_and_description(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# A\n")
    _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])
    skill = (ws / ".claude/skills/toy-guidance/SKILL.md").read_text()
    frontmatter = yaml.safe_load(skill.split("---\n")[1])
    assert frontmatter == {"name": "toy-guidance", "description": GUIDANCE_SKILL_DESCRIPTION}


def test_as_skill_refuses_a_file_that_is_not_utf8_as_a_prep_error(tmp_path):
    ws = tmp_path / "ws"
    (ws / "AGENTS.md").parent.mkdir(parents=True)
    (ws / "AGENTS.md").write_bytes(b"\xff# A\n")
    with pytest.raises(PrepError, match="AGENTS.md"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])


def test_as_skill_packages_a_linked_guidance_file_once(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# A\nbody\n")
    (ws / "CLAUDE.md").symlink_to("AGENTS.md")
    _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md", "CLAUDE.md"])
    skill = (ws / ".claude/skills/toy-guidance/SKILL.md").read_text()
    assert skill.count("# A\nbody\n") == 1 and "## File: CLAUDE.md" not in skill
    assert not (ws / "CLAUDE.md").is_symlink() and not (ws / "CLAUDE.md").exists()


def test_as_skill_refuses_a_link_to_a_file_that_is_not_guidance(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "docs/guide.md", "# guide\n")
    write(ws, "AGENTS.md", "# A\n")
    (ws / "CLAUDE.md").symlink_to("docs/guide.md")
    with pytest.raises(PrepError, match="not a guidance file"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md", "CLAUDE.md"])

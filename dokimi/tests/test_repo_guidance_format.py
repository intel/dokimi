# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``guidance:format=<fmt>`` and ``guidance:as-skill`` arms (contract §3, step 5). No docker, no LLM."""

from __future__ import annotations

import re
import types

import pytest
import yaml

from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.arms import guidance_skill_name, tree_sha256
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


@pytest.mark.parametrize("text, sections", [
    ("- ```\n  echo hi\n  ```\n# Real\n", ["Real"]),  # the closer is indented to the item's content column
    ("- ```\n  echo hi\n# Real\n", ["Real"]),  # a line less indented than the item ends the item and its fence
    ("- a\n  - ```\n    # not\n    ```\n# Real\n", ["Real"]),  # a nested item
    ("1. ```\n   # not\n   ```\n# Real\n", ["Real"]),  # an ordered item
    ("- ```\n# Real\n```\n# Real2\n", ["Real"]),  # the item ends; the ``` at column 0 opens a fence over Real2
    ("- use ``` for code\n# Real\n", ["Real"]),  # a list item that is prose, not a fence
    ("- - ```\n    # not\n    ```\n# Real\n", ["Real"]),  # two markers on one line
    ("-      ```\n# Real\n", ["Real"]),  # five spaces after the marker: indented code, not a fence
    ("- ```\n\t# not\n\t```\n# Real\n", ["Real"]),  # a tab is four columns of indentation: the item's content
    ("- Example:\n  ```\n  code\n# Real\n", ["Real"]),  # a fence on a continuation line takes the item's column
    ("1. Step\n\n   ```sh\n   x\n# Real\n", ["Real"]),  # the same after a blank line, ordered item
    ("- Example:\n  ```\n  code\n  ```\n# Real\n", ["Real"]),  # closed inside the item: the heading is a section
    ("- Example:\n\n```\n# not\n```\n# Real\n", ["Real"]),  # a column-0 fence after a blank line ends the item
    ("- a\n  - b\n  ```\n  code\n# Real\n", ["Real"]),  # a fence on the outer item's line after a nested item
    ("-\t```\n    echo hi\n    ```\n# Real\n", ["Real"]),  # a tab after a bullet is four columns: the item's fence
    ("1.\t```\n    echo hi\n    ```\n# Real\n", ["Real"]),  # the same after an ordered marker
    ("-\t```\n  echo hi\n  ```\n# Real\n", []),  # 2 spaces is under the item's column 4: the item ends, as in CommonMark
    ("- a\n\t```\n```\n# Real\n", []),  # a tab-indented continuation fence (a pin: the sections match the spaces form)
    ("- Example:\ncontinued text\n  ```\n  code\n# Real\n", ["Real"]),  # a lazy paragraph line keeps the item open
    ("- a\nlazy\n```\n# not\n```\n# Real\n", ["Real"]),  # a fence at column 0 is a block start, not a lazy line
    ("- a\nlazy\n# Real\n", ["Real"]),  # a heading is a block start too
    ("- a\n> quote\n  ```\n# Real\n", []),  # a block quote is a block start: the item ends, the fence opens at top level
    ("- a\n---\n  ```\n# x\n", []),  # a rule is a block start, not a lazy line
    ("- a\n# H\n  ```\n# x\n", ["H"]),  # a heading ends the item too: only H is a section, the fence holds x
    ("- a\n\nlazy\n  ```\n# x\n", []),  # a blank line ends the item: the next line is not a lazy continuation
    ("-\n  ```\n# Real\n", ["Real"]),  # an empty bullet is an item: its fence is at the content column 2
    ("1.\n   ```\n# Real\n", ["Real"]),  # an empty ordered item: its content column is 3
    ("-\n   ```\n```\n# Real\n", []),  # the column-0 fence ends the empty item; the hash line is code
    ("-\n  ```\n# H\n```\n# Real\n", ["H"]),  # H ends the item and its fence; the fence at column 0 holds Real
    ("-\n\n   ```\n# Real\n", []),  # an empty item ends at a blank line: the indented fence is top-level
    ("- - -\n  ```\n# not a heading\n  ```\n", []),  # a thematic break is no list item
    ("* * *\n  ```\n# not a heading\n  ```\n", []),  # the same with asterisks
    ("paragraph\n2. ```\n   ```\n# Real\n", []),  # a numbered marker other than 1 cannot interrupt a paragraph
    ("- a\n2. x\n  ```\n# Real\n", []),  # 2. x starts a new list (the item is not continued), so the fence is top-level
    ("- a\n\n      code\noutside\n  ```\n# not a heading\n", []),  # indented code is not paragraph text
    ("- > quoted\nlazy\n  ```\n# Real\n", ["Real"]),  # a lazy line continues the quoted paragraph in the item
    ("> quoted\nlazy\n  ```\n# Real\n", []),  # the same at top level: the fence opens and holds Real
    ("- > # H\nlazy\n  ```\n# x\n", []),  # a quoted heading is no paragraph: the lazy line ends the item
    ("- >     code\nlazy\n  ```\n# Real\n", []),  # indented code in the quote is no paragraph: lazy ends the item
    ("para\n- >     code\nlazy\n  ```\n# Real\n", []),  # a new item after a paragraph: the same, no lazy continuation
    ("- > a\n  >     more\nlazy\n  ```\n# Real\n", ["Real"]),  # an open quoted paragraph takes an indented line too
])
def test_a_fence_opened_by_a_list_item_holds_body_text_only_inside_the_item(text, sections):
    assert re.findall(r'title="([^"]*)"', to_format(text, "xml")) == sections


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
    "---\npaths:\n  - src/**\n# a yaml comment\n---\n# Rules\n",
    "---\n---\n# Rules\n",
    "---\nunclosed\n# Rules\n",
    "<!--\n# commented out\n-->\n# Real\n",
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


@pytest.mark.parametrize("text, sections", [
    ("<!--\n# commented out\n-->\n# Real\n", ["Real"]),  # a comment block spans lines: its heading is body text
    ("<!-- # one line -->\n# Real\n", ["Real"]),  # closed on its own line: nothing after it is in the comment
    ("  <!--\n# a\n  -->\n# Real\n", ["Real"]),  # up to three spaces of indentation
    ("<!--\n# a\n", []),  # unclosed: the rest of the file is the comment
    ("text <!--\n# Real\n-->\n", ["Real"]),  # a comment inside a paragraph line is no block: the heading is a section
    ("- item\n  <!--\n# Real\n-->\n", ["Real"]),  # the item ends at the heading, and its comment block with it
    ("- item\n<!--\n# Real\n-->\n", []),  # a lazy comment line starts a block outside the item: the comment holds on
])
def test_a_heading_inside_an_html_comment_block_is_body_text(text, sections):
    assert re.findall(r'title="([^"]*)"', to_format(text, "xml")) == sections


def test_a_yaml_front_matter_block_stays_verbatim_above_the_root():
    text = "---\npaths:\n  - src/**\n# a yaml comment\n---\n# Rules\n"
    assert to_format(text, "xml") == (
        "---\npaths:\n  - src/**\n# a yaml comment\n---\n"
        "<guidance>\n"
        '<section level="1" title="Rules">\n\n'
        "</section>\n"
        "</guidance>"
    )


def test_a_crlf_front_matter_block_stays_verbatim_above_the_root():
    text = "---\r\npaths:\r\n  - src/**\r\n---\r\n# Rules\r\n"
    rendered = to_format(text, "xml")  # the delimiters keep their CR; the match ignores it
    assert rendered.startswith("---\r\npaths:\r\n  - src/**\r\n---\r\n<guidance>\n")
    assert restore(rendered) == text


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
    name = guidance_skill_name(toy["manifest"].id)
    assert not (ws / "AGENTS.md").exists() and not (ws / "sub/AGENTS.md").exists()
    skill = (ws / ".claude/skills" / name / "SKILL.md").read_text()
    assert skill.startswith(f"---\nname: {name}\ndescription: {GUIDANCE_SKILL_DESCRIPTION}\n---\n")
    assert "## File: AGENTS.md\n\n# guidance\n" in skill and "## File: sub/AGENTS.md\n\n# sub guidance\n" in skill
    assert "skills" not in meta  # the packaged skill is apart from the catalog's skills (its usage is measured apart)
    assert meta["guidance_skill"] == {"name": name, "path": f".claude/skills/{name}",
                                      "sha256": tree_sha256(ws / ".claude/skills" / name)}
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


# -- review of #333: the fence delimiter, the links, the bytes on disk ----------------------------------------


def test_a_fence_closes_only_on_its_own_delimiter_and_length():
    assert "<section" not in to_format("````\n```\n# code comment\n````\n", "xml")
    assert "<section" not in to_format("```\n~~~\n# code comment\n```\n", "xml")
    rendered = to_format("````md\n```text\n# code comment\n````\n# real\n", "xml")
    assert rendered.count("<section") == 1 and '<section level="1" title="real">' in rendered


def test_a_format_link_to_a_file_the_globs_do_not_match_is_a_prep_error(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "docs/guide.md", "# guide\n")
    (ws / "AGENTS.md").symlink_to("docs/guide.md")
    with pytest.raises(PrepError, match="not a guidance file"):
        _apply_format(ws, ["AGENTS.md"], "xml")


def test_a_link_cycle_is_refused_and_nothing_is_deleted(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").symlink_to("CLAUDE.md")
    (ws / "CLAUDE.md").symlink_to("AGENTS.md")
    with pytest.raises(PrepError, match="cycle"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md", "CLAUDE.md"])
    assert (ws / "AGENTS.md").is_symlink() and (ws / "CLAUDE.md").is_symlink()
    with pytest.raises(PrepError, match="cycle"):
        _apply_format(ws, ["AGENTS.md", "CLAUDE.md"], "xml")


def test_format_and_as_skill_write_the_bytes_they_read_with_no_newline_translation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_bytes(b"# A\r\nbody\r\n")
    _apply_format(ws, ["AGENTS.md"], "xml")
    assert (ws / "AGENTS.md").read_bytes() == to_format("# A\r\nbody\r\n", "xml").encode("utf-8")
    (ws / "AGENTS.md").write_bytes(b"# A\r\nbody\r\n")
    _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])
    assert b"body\r\n" in (ws / ".claude/skills/toy-guidance/SKILL.md").read_bytes()


def test_a_fence_opener_is_one_only_where_markdown_accepts_it():
    # a backtick in the info string: not a fence, so the heading after the line is a section
    assert '<section level="1" title="Heading">' in to_format("```foo`bar\n# Heading\n", "xml")
    # a tab is four columns of indentation: indented code, not a fence
    assert '<section level="1" title="H">' in to_format("\t```\n# H\n", "xml")


def test_a_fence_closes_only_on_its_own_character_and_on_no_info_string():
    # a tilde run opens a fence: the comment in it is body text
    rendered = to_format("~~~\n# comment\n~~~\n# real\n", "xml")
    assert 'title="comment"' not in rendered and '<section level="1" title="real">' in rendered
    # a ``` line followed by an info string does not close a ``` fence
    rendered = to_format("```\n```bash\n# comment\n```\n# real\n", "xml")
    assert rendered.count("<section") == 1 and 'title="real"' in rendered


def _a_link_through_a_linked_directory(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "other/AGENTS.md", "# other\n")
    write(ws, "AGENTS.md", "# root\n")
    (ws / "other" / "sub").mkdir()
    (ws / "sub").symlink_to("other/sub", target_is_directory=True)
    (ws / "CLAUDE.md").symlink_to("sub/../AGENTS.md")  # the filesystem reaches other/AGENTS.md
    return ws


def _run_link_caller(caller, ws, matched):
    if caller == "format":
        return _apply_format(ws, matched, "xml")
    return _apply_as_skill(ws, types.SimpleNamespace(id="toy"), matched)


@pytest.mark.parametrize("caller", ["format", "as-skill"])
def test_a_link_through_a_linked_directory_reaches_what_the_filesystem_reaches(tmp_path, caller):
    ws = _a_link_through_a_linked_directory(tmp_path)
    with pytest.raises(PrepError, match="not a guidance file"):
        _run_link_caller(caller, ws, ["AGENTS.md", "CLAUDE.md"])


def test_a_link_through_a_linked_directory_to_a_matched_file_is_kept(tmp_path):
    ws = _a_link_through_a_linked_directory(tmp_path)
    _, meta = _apply_format(ws, ["AGENTS.md", "other/AGENTS.md", "CLAUDE.md"], "xml")
    assert meta == {"guidance_links_kept": ["CLAUDE.md"]}


@pytest.mark.parametrize("caller", ["format", "as-skill"])
def test_a_link_in_a_subdirectory_resolves_from_its_own_directory(tmp_path, caller):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# root\n")
    write(ws, "sub/AGENTS.md", "# sub, not matched\n")
    (ws / "sub" / "CLAUDE.md").symlink_to("AGENTS.md")  # sub/AGENTS.md, not the root file
    with pytest.raises(PrepError, match="not a guidance file"):
        _run_link_caller(caller, ws, ["AGENTS.md", "sub/CLAUDE.md"])


@pytest.mark.parametrize("caller", ["format", "as-skill"])
def test_a_dangling_link_is_a_prep_error(tmp_path, caller):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# a\n")
    (ws / "CLAUDE.md").symlink_to("nowhere.md")
    with pytest.raises(PrepError, match="dangling"):
        _run_link_caller(caller, ws, ["AGENTS.md", "CLAUDE.md"])


def test_as_skill_does_not_write_through_a_linked_skills_directory(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# a\n")
    host = tmp_path / "host"
    host.mkdir()
    (ws / ".claude").mkdir()
    (ws / ".claude" / "skills").symlink_to(host, target_is_directory=True)
    with pytest.raises(PrepError, match="symlink"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])
    assert list(host.iterdir()) == []


def test_as_skill_refuses_a_dangling_link_at_the_skill_directory(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# a\n")
    host = tmp_path / "host"
    host.mkdir()
    (ws / ".claude" / "skills").mkdir(parents=True)
    (ws / ".claude" / "skills" / "toy-guidance").symlink_to(host / "nowhere", target_is_directory=True)
    with pytest.raises(PrepError, match="already has"):
        _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])
    assert list(host.iterdir()) == []


def test_as_skill_packages_beside_a_skill_the_base_ships_without_a_collision(tmp_path):
    ws = tmp_path / "ws"
    write(ws, "AGENTS.md", "# a\n")
    write(ws, ".claude/skills/lint/SKILL.md", "---\nname: lint\n---\n")
    _, meta = _apply_as_skill(ws, types.SimpleNamespace(id="toy"), ["AGENTS.md"])
    assert (ws / ".claude/skills/lint/SKILL.md").read_text(encoding="utf-8") == "---\nname: lint\n---\n"
    assert (ws / ".claude/skills/toy-guidance/SKILL.md").is_file()
    assert meta["guidance_skill"]["path"] == ".claude/skills/toy-guidance"

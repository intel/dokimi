# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#942: ``catalog check`` findings say where they are (``line``, ``path``) and, for the mechanical codes, the fix.

A finding row carries ``line`` (1-based, in ``SKILL.md``, or in ``path`` when that is set) and ``path`` (a bundled
file, relative to the skill directory) only when the check knows them (docs/catalog-jsonl.md). The messages of
``name-mismatch``, ``name-format``, ``name-missing``, ``description-too-long``, ``script-crlf``, ``link-missing``,
``path-missing`` and ``frontmatter-unknown-key`` end with the edit that clears them. ``tests/fixtures/finding_codes``
holds one skill per code that can be raised offline.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.test_catalog_ignore import _run

from dokimi.external.finding_lines import key_line, link_line, path_needles, text_line, written_path
from dokimi.external.validate import FINDING_CODES, NAME_MAX, NAME_PATTERN

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "finding_codes"
OK = "Formats tax tables. Use when the user asks for a tax table."
# An imperative verb opens a clause: the start, or right after "; ", ". ", ", ", ": ", "(" or "or ".
FIX = re.compile(
    r"(?:^|[;.:,]\s+|\(|\bor\s+)['\"`]?(?:add|delete|drop|fix|move|pin|quote|read|remove|rename|replace|save|say|"
    r"scope|set|shorten|split|use)\b",
    re.I,
)
# Codes `catalog check` cannot raise from files on disk, with what each needs.
NOT_ON_DISK = {
    "link-dead": "--online and a network answer",
    "package-not-found": "--online and a registry answer",
    "not-loaded": "review_skill_dir on a SKILL.md the loader refuses",
    "unreadable": "a SKILL.md that loads and then fails to read",
    "listing-over-budget": "--listing-budget",
    "bundle-file-unchecked": "a bundled file the scan could not read",
    "not-a-skill-no-frontmatter": "a plain Markdown file named as a skill",
    "opencode-no-description": "a skill under .opencode/skills",
    "unsupported-skill-format": "a skill of a host with no spec",
    "license-dropped-from-upstream": "--upstream",
    "risk-inherited-from-upstream": "--upstream",
    "upstream-stale": "--upstream",
}
# Raised from a tool on the PATH (ruff, shellcheck), so the fixture test does not depend on it.
NEEDS_TOOL = {"script-lint"}


def _skill(root: Path, rel: str, *, name: str | None = None, desc: str = OK, extra: str = "", body: str = "Body.\n") -> Path:
    directory = root / rel
    directory.mkdir(parents=True, exist_ok=True)
    name = directory.name if name is None else name
    head = (f"name: {name}\n" if name else "") + f"description: {desc}\n" + extra
    (directory / "SKILL.md").write_text(f"---\n{head}---\n{body}", encoding="utf-8")
    return directory


def _findings(root: Path, capsys, code: str) -> list[dict]:
    out, _ = _run(["--root", str(root), "--kind", "skills", "--format", "json"], capsys)
    return [f for row in json.loads(out)["skills"] for f in row["findings"] if f["code"] == code]


def _one(root: Path, capsys, code: str) -> dict:
    [found] = _findings(root, capsys, code)
    return found


@pytest.mark.parametrize("code, build", [
    ("name-mismatch", lambda r: _skill(r, "a", name="other-name")),
    ("name-format", lambda r: _skill(r, "Bad_Name")),
    ("name-missing", lambda r: _skill(r, "a", name="")),
    ("description-too-long", lambda r: _skill(r, "a", desc="Use when asked. " + "word " * 220)),
    ("script-crlf", lambda r: (_skill(r, "a") / "run.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")),
    ("link-missing", lambda r: _skill(r, "a", body="See [notes](notes.md).\n")),
    ("path-missing", lambda r: _skill(r, "a", body="Run `scripts/build.py` first.\n")),
    ("frontmatter-unknown-key", lambda r: _skill(r, "a", extra="descripton: typo\n")),
])
def test_the_message_ends_with_the_fix(tmp_path, capsys, code, build):
    """#942: each mechanical code states the edit that clears it."""
    build(tmp_path)
    for found in _findings(tmp_path, capsys, code):
        assert FIX.search(found["message"]), found["message"]


def test_name_missing_at_the_root_still_says_to_add_the_name(tmp_path, capsys):
    """#942: the root SKILL.md has no directory name to offer, so the fix names the key only."""
    (tmp_path / "SKILL.md").write_text(f"---\ndescription: {OK}\n---\nBody.\n", encoding="utf-8")
    found = _one(tmp_path, capsys, "name-missing")
    assert found["message"].endswith("add a 'name:' line")


def test_a_missing_link_names_its_line(tmp_path, capsys):
    """#942: ``line`` is the line of SKILL.md that holds the link (4 frontmatter lines, then the body)."""
    _skill(tmp_path, "a", body="Intro.\n\nSee [notes](notes.md).\n")
    found = _one(tmp_path, capsys, "link-missing")
    assert found["line"] == 7
    assert "path" not in found


def test_an_escaping_link_and_span_and_a_missing_path_name_their_lines(tmp_path, capsys):
    """#942: the other body checks locate the written link or path too."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "n.md").write_text("n\n", encoding="utf-8")
    _skill(tmp_path, "a", body="x\n[n](../shared/n.md)\n`../shared/n.md`\nRun `scripts/build.py`.\n")
    assert _one(tmp_path, capsys, "link-escapes-skill")["line"] == 6
    assert _one(tmp_path, capsys, "path-escapes-skill")["line"] == 7
    assert _one(tmp_path, capsys, "path-missing")["line"] == 8


def test_frontmatter_codes_name_the_line_of_their_key(tmp_path, capsys):
    """#942: ``line`` of a frontmatter code is the line of its key."""
    _skill(tmp_path, "a", name="other-name", desc="Use when " + "word " * 220, extra="descripton: typo\n")
    assert _one(tmp_path, capsys, "name-mismatch")["line"] == 2
    assert _one(tmp_path, capsys, "description-too-long")["line"] == 3
    assert _one(tmp_path, capsys, "frontmatter-unknown-key")["line"] == 4


def test_a_name_over_the_limit_names_the_name_line(tmp_path, capsys):
    """#942: ``name-format`` points at the ``name:`` line."""
    _skill(tmp_path, "Bad_Name")
    assert _one(tmp_path, capsys, "name-format")["line"] == 2


def test_a_script_finding_names_its_path(tmp_path, capsys):
    """#942: a finding about a bundled script has ``path`` relative to the skill directory, with ``script``."""
    skill_dir = _skill(tmp_path, "a")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
    found = _one(tmp_path, capsys, "script-crlf")
    assert found["path"] == found["script"] == "scripts/run.sh"
    assert "line" not in found


def test_one_install_finding_has_no_line(tmp_path, capsys):
    """#942: ``install-unpinned`` stays one finding per skill, with no ``line``."""
    _skill(tmp_path, "a", body="Run:\n\n```sh\npip install requests\npip install numpy\n```\n")
    found = _one(tmp_path, capsys, "install-unpinned")
    assert "line" not in found and "path" not in found


def test_the_json_document_carries_line_and_path_in_cli_output(tmp_path, capsys):
    """#942: ``--json`` copies ``line`` and ``path`` into the subject's finding; ``file`` stays SKILL.md."""
    skill_dir = _skill(tmp_path, "a", body="See [notes](notes.md).\n")
    (skill_dir / "run.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", "--json"], capsys)
    [subject] = json.loads(out)["subjects"]
    by_code = {f["code"]: f for f in subject["findings"]}
    assert by_code["link-missing"]["line"] == 5 and "path" not in by_code["link-missing"]
    assert by_code["script-crlf"]["path"] == "run.sh"
    assert by_code["script-crlf"]["file"].endswith("a/SKILL.md")


def test_a_reference_is_located_in_the_body_not_in_the_frontmatter(tmp_path, capsys):
    """#942: the same text in the description does not move the line of a missing link into the frontmatter."""
    _skill(tmp_path, "a", desc="Use when discussing (notes.md).", body="See [notes](notes.md).\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 5


def test_a_finding_the_check_cannot_place_has_no_line_key_in_json(tmp_path, capsys):
    """#942: ``--json`` leaves ``line`` out of a finding with no position, as ``install-unpinned`` has none."""
    _skill(tmp_path, "a", body="```sh\npip install requests\npip install numpy\n```\n")
    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", "--json"], capsys)
    [subject] = json.loads(out)["subjects"]
    [found] = [f for f in subject["findings"] if f["code"] == "install-unpinned"]
    assert "line" not in found


def test_a_quoted_frontmatter_key_is_located(tmp_path, capsys):
    """#942: ``"name": x`` is the ``name`` key, so ``name-mismatch`` names its line."""
    skill_dir = tmp_path / "a"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f'---\n"name": other-name\ndescription: {OK}\n---\nBody.\n', encoding="utf-8")
    assert _one(tmp_path, capsys, "name-mismatch")["line"] == 2


def test_a_path_with_an_apostrophe_is_located(tmp_path, capsys):
    """#942: the path of a ``path-missing`` finding is read from its repr, which uses double quotes here."""
    _skill(tmp_path, "a", body='Run `python "scripts/owner\'s.py"`.\n')
    assert _one(tmp_path, capsys, "path-missing")["line"] == 5


def test_a_link_is_not_located_at_an_earlier_link_that_it_prefixes(tmp_path, capsys):
    """#942: ``missing.md`` is not found at the line of the existing ``missing.md.bak``."""
    skill_dir = _skill(tmp_path, "a", body="[existing](missing.md.bak)\n[missing](missing.md)\n")
    (skill_dir / "missing.md.bak").write_text("x\n", encoding="utf-8")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 6


def test_a_reference_is_not_located_at_an_earlier_definition_that_it_prefixes(tmp_path, capsys):
    """#942: ``]: missing.md`` is not found at ``]: missing.md.bak``, above or before the inline link."""
    skill_dir = _skill(tmp_path, "a", body="[other]: missing.md.bak\n\n[bad](missing.md)\n")
    (skill_dir / "missing.md.bak").write_text("x\n", encoding="utf-8")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 7


def test_the_second_of_two_reference_definitions_is_located(tmp_path, capsys):
    """#942: ``[missing]: missing.md`` on the line after ``[existing]: missing.md.bak`` is that line."""
    skill_dir = _skill(tmp_path, "a", body="[existing]: missing.md.bak\n[missing]: missing.md\n")
    (skill_dir / "missing.md.bak").write_text("x\n", encoding="utf-8")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 6


def test_a_padded_first_link_is_the_first_line(tmp_path, capsys):
    """#942: ``[first]( missing.md )`` above ``[later](missing.md)`` is the first occurrence."""
    _skill(tmp_path, "a", body="[first]( missing.md )\n[later](missing.md)\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 5


def test_a_link_is_not_located_at_an_angle_destination_with_a_space_that_it_prefixes(tmp_path, capsys):
    """#942: ``(<missing.md backup>)`` is a different destination from ``missing.md``."""
    skill_dir = _skill(tmp_path, "a", body="[existing](<missing.md backup>)\n[bad](missing.md)\n")
    (skill_dir / "missing.md backup").write_text("x\n", encoding="utf-8")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 6


def test_prose_in_parentheses_is_not_located_as_a_link(tmp_path, capsys):
    """#942: ``( missing.md )`` in a sentence is not the link ``[bad](missing.md)`` on the next line."""
    _skill(tmp_path, "a", body="Optional filename ( missing.md ).\n[bad](missing.md)\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 6


def test_a_shell_escaped_path_is_located_as_written(tmp_path, capsys):
    """#942: ``my\\ tool.py`` in a code span names its line though the finding names ``my tool.py``."""
    _skill(tmp_path, "a", body="Run `python scripts/my\\ tool.py`.\n")
    assert _one(tmp_path, capsys, "path-missing")["line"] == 5


def test_a_bom_does_not_hide_the_frontmatter(tmp_path, capsys):
    """#942: the loader accepts a UTF-8 BOM, so the frontmatter lines are found with one."""
    skill_dir = tmp_path / "a"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"\ufeff---\nname: other-name\ndescription: {OK}\n---\nBody.\n", encoding="utf-8")
    assert _one(tmp_path, capsys, "name-mismatch")["line"] == 2


def test_an_empty_name_key_has_a_line_and_an_absent_one_has_none(tmp_path, capsys):
    """#942: ``name-missing`` names the line of an empty ``name:`` key; with no key there is no line."""
    skill_dir = tmp_path / "a"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f"---\nname: ''\ndescription: {OK}\n---\nBody.\n", encoding="utf-8")
    assert _one(tmp_path, capsys, "name-missing")["line"] == 2
    other = tmp_path / "other"
    _skill(other, "b", name="")
    assert "line" not in _one(other, capsys, "name-missing")


def test_key_line_edges():
    """#942: no frontmatter, a key after the closing fence, and a key that is a prefix are not found."""
    assert key_line("", "name") is None
    assert key_line("# Body\nname: x\n", "name") is None
    assert key_line("---\ndescription: d\n---\nname: x\n", "name") is None
    assert key_line("---\nname-x: 1\nname: x\n---\n", "name") == 3
    assert key_line("---\n'name': x\n---\n", "name") == 2


def test_text_line_and_written_path_edges():
    """#942: a needle that is absent has no line; a message with no quoted word is returned whole."""
    assert text_line("a\nb `x`\n", "`x`") == 2
    assert text_line("a\n", "zz") is None
    assert text_line("a zz\nb `x`\n", "`x`", "zz") == 2  # the first needle that is found, not the first line
    assert written_path("'scripts/a.sh' (in 'x') names no file") == "scripts/a.sh"
    assert written_path("no quotes") == "no quotes"
    assert path_needles("'a b.py' (in 'python a\\\\ b.py') names no file")[:2] == ("`python a\\ b.py`", "python a\\ b.py")
    assert path_needles("no quotes")[0] == "`no quotes`"
    assert written_path("\"scripts/o'k.py\" (in 'x') names no file") == "scripts/o'k.py"


def test_the_fixture_catalog_raises_every_code_that_can_be_raised_from_disk(tmp_path, capsys):
    """#942: tests/fixtures/finding_codes holds one skill per code that files on disk can raise."""
    catalog = tmp_path / "catalog"
    shutil.copytree(FIXTURES, catalog)
    # Scripts are written here, not shipped: a broken .py would fail the project's own lint, a .sh needs LF-exact bytes.
    for name, files in {
        "script-crlf": {"tool.sh": b"#!/bin/sh\r\necho hello\r\n"},
        "script-lint": {"tool.py": b"assert True\n"},
        "script-syntax": {"tool.py": b"def broken(:\n"},
    }.items():
        directory = _skill(catalog, name)
        (directory / "scripts").mkdir()
        for file, data in files.items():
            (directory / "scripts" / file).write_bytes(data)
    # The invisible character is built here, so a reviewer of the fixtures sees no hidden text.
    _skill(catalog, "hidden-unicode", body="# Instructions\ns" + chr(0x200B) + "ort records.\n")
    out, _ = _run(["--root", str(catalog), "--kind", "skills", "--format", "json"], capsys)
    found = {f["code"] for row in json.loads(out)["skills"] for f in row["findings"]}
    assert set(NOT_ON_DISK) <= set(FINDING_CODES)
    assert found - NEEDS_TOOL == set(FINDING_CODES) - set(NOT_ON_DISK) - NEEDS_TOOL


def test_a_reference_definition_above_an_inline_link_is_the_first_line(tmp_path, capsys):
    """#942: the same missing target written as ``[n]: x`` then ``(x)`` is located at the earlier line."""
    _skill(tmp_path, "a", body="Intro.\n\n[n]: missing.md\n\nSee [later](missing.md).\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 7
    assert text_line("a\n[n]: x.md\n[l](x.md)\n", ("(x.md)", "]: x.md"), "x.md") == 2


def test_a_blockquoted_reference_definition_is_the_first_line(tmp_path, capsys):
    """#942: ``> [n]: missing.md`` above ``[later](missing.md)`` is located at the quoted definition."""
    _skill(tmp_path, "a", body="> [n]: missing.md\n\n[later](missing.md)\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 5


def test_a_multiline_inline_link_is_located_at_its_destination(tmp_path, capsys):
    """#942: ``[first](`` then ``missing.md`` on the next line, above ``[later](missing.md)``, is the earlier line."""
    _skill(tmp_path, "a", body="[first](\nmissing.md\n)\n[later](missing.md)\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 6


def test_an_unfinished_link_opener_does_not_reach_across_a_blank_line():
    """#942: ``[unfinished](`` then a blank line then ``missing.md`` is no link; the real one is located."""
    assert link_line("[unfinished](\n\nmissing.md\n\n[real](missing.md)\n", "missing.md") == 5


def test_an_opener_does_not_continue_into_a_blockquote():
    """#942: ``[unfinished](`` then ``> missing.md`` is two blocks; the real link on line 4 is located."""
    assert link_line("[unfinished](\n> missing.md\n\n[real](missing.md)\n", "missing.md") == 4
    assert link_line("> [q](\n> missing.md)\n\n[real](missing.md)\n", "missing.md") == 2


def test_an_unclosed_link_does_not_take_the_line_of_the_real_one(tmp_path, capsys):
    """#942: ``[q](`` with no ``)`` before the blank line is no link to the check, so ``[real]`` is located."""
    _skill(tmp_path, "a", body="> [q](\n> missing.md\n\n[real](missing.md)\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 8
    assert link_line("> [q](\n> missing.md\n\n[real](missing.md)\n", "missing.md") == 4
    assert link_line("See [q](missing.md\n\n) [real](missing.md)\n", "missing.md") == 3


def test_a_fenced_copy_does_not_take_the_line_of_the_real_one(tmp_path, capsys):
    """#942: the check skips fenced code, so ``line`` is the link or span it read, not an earlier copy in a fence."""
    _skill(tmp_path, "a", body="Intro.\n```md\n[g](missing.md)\n```\n\nSee [real](missing.md).\n")
    assert _one(tmp_path, capsys, "link-missing")["line"] == 10
    other = tmp_path / "other"
    _skill(other, "b", body="````\nRun `scripts/missing.py`.\n```\n````\nRun `scripts/missing.py`.\n")
    assert _one(other, capsys, "path-missing")["line"] == 9
    assert link_line("```markdown\n> [example]: missing.md\n```\n[real](missing.md)\n", "missing.md") == 4


def test_a_tab_after_a_quote_marker_is_dropped_as_the_check_drops_it():
    """#942: ``>`` then a tab before ``[n]: missing.md`` is the quoted definition the check reads, on line 1."""
    assert link_line(">\t[n]: missing.md\n\n[later](missing.md)\n", "missing.md") == 1


def test_key_line_allows_a_frontmatter_indented_as_a_whole():
    """#942: ``  name: x`` is top level when every key is indented alike; a nested key is not."""
    assert key_line("---\n  name: x\n  description: d\n---\n", "description") == 3
    assert key_line("---\nmetadata:\n  name: x\n---\n", "name") is None


def test_locate_keeps_falsy_values_and_drops_unset_ones():
    """#942: only an unset (None) ``line`` or ``path`` is left out of a row."""
    from dokimi.external.findings import Finding, locate

    assert locate(Finding("note", "c", "m", line=0, path="")) == {"line": 0, "path": ""}
    assert locate(Finding("note", "c", "m")) == {}


def test_several_unknown_keys_carry_no_line(tmp_path, capsys):
    """#942: one finding lists all the unknown keys, so it names no single line; one key has its line."""
    _skill(tmp_path, "a", extra="aaa: 1\nbbb: 2\n")
    assert "line" not in _one(tmp_path, capsys, "frontmatter-unknown-key")


_OFFERED = re.compile(r"rename the directory to '([^']*)'|set name to '([^']*)'|add 'name: ([^']*)'")


@pytest.mark.parametrize("directory, name", [
    ("Bad_Name", ""), ("Bad_Name", "Bad_Name"), ("Bad_Name", "bad-name"), ("Bad_Name", "Other_Name"), ("a", "A_b"),
    ("good-name", "Bad_Name"), ("a" * 70, "a" * 64), ("a--b", "a-b"), ("my-skill", "my_skill"),
])
def test_name_advice_offers_only_valid_names_and_never_loops(tmp_path, capsys, directory, name):
    """#942: following any edit a ``name-*`` finding offers reaches a skill with no ``name-*`` finding, through
    names that pass ``name-format``, without coming back to a state (directory, name) it has been in."""
    def walk(state: tuple[str, str], seen: set[tuple[str, str]]) -> None:
        root = tmp_path / str(len(seen))
        shutil.rmtree(root, ignore_errors=True)
        _skill(root, state[0], name=state[1])
        out, _ = _run(["--root", str(root), "--kind", "skills", "--format", "json"], capsys)
        messages = [f["message"] for row in json.loads(out)["skills"] for f in row["findings"]
                    if f["code"].startswith("name-")]
        for message in messages:
            assert FIX.search(message), message
            for renamed, set_to, added in _OFFERED.findall(message):
                offered = renamed or set_to or added
                named = set_to or added
                assert NAME_PATTERN.fullmatch(offered) and len(offered) <= NAME_MAX, message
                after = (renamed, state[1]) if renamed else (state[0], named)
                assert after not in seen, (seen, message)
                walk(after, seen | {after})

    walk((directory, name), {(directory, name)})


@pytest.mark.parametrize("separator, in_description", [
    (0x2028, True), (0x2028, False), (0x2029, False), (0x85, False), (0x0B, False), (0x0C, False), (0x1C, False),
])
def test_only_cr_and_lf_end_a_line(tmp_path, capsys, separator, in_description):
    """#942: a separator ``str.splitlines`` knows but an editor does not show as a break moves no ``line``."""
    sep = chr(separator)  # built here, so a reviewer of this file sees no hidden text
    desc = f'"Formats tax{sep}tables. Use when asked."' if in_description else OK
    _skill(tmp_path, "a", desc=desc, extra="descripton: typo\n", body=f"Intro{sep}more.\n\nSee [n](missing.md).\n")
    assert _one(tmp_path, capsys, "frontmatter-unknown-key")["line"] == 4
    assert _one(tmp_path, capsys, "link-missing")["line"] == 8

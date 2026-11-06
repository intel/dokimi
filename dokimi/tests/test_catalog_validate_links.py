# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reference, link and description checks of ``catalog check``: links, mirror drift and near-duplicates (split from ``test_catalog_validate``)."""

from __future__ import annotations

import pytest

from dokimi.external import read_skill_manifest, review_skills
from tests.test_catalog_validate import _codes, _reviews_by_name, _root_skill, _skill


def test_a_link_target_the_os_refuses_is_a_missing_link_not_a_crash(tmp_path):
    # A name over 255 bytes in one path part: Path.exists() raises on Python 3.11 to 3.13.
    too_long = "a" * 300
    _skill(tmp_path / "skills", "healthy", name="healthy", description="Use for healthy.",
           body="Run [it](references/a.md).\n")
    _skill(tmp_path / "skills", "longname", name="longname", description="Use for longname.",
           body=f"See [g]({too_long}.md).\n")

    reviews = _reviews_by_name(tmp_path)
    assert reviews["healthy"].state == "warn"
    assert _codes(reviews["longname"]) == ["link-missing"]


@pytest.mark.parametrize(
    "destination",
    ["tel:+15551234567", "urn:isbn:0451450523", "MAILTO:someone@example.com", "data:text/plain,x"],
)
def test_uri_scheme_destinations_are_not_files(tmp_path, destination):
    _skill(tmp_path / "skills", "uris", name="uris", description="Use for uris.",
           body=f"Reach us at [here]({destination}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


def test_reference_definition_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "refs", name="refs", description="Use for refs.",
           body="See [the guide][g] for details.\n\n[g]: references/missing.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


def test_reference_definition_out_of_the_skill_dir_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Use for reach.",
           body="See [the notes][n].\n\n[n]: ../../shared/notes.md \"Notes\"\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_reference_definitions_that_name_no_file_are_not_checked(tmp_path):
    body = (
        "A footnote[^1] and [a site][web].\n\n"
        "[^1]: Footnote text, not a file.\n"
        "[web]: https://example.com/docs\n"
        "[anchor]: #usage\n"
        "```markdown\n"
        "[example]: references/missing.md\n"
        "```\n"
    )
    _skill(tmp_path / "skills", "clean", name="clean", description="Use for clean.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


def test_angle_bracket_destination_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "angle", name="angle", description="Use for angle.",
           body="See [the guide](<references/missing.md>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


def test_angle_bracket_destination_out_of_the_skill_dir_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Use for reach.",
           body="See [notes](<../../shared/notes.md>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_angle_bracket_destination_with_an_anchor_out_of_the_skill_dir_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Use for reach.",
           body="See [setup](<../../shared/notes.md#setup>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_reference_definition_with_spaces_in_angle_brackets_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "spaced", name="spaced", description="Use for spaced.",
           body="See [notes][n].\n\n[n]: <my notes.md>\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


@pytest.mark.parametrize(
    "line",
    ["[Note]: read this first", "[Step 1]: Run the tests", "[--verbose]: print more"],
)
def test_prose_line_that_starts_with_a_bracketed_label_is_not_a_definition(tmp_path, line):
    _skill(tmp_path / "skills", "prose", name="prose", description="Use for prose.",
           body=f"Start here.\n\n{line}\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


@pytest.mark.parametrize("destination", ["run.py:42", "run.py:10-20", "scripts/run.py:42:7"])
def test_a_file_line_link_to_a_missing_file_is_still_a_warning(tmp_path, destination):
    # "run.py:42" is read as a path, not a URI, so it is checked and the missing file is flagged.
    _skill(tmp_path / "skills", "fileline", name="fileline", description="Use for fileline.",
           body=f"See [the entry point]({destination}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


@pytest.mark.parametrize(
    "destination", ["z39.50r:example.org/database", "x.y+z:records/item"],
)
def test_dotted_uri_scheme_destinations_are_not_files(tmp_path, destination):
    _skill(tmp_path / "skills", "dotted", name="dotted", description="Use for dotted.",
           body=f"Look up [the record]({destination}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_skill_dir_variable_with_another_variable_is_not_read_as_a_file(tmp_path):
    _skill(tmp_path / "skills", "tpl", name="tpl", description="Use for tpl.",
           body="Run [it](${CLAUDE_SKILL_DIR}/scripts/${SCRIPT_NAME}.py).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_root_skill_and_a_nested_skill_named_like_the_checkout_are_not_mirror_drift(tmp_path):
    # The root SKILL.md is named by the checkout's directory, so it is no copy of skills/tools.
    root = tmp_path / "tools"
    _root_skill(root, "name: root-skill\ndescription: Use for root work.\n")
    _skill(root, "skills/tools", name="tools", description="Use for tool work.")

    reviews = _reviews_by_name(root)
    assert _codes(reviews["root-skill"]) == []
    assert _codes(reviews["tools"]) == []


def test_root_skills_of_two_checkouts_are_near_duplicates_not_mirror_drift(tmp_path):
    # Both root skills reached as "sub/.." have no directory name, so they never mirror each other.
    entries = []
    for checkout, ending in (("a/repo", "bugs"), ("b/repo", "typing")):
        root = tmp_path / checkout
        root.mkdir(parents=True)
        _root_skill(
            root, f"name: r{checkout[0]}\ndescription: Use when reviewing Python pull "
                  f"requests for style and {ending}.\n")
        (root / "sub").mkdir()
        (root / "skills.yaml").write_text(
            f"skills:\n  - name: r{checkout[0]}\n    catalog_path: sub/..\n", encoding="utf-8")
        entries.extend(read_skill_manifest(root))

    reviews = review_skills(entries)
    assert [_codes(r) for r in reviews] == [["description-near-duplicate"]] * 2
    for review, other in zip(reviews, ("b/repo", "a/repo"), strict=True):
        assert str((tmp_path / other).as_posix()) in review.findings[0].message


def test_mirror_drift_names_the_checkout_of_the_other_copy(tmp_path):
    entries = []
    for checkout, text in (("a", "one"), ("b", "two")):
        root = tmp_path / checkout
        _skill(root, "same", name="same", description="Use for same.", body=f"# {text}\n")
        entries.extend(read_skill_manifest(root))

    reviews = review_skills(entries)
    for review, other in zip(reviews, ("b", "a"), strict=True):
        assert (tmp_path / other).as_posix() in review.findings[0].message


def test_near_duplicate_check_reads_non_ascii_description_words(tmp_path):
    shared = "Use when проверка исходного кода необходима."
    _skill(tmp_path / "skills", "code-check", name="code-check", description=shared)
    _skill(tmp_path / "skills", "code-audit", name="code-audit", description=shared)

    reviews = _reviews_by_name(tmp_path)
    assert _codes(reviews["code-check"]) == ["description-near-duplicate"]
    assert _codes(reviews["code-audit"]) == ["description-near-duplicate"]


def test_a_file_symbol_link_to_a_missing_file_is_still_a_warning(tmp_path):
    _skill(tmp_path / "skills", "symbol", name="symbol", description="Use for symbol.",
           body="See [the entry point](run.py:main).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]


@pytest.mark.parametrize(
    "description",
    [
        "Run database migrations when deploying a new release.",
        "Diagnose failures when debugging production incidents.",
        "Format commit messages when the user asks for a commit.",
    ],
)
def test_a_general_when_clause_is_not_a_description_warning(tmp_path, description):
    _skill(tmp_path / "skills", "clause", name="clause", description=description)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == []


@pytest.mark.parametrize("body", [
    "See [the entry point](run.py:42).\n",
    "See [the entry point](run.py:main).\n",
    "See [the entry point](run.py:10-20).\n",
    "See [the entry point](scripts/run.py:42:7).\n",
])
def test_an_existing_file_with_a_line_or_symbol_suffix_is_not_a_missing_link(tmp_path, body):
    skill_dir = _skill(tmp_path / "skills", "bundled-run", name="bundled-run",
                       description="Use for bundled run.", body=body)
    (skill_dir / "run.py").write_text("print('x')\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('x')\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_an_escaping_link_with_a_line_suffix_is_named_as_escaping(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "run.py").write_text("print('x')\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach-run", name="reach-run", description="Use for reach run.",
           body="See [the entry point](../../shared/run.py:42).\n"
                "Also `../../shared/run.py:main`.\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert sorted(_codes(review)) == ["link-escapes-skill", "path-escapes-skill"]


@pytest.mark.parametrize(
    "description",
    [
        "Use formatting tools to normalize logs.",
        "Use iframe helpers to inspect embedded pages.",
        "Use ifs-free helpers to rewrite conditions.",
        "Use this skillset to lint sources.",
        "Triggers online for logs.",
        "Wheneverish tooling for logs.",
    ],
)
def test_a_use_phrase_that_only_starts_like_a_trigger_is_still_a_description_warning(tmp_path, description):
    _skill(tmp_path / "skills", "prefix-only", name="prefix-only", description=description)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["description-no-when"]


@pytest.mark.parametrize(
    ("link", "body", "codes"),
    [
        ("skills/probe/refs", "[a](refs/{})", ["link-escapes-skill"] * 2),
        ("references", "[a](references/{})", ["link-missing"] * 2),
        ("skills/shared", "`../shared/{}`", []),
    ],
)
def test_a_symlink_out_of_the_checkout_does_not_tell_which_host_files_exist(tmp_path, link, body, codes):
    host, root = tmp_path / "host", tmp_path / "catalog"
    host.mkdir()
    (host / "present.md").write_text("# Host\n", encoding="utf-8")
    _skill(root / "skills", "probe", name="probe", description="Use when probing.",
           body=f"{body.format('present.md')} {body.format('absent.md')}\n")
    (root / link).symlink_to(host, target_is_directory=True)

    [review] = review_skills(read_skill_manifest(root))
    assert _codes(review) == codes
    assert len({f.message.replace("present", "absent") for f in review.findings}) <= 1


def test_same_named_skills_with_other_descriptions_are_near_duplicates_not_drift(tmp_path):
    _skill(tmp_path / "plugins/a/skills", "review", name="review",
           description="Use when reviewing Python pull requests for style issues.")
    _skill(tmp_path / "plugins/b/skills", "review", name="review",
           description="Use when reviewing Python pull requests for security issues.")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert len(reviews) == 2
    assert all("description-near-duplicate" in _codes(r) for r in reviews)
    assert all("mirror-drift" not in _codes(r) for r in reviews)


def test_a_link_that_reaches_a_file_only_from_above_the_catalog_root_gets_no_root_note(tmp_path):
    (tmp_path / "skills" / "probe").mkdir(parents=True)
    (tmp_path / "skills" / "probe" / "x.md").write_text("# Above\n", encoding="utf-8")
    root = tmp_path / "a" / "catalog"
    _skill(root / "skills", "probe", name="probe", description="Use when probing.",
           body="See [a](../../skills/probe/x.md).\n")

    [review] = review_skills(read_skill_manifest(root))
    assert _codes(review) == ["link-missing"]
    assert "catalog root" not in review.findings[0].message

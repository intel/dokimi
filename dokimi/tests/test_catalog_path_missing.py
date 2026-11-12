# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``path-missing``: a code span naming a bundled file the skill does not have.

The cases come from a hand audit of GitSkills corpus hits (scratch x14 part A): a missing
``scripts/deploy.sh`` in a span, a ``python scripts/...`` command, ``references/finance.md`` next
to other references, and the forms that were noise (folders, placeholders, repo-root paths).
"""

from __future__ import annotations

import pytest

from dokimi.external import read_skill_manifest, review_skills
from dokimi.external.bundle_paths import _bundle_path, path_missing
from tests.test_catalog_validate import _codes, _skill


def _review(tmp_path, body, files=()):
    skill_dir = _skill(tmp_path / "skills", "deck", name="deck", description="Use when building decks.", body=body)
    for rel in files:
        target = tmp_path / "skills" / "deck" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    return review, skill_dir


@pytest.mark.parametrize(
    "body",
    [
        "Deploy with `scripts/deploy.sh`.\n",
        "Run `python scripts/workflows/lib/engine.py --check`.\n",
        "Read `references/finance.md#taxes` first.\n",
        "Use `${CLAUDE_SKILL_DIR}/scripts/deploy.sh`.\n",
        "Copy `./assets/style.css`.\n",
    ],
)
def test_a_span_naming_a_missing_bundled_file_is_a_warning(tmp_path, body):
    review, _ = _review(tmp_path, body, files=["references/metadata.md"])
    assert _codes(review) == ["path-missing"]
    assert review.state == "warn"


def test_a_span_naming_a_bundled_file_that_exists_is_fine(tmp_path):
    review, _ = _review(
        tmp_path,
        "Run `python scripts/init_skill.py` then read `references/metadata.md#keys`.\n",
        files=["scripts/init_skill.py", "references/metadata.md"],
    )
    assert review.findings == []


@pytest.mark.parametrize(
    "body",
    [
        "Put images in `assets/`.\n",  # a folder
        "Put images in `assets/icons`.\n",  # no extension: a folder
        "Run `run.py`.\n",  # flat name
        "Write `scripts/<name>.py`.\n",  # placeholder
        "Write `scripts/x.py` for each step.\n",  # placeholder part
        "Edit `src/app.py` in your project.\n",  # repo-root path, not a bundle folder
        "Use `scripts/*.sh`.\n",  # glob
        "See `../shared/scripts/a.py`.\n",  # escapes: another check
        "```\npython scripts/missing.py\n```\n",  # fence: example
    ],
)
def test_spans_that_name_no_bundled_file_are_not_checked(tmp_path, body):
    review, _ = _review(tmp_path, body)
    assert "path-missing" not in _codes(review)


def test_one_finding_per_path(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/a.sh`, then `bash scripts/a.sh --again`.\n")
    assert _codes(review) == ["path-missing"]


def test_a_symlink_out_of_the_skill_is_not_followed(tmp_path):
    outside = tmp_path / "outside.sh"
    outside.write_text("x\n", encoding="utf-8")
    review, skill_dir = _review(tmp_path, "Run `scripts/link.sh`.\n")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "link.sh").symlink_to(outside)
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "path-missing" not in _codes(review)


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("scripts/a.py", "scripts/a.py"),
        ("'scripts/a.py',", "scripts/a.py"),
        ("(scripts/a.py)", "scripts/a.py"),
        ("scripts/a.py:42", "scripts/a.py"),
        ("templates/report.md#top", "templates/report.md"),
        ("scripts", None),
        ("scripts/", None),
        ("scripts/build", None),
        ("scripts/foo.py", None),
        ("lib/a.py", None),
        ("https://x.org/scripts/a.py", None),
        ("scripts/../a.py", None),
        ("scripts/$X.sh", None),
        ("scripts/{a,b}.sh", None),
        ("scripts/a?.sh", None),
        ("scripts/[ab].sh", None),
        ("scripts/a|b.sh", None),
        ("scripts/a=b.sh", None),
        ("scripts//a.sh", None),
        ("scripts/./a.sh", None),
        ("scripts/xx.py", None),
        ("scripts/Foo.py", None),
        ("assets/Name/a.png", None),
        ("scripts/a.py:main", "scripts/a.py"),
        ("scripts/a.py:12-14", "scripts/a.py"),
    ],
)
def test_bundle_path_reading(word, expected):
    assert _bundle_path(word) == expected


def test_a_path_that_exists_from_the_catalog_root_says_so(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ecc.js").write_text("x\n", encoding="utf-8")
    review, _ = _review(tmp_path, "Run `node scripts/ecc.js memory`.\n")
    [finding] = review.findings
    assert finding.code == "path-missing"
    assert "exists from the catalog root" in finding.message


def test_a_quoted_argument_with_a_space_is_one_path(tmp_path):
    review, _ = _review(
        tmp_path,
        "Copy it with `cp 'assets/logo.dark mode.svg' /tmp/logo.svg`.\n",
        files=["assets/logo.dark mode.svg"],
    )
    assert review.findings == []


def test_a_missing_quoted_argument_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Copy it with `cp 'assets/logo.dark mode.svg' /tmp/logo.svg`.\n")
    [finding] = review.findings
    assert finding.code == "path-missing"
    assert finding.message.startswith("'assets/logo.dark mode.svg' (in ")


def test_a_standalone_path_with_a_space_that_exists_is_fine(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/logo.dark mode.svg`.\n", files=["assets/logo.dark mode.svg"])
    assert review.findings == []


def test_existing_paths_joined_by_a_separator_are_fine(tmp_path):
    review, _ = _review(
        tmp_path,
        "Run `python scripts/a.py,scripts/b.py` and `scripts/a.py;scripts/b.py`.\n",
        files=["scripts/a.py", "scripts/b.py"],
    )
    assert review.findings == []


def test_a_missing_path_in_a_separator_joined_list_is_named_alone(tmp_path):
    review, _ = _review(tmp_path, "Run `python scripts/a.py;scripts/zz.py`.\n", files=["scripts/a.py"])
    [finding] = review.findings
    assert finding.message.startswith("'scripts/zz.py' (in ")


def test_a_missing_quoted_argument_with_a_comma_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Copy it with `cp 'assets/a,b.svg' /tmp/x`.\n")
    [finding] = review.findings
    assert finding.message.startswith("'assets/a,b.svg' (in ")


def test_a_malformed_quote_falls_back_to_whitespace_words(tmp_path):
    review, _ = _review(tmp_path, "Run `cp 'scripts/a.sh /tmp/x`.\n")
    [finding] = review.findings
    assert finding.message.startswith("'scripts/a.sh' (in ")


def test_a_span_that_is_also_a_link_target_is_reported_once(tmp_path):
    review, _ = _review(tmp_path, "See [`scripts/run.py`](scripts/run.py).\n")
    assert _codes(review) == ["link-missing"]


def test_a_link_to_a_dot_slash_path_also_names_the_span_once(tmp_path):
    review, _ = _review(tmp_path, "See [run](./scripts/run.py) and `./scripts/run.py`.\n")
    assert _codes(review) == ["link-missing"]


def test_the_message_says_the_skill_directory_not_the_install(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/deploy.sh`.\n")
    [finding] = review.findings
    assert finding.message == "'scripts/deploy.sh' (in 'scripts/deploy.sh') names no file in the skill directory"


def test_a_span_with_a_backtick_is_quoted_in_the_message(tmp_path):
    [message] = path_missing(["echo `x` scripts/deploy.sh"], tmp_path, tmp_path, tmp_path.resolve())
    assert message == "'scripts/deploy.sh' (in 'echo `x` scripts/deploy.sh') names no file in the skill directory"


def test_a_path_in_parentheses_is_checked(tmp_path):
    review, _ = _review(tmp_path, "Run it (`(scripts/run.py)`).\n")
    assert _codes(review) == ["path-missing"]


def test_a_missing_escaped_comma_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, r"Copy it with `cp assets/a\,b.svg /tmp/out`." + "\n")
    [finding] = review.findings
    assert finding.message.startswith("'assets/a,b.svg' (in ")


def test_an_escaped_quote_does_not_end_a_quoted_name(tmp_path):
    review, _ = _review(
        tmp_path,
        r'Copy it with `cp "assets/a\"b,c.svg" /tmp/out`.' + "\n",
        files=['assets/a"b,c.svg'],
    )
    assert review.findings == []


def test_a_missing_name_with_a_comma_and_an_extension_like_tail_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/logo.v1,2.png`.\n")
    [finding] = review.findings
    assert finding.message.startswith("'assets/logo.v1,2.png' (in ")


def test_an_existing_name_with_a_comma_and_an_extension_like_tail_is_fine(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/logo.v1,2.png`.\n", files=["assets/logo.v1,2.png"])
    assert review.findings == []


def test_a_list_of_missing_paths_with_no_space_is_read_one_by_one(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/a.py,scripts/b.py`.\n", files=["scripts/a.py"])
    [finding] = review.findings
    assert finding.message.startswith("'scripts/b.py' (in ")

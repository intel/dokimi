# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``path-missing``: a code span naming a bundled file the skill does not have.

The cases come from a hand audit of GitSkills corpus hits: a missing
``scripts/deploy.sh`` in a span, a ``python scripts/...`` command, ``references/finance.md`` next
to other references, and the forms that were noise (folders, placeholders, repo-root paths).
"""

from __future__ import annotations

import pytest

from dokimi.external import read_skill_manifest, review_skills
from dokimi.external.bundle_paths import _bundle_path, _words, path_missing
from tests.test_catalog_validate import _codes, _skill


def _path_findings(review):
    """The path-missing findings only: the skill may have other findings (script-lint, description-no-when)."""
    return [f for f in review.findings if f.code == "path-missing"]


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
    # metadata.md is bundled and never named, so bundle-file-unreferenced warns about it too
    assert sorted(_codes(review)) == ["bundle-file-unreferenced", "path-missing"]
    assert review.state == "warn"


def test_a_span_naming_a_bundled_file_that_exists_is_fine(tmp_path):
    review, _ = _review(
        tmp_path,
        "Run `python scripts/init_skill.py` then read `references/metadata.md#keys`.\n",
        files=["scripts/init_skill.py", "references/metadata.md"],
    )
    assert _path_findings(review) == []


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


def test_a_dangling_symlink_out_of_the_skill_is_not_reported(tmp_path):
    # The target does not exist, so only the containment check keeps this quiet.
    review, skill_dir = _review(tmp_path, "Run `scripts/gone.sh`.\n")
    assert len(_path_findings(review)) == 1  # positive control: missing before the link exists
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "gone.sh").symlink_to(tmp_path / "nowhere" / "gone.sh")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _path_findings(review) == []


def test_a_symlink_to_an_existing_file_out_of_the_skill_is_not_followed(tmp_path):
    outside = tmp_path / "outside.sh"
    outside.write_text("x\n", encoding="utf-8")
    review, skill_dir = _review(tmp_path, "Run `scripts/link.sh`.\n")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "link.sh").symlink_to(outside)
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _path_findings(review) == []


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
    [finding] = _path_findings(review)
    assert finding.code == "path-missing"
    assert "exists from the catalog root" in finding.message


def test_a_quoted_argument_with_a_space_is_one_path(tmp_path):
    review, _ = _review(
        tmp_path,
        "Copy it with `cp 'assets/logo.dark mode.svg' /tmp/logo.svg`.\n",
        files=["assets/logo.dark mode.svg"],
    )
    assert _path_findings(review) == []


def test_a_missing_quoted_argument_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Copy it with `cp 'assets/logo.dark mode.svg' /tmp/logo.svg`.\n")
    [finding] = _path_findings(review)
    assert finding.code == "path-missing"
    assert finding.message.startswith("'assets/logo.dark mode.svg' (in ")


def test_an_escaped_space_is_part_of_the_path(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/my\\ run.py`.\n", files=["scripts/my run.py"])
    assert _path_findings(review) == []


def test_a_missing_path_with_an_escaped_space_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/my\\ run.py`.\n")
    [finding] = _path_findings(review)
    assert finding.code == "path-missing"
    assert finding.message.startswith("'scripts/my run.py' (in ")


def test_a_standalone_path_with_a_space_that_exists_is_fine(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/logo.dark mode.svg`.\n", files=["assets/logo.dark mode.svg"])
    assert _path_findings(review) == []


def test_existing_paths_joined_by_a_separator_are_fine(tmp_path):
    review, _ = _review(
        tmp_path,
        "Run `python scripts/a.py,scripts/b.py` and `scripts/a.py;scripts/b.py`.\n",
        files=["scripts/a.py", "scripts/b.py"],
    )
    assert _path_findings(review) == []


def test_a_missing_path_in_a_separator_joined_list_is_named_alone(tmp_path):
    review, _ = _review(tmp_path, "Run `python scripts/a.py;scripts/zz.py`.\n", files=["scripts/a.py"])
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/zz.py' (in ")


def test_a_missing_quoted_argument_with_a_comma_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Copy it with `cp 'assets/a,b.svg' /tmp/x`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/a,b.svg' (in ")


def test_a_malformed_quote_falls_back_to_whitespace_words(tmp_path):
    review, _ = _review(tmp_path, "Run `cp 'scripts/a.sh /tmp/x`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/a.sh' (in ")


def test_a_span_that_is_also_a_link_target_is_reported_once(tmp_path):
    review, _ = _review(tmp_path, "See [`scripts/run.py`](scripts/run.py).\n")
    assert _codes(review) == ["link-missing"]


def test_a_link_to_a_dot_slash_path_also_names_the_span_once(tmp_path):
    review, _ = _review(tmp_path, "See [run](./scripts/run.py) and `./scripts/run.py`.\n")
    assert _codes(review) == ["link-missing"]


def test_the_message_says_the_skill_directory_not_the_install(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/deploy.sh`.\n")
    [finding] = _path_findings(review)
    assert finding.message == "'scripts/deploy.sh' (in 'scripts/deploy.sh') names no file in the skill directory; add the file or fix the path"


def test_a_span_with_a_backtick_is_quoted_in_the_message(tmp_path):
    [message] = path_missing(["echo `x` scripts/deploy.sh"], tmp_path, tmp_path, tmp_path.resolve())
    assert message == "'scripts/deploy.sh' (in 'echo `x` scripts/deploy.sh') names no file in the skill directory; add the file or fix the path"


def test_a_path_in_parentheses_is_checked(tmp_path):
    review, _ = _review(tmp_path, "Run it (`(scripts/run.py)`).\n")
    assert _codes(review) == ["path-missing"]


def test_a_missing_escaped_comma_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, r"Copy it with `cp assets/a\,b.svg /tmp/out`." + "\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/a,b.svg' (in ")


def test_a_missing_escaped_semicolon_is_named_whole(tmp_path):
    # outside quotes, an escaped ``;`` is no shell operator: the name is not cut at it
    review, _ = _review(tmp_path, r"Copy it with `cp assets/a\;b.svg /tmp/out`." + "\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/a;b.svg' (in ")


def test_an_escaped_quote_does_not_end_a_quoted_name(tmp_path):
    review, _ = _review(
        tmp_path,
        r'Copy it with `cp "assets/a\"b,c.svg" /tmp/out`.' + "\n",
        files=['assets/a"b,c.svg'],
    )
    assert _path_findings(review) == []


@pytest.mark.parametrize("char", [";", ",", "'", " "])
def test_a_backslash_before_other_characters_inside_double_quotes_is_kept(char):
    # a shell escapes only ``"`` and ``\`` of these inside double quotes: ``"a\;b"`` is the name ``a\;b``
    assert _words('cp "assets/a\\' + char + 'b.svg" dest') == ["cp", "assets/a\\" + char + "b.svg", "dest"]


@pytest.mark.parametrize("char", [";", ","])
def test_a_missing_name_with_a_kept_backslash_is_named_with_it(tmp_path, char):
    review, _ = _review(tmp_path, "Copy it with `cp \"assets/a\\" + char + "b.svg\" /tmp/out`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith(repr("assets/a\\" + char + "b.svg") + " (in ")  # the message quotes the name with repr


def test_a_double_backslash_inside_double_quotes_is_one_backslash():
    assert _words('cp "assets/a\\\\b.svg" dest') == ["cp", "assets/a\\b.svg", "dest"]


def test_a_double_backslash_before_the_closing_quote_closes_the_string():
    assert _words('cp "assets/a\\\\" dest') == ["cp", "assets/a\\", "dest"]


def test_an_escaped_quote_inside_double_quotes_is_a_quote():
    assert _words('cp "assets/a\\"b.svg" dest') == ["cp", 'assets/a"b.svg', "dest"]


def test_a_missing_name_with_a_comma_and_an_extension_like_tail_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/logo.v1,2.png`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/logo.v1,2.png' (in ")


def test_an_existing_name_with_a_comma_and_an_extension_like_tail_is_fine(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/logo.v1,2.png`.\n", files=["assets/logo.v1,2.png"])
    assert _path_findings(review) == []


def test_a_list_of_missing_paths_with_no_space_is_read_one_by_one(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/a.py,scripts/b.py`.\n", files=["scripts/a.py"])
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/b.py' (in ")


def test_a_missing_path_before_a_semicolon_command_separator_is_named(tmp_path):
    review, _ = _review(tmp_path, "Run `python scripts/run.py;echo done`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/run.py' (in ")


def test_a_comma_not_followed_by_a_bundle_folder_stays_in_the_name(tmp_path):
    review, _ = _review(tmp_path, "Use `assets/a.svg,b.svg`.\n", files=["assets/a.svg"])
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/a.svg,b.svg' (in ")


def test_a_list_of_skill_dir_paths_with_no_space_is_read_one_by_one(tmp_path):
    review, _ = _review(
        tmp_path,
        "Run `${CLAUDE_SKILL_DIR}/scripts/a.py,${CLAUDE_SKILL_DIR}/scripts/b.py`.\n",
        files=["scripts/a.py"],
    )
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/b.py' (in ")


def test_a_missing_path_before_a_semicolon_is_named_when_quotes_do_not_balance(tmp_path):
    review, _ = _review(tmp_path, 'Run `python scripts/run.py;echo "done`.\n')
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/run.py' (in ")


def test_a_list_with_a_comma_and_a_space_is_read_one_by_one(tmp_path):
    review, _ = _review(
        tmp_path,
        "Run `python scripts/a.py, scripts/b.py`.\n",
        files=["scripts/a.py", "scripts/b.py"],
    )
    assert _path_findings(review) == []


def test_a_list_with_a_dot_slash_after_the_comma_is_read_one_by_one(tmp_path):
    review, _ = _review(tmp_path, "Run `scripts/a.py,./scripts/b.py`.\n", files=["scripts/a.py"])
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/b.py' (in ")


def test_a_quoted_list_of_bundled_paths_is_read_one_by_one(tmp_path):
    review, _ = _review(tmp_path, 'Run `cp "scripts/a.py;scripts/b.py" /tmp/x`.\n', files=["scripts/a.py"])
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/b.py' (in ")


@pytest.mark.parametrize(
    ("body", "names"),
    [
        ("Run `python scripts/run.py|tee log`.\n", ["scripts/run.py"]),
        ("Run `python scripts/run.py>out.log`.\n", ["scripts/run.py"]),
        ("Run `cat <scripts/in.txt`.\n", ["scripts/in.txt"]),
        ("Run `python scripts/run.py --config=scripts/settings.yaml`.\n", ["scripts/run.py", "scripts/settings.yaml"]),
        ("Run `python scripts/run.py --define=KEY=scripts/settings.yaml`.\n", ["scripts/run.py", "scripts/settings.yaml"]),
    ],
)
def test_a_missing_path_next_to_a_shell_operator_or_an_option_is_named(tmp_path, body, names):
    review, _ = _review(tmp_path, body)
    assert [finding.message.split("'")[1] for finding in _path_findings(review)] == names


def test_a_quoted_name_with_an_operator_character_is_one_name(tmp_path):
    review, _ = _review(
        tmp_path, "Run `cp 'assets/logo.dark&light.svg' /tmp/x`.\n", files=["assets/logo.dark&light.svg"]
    )
    assert _path_findings(review) == []


def test_a_missing_quoted_name_with_an_operator_character_is_named_whole(tmp_path):
    review, _ = _review(tmp_path, "Run `cp 'assets/logo.dark&light.svg' /tmp/x`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/logo.dark&light.svg' (in ")


def test_a_backslash_in_a_code_span_is_a_character_of_the_name(tmp_path):
    review, _ = _review(tmp_path, r"Run `scripts/a\b.py`." + "\n", files=[r"scripts/a\b.py"])
    assert _path_findings(review) == []


def test_a_missing_backslash_name_is_named_with_the_backslash(tmp_path):
    review, _ = _review(tmp_path, r"Run `scripts/a\b.py`." + "\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith(r"'scripts/a\\b.py' (in ")  # repr shows the backslash doubled


def test_an_escaped_comma_before_a_bundle_folder_stays_in_the_name(tmp_path):
    review, _ = _review(tmp_path, r"Copy it with `cp scripts/a\,scripts/b.py /tmp/x`." + "\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith("'scripts/a,scripts/b.py' (in ")


@pytest.mark.parametrize("name", ["example", "file", "filename", "name"])
def test_a_missing_file_with_a_real_name_such_as_example_is_named(tmp_path, name):
    review, _ = _review(tmp_path, f"Run `python scripts/{name}.py`.\n")
    [finding] = _path_findings(review)
    assert finding.message.startswith(f"'scripts/{name}.py' (in ")


def test_a_quoted_name_with_a_hash_is_one_name(tmp_path):
    review, _ = _review(
        tmp_path, "Run `cp 'assets/logo.v2#dark.svg' /tmp/x`.\n", files=["assets/logo.v2#dark.svg"]
    )
    assert _path_findings(review) == []


def test_a_hash_name_that_is_absent_falls_back_to_the_part_before_the_anchor(tmp_path):
    review, _ = _review(tmp_path, "Run `cp 'assets/logo.v2#dark.svg' /tmp/x`.\n", files=["assets/logo.v2"])
    assert _path_findings(review) == []  # the part before ``#`` is a file the skill has


def test_a_backslash_inside_single_quotes_is_part_of_the_name(tmp_path):
    review, _ = _review(tmp_path, r"Run `cp 'assets/a\,b.svg' out/`." + "\n", files=["assets/a,b.svg"])
    [finding] = _path_findings(review)
    assert finding.message.startswith(r"'assets/a\\,b.svg' (in ")  # repr shows the backslash doubled


def test_an_existing_backslash_name_inside_single_quotes_is_not_reported(tmp_path):
    review, _ = _review(tmp_path, r"Run `cp 'assets/a\,b.svg' out/`." + "\n", files=[r"assets/a\,b.svg"])
    assert _path_findings(review) == []


def test_an_apostrophe_inside_double_quotes_does_not_open_a_single_quote(tmp_path):
    body = "Run `cp \"assets/a'\\\"b'c.svg\" out/`.\n"
    review, _ = _review(tmp_path, body, files=["assets/a'\"b'c.svg"])
    assert _path_findings(review) == []
    review, _ = _review(tmp_path / "absent", body)
    [finding] = _path_findings(review)
    assert finding.message.startswith("'assets/a\\'\"b\\'c.svg' (in ")


URL = "https://github.com/NVIDIA/compileiq/blob/main/examples/compilers/triton_example/mixed_triton.py"


@pytest.mark.parametrize(
    "body",
    [
        f"See [`examples/triton_example/mixed_triton.py`]({URL}) upstream.\n",
        f"See [the `examples/mixed.py` file]({URL}) upstream.\n",
        f"See [`examples/mixed.py`](<{URL}>) upstream.\n",
        "Mail [`examples/mixed.py`](mailto:a@b.example).\n",
        f"See [![`examples/mixed.py`](badge.svg) `examples/other.py`]({URL}).\n",
    ],
)
def test_1089_a_code_span_in_the_label_of_a_url_link_is_not_path_missing(tmp_path, body):
    """#1089: the label of an upstream example link names no bundled file."""
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


@pytest.mark.parametrize(
    "body",
    [
        f"See `examples/mixed.py`, then [docs]({URL}).\n",
        f"See [docs]({URL}) and [`examples/mixed.py`](notes.md).\n",
        "See [`examples/mixed.py`] and (https://example.com/x).\n",
    ],
)
def test_1089_a_code_span_outside_a_url_link_label_is_still_checked(tmp_path, body):
    """#1089: only the label of a URL link is skipped."""
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "examples/mixed.py" in " ".join(f.message for f in _path_findings(review))


def test_1089_a_missing_local_link_target_is_reported_once(tmp_path):
    """#1089: the span of a local link is not dropped; the missing target is one `link-missing`, not a `path-missing` too."""
    review, _ = _review(tmp_path, "See [`examples/mixed.py`](examples/mixed.py).\n")
    assert _codes(review).count("link-missing") == 1
    assert _path_findings(review) == []


@pytest.mark.parametrize(
    "body",
    [
        "Run [`scripts/deploy.sh` and [guide](notes.md)](https://example.com).\n",
        "Run [`scripts/deploy.sh` and [guide][g]](https://example.com).\n\n[g]: notes.md\n",
    ],
)
def test_1089_a_label_that_holds_a_link_is_not_a_link_label(tmp_path, body):
    """#1089: CommonMark does not nest links, so the outer brackets are text and the span is checked."""
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert [f.message.split(" ")[0] for f in _path_findings(review)] == ["'scripts/deploy.sh'"]


@pytest.mark.parametrize(
    "body",
    [
        "See [`examples/mixed.py`][upstream].\n\n[upstream]: https://example.com/mixed.py\n",
        "See [`examples/mixed.py`][Up  Stream].\n\n[up stream]: <https://example.com/mixed.py> \"t\"\n",
        "See [`examples/mixed.py`][upstream].\n\n[upstream]:\n  https://example.com/mixed.py\n",
    ],
)
def test_1089_a_code_span_in_the_label_of_a_reference_url_link_is_not_path_missing(tmp_path, body):
    """#1089: a reference link whose definition is a URL is judged by its destination, like an inline one."""
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


@pytest.mark.parametrize(
    "body",
    [
        "See [`examples/mixed.py`][local].\n\n[local]: notes.md\n",
        "See [`examples/mixed.py`][absent].\n\n[upstream]: https://example.com/mixed.py\n",
    ],
)
def test_1089_a_code_span_in_the_label_of_a_reference_local_link_is_still_checked(tmp_path, body):
    """#1089: only a reference whose definition is a URL is skipped."""
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "examples/mixed.py" in " ".join(f.message for f in _path_findings(review))


def test_1089_the_first_definition_of_a_reference_decides(tmp_path):
    """#1089: CommonMark uses the first definition of a label, so a later URL one does not make a local link external."""
    body = "Run [`scripts/deploy.sh`][guide].\n\n[guide]: notes.md\n[guide]: https://example.com\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "scripts/deploy.sh" in " ".join(f.message for f in _path_findings(review))


def test_1089_a_first_url_definition_wins_over_a_later_local_one(tmp_path):
    """#1089: the first definition decides in this direction too."""
    body = "Run [`scripts/deploy.sh`][guide].\n\n[guide]: https://example.com\n[guide]: notes.md\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert _path_findings(review) == []


def test_1089_an_undefined_reference_inside_a_url_label_is_text(tmp_path):
    """#1089: `[guide][undefined]` with no definition is no link, so it does not stop the outer URL link."""
    body = "See [`examples/mixed.py` and [guide][undefined]](https://example.com).\n"
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def test_1089_a_local_link_with_a_line_suffix_is_not_a_url_link(tmp_path):
    """#1089: `notes.md:42` is a file and a line, not a scheme, so the label's missing file is reported."""
    review, _ = _review(tmp_path, "Run [`scripts/deploy.sh`](notes.md:42).\n", files=("notes.md",))
    assert "scripts/deploy.sh" in " ".join(f.message for f in _path_findings(review))


@pytest.mark.parametrize("ref", ["[guide]", "[guide][]"])
def test_1089_a_shortcut_or_collapsed_link_in_a_label_means_the_label_is_no_link(tmp_path, ref):
    """#1089: links do not nest, so a defined `[guide]` or `[guide][]` inside the label makes the outer one text."""
    body = f"Run [`scripts/deploy.sh` and {ref}](https://example.com).\n\n[guide]: notes.md\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "scripts/deploy.sh" in " ".join(f.message for f in _path_findings(review))


def test_1089_a_definition_with_trailing_text_is_no_definition(tmp_path):
    """#1089: `[guide]: https://example.com unexpected trailing text` is a paragraph, so `[guide]` is not a URL link."""
    body = "Run [`scripts/deploy.sh`][guide].\n\n[guide]: https://example.com unexpected trailing text\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "scripts/deploy.sh" in " ".join(f.message for f in _path_findings(review))


def test_1089_a_tel_link_with_digits_is_a_url_link(tmp_path):
    """#1089: `tel:1234567890` is a URI scheme, so the label's span is the link's text."""
    review, _ = _review(tmp_path, "Run [`scripts/deploy.sh`](tel:1234567890).\n")
    assert _path_findings(review) == []


def test_1089_a_definition_in_angle_brackets_with_a_space_is_a_url_definition(tmp_path):
    """#1089: `<https://example.com/my guide>` is one destination, so `[guide]` is a URL link."""
    body = "Run [`scripts/deploy.sh`][guide]\n\n[guide]: <https://example.com/my guide>\n"
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def test_1089_a_definition_whose_title_continues_on_the_next_line_is_a_url_definition(tmp_path):
    """#1089: a title may span lines, so `[guide]` is still a URL link."""
    body = 'See [`scripts/deploy.sh`][guide]\n\n[guide]: https://example.com "first line\nsecond line"\n'
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def _missing(review):
    return [f.message.split(" ")[0] for f in _path_findings(review)]


@pytest.mark.parametrize("dest", ["notes.md:main", "notes.md:42", "notes.md:10-20", "<notes.md:main>"])
def test_1089_a_local_link_with_a_line_or_symbol_suffix_is_not_a_url_link(tmp_path, dest):
    """#1089: `_file_ref` reads `notes.md:main` as the file `notes.md`, so the label's missing file is reported."""
    review, _ = _review(tmp_path, f"Run [`scripts/deploy.sh`]({dest}).\n", files=("notes.md",))
    assert _missing(review) == ["'scripts/deploy.sh'"]


@pytest.mark.parametrize("dest", ["x:example", "tel:123", "urn:isbn:1", "a.b:c.d"])
def test_1089_a_destination_with_any_other_scheme_is_a_url_link(tmp_path, dest):
    """#1089: a one-letter or a dotted scheme with no file-like tail is a URI, as in `_file_ref`."""
    review, _ = _review(tmp_path, f"Run [`scripts/deploy.sh`]({dest}).\n\n[g]: {dest}\n")
    assert _path_findings(review) == []


@pytest.mark.parametrize("opener,closer", [('"', '"'), ("'", "'"), ("(", ")")])
def test_1089_a_definition_whose_title_never_closes_is_no_definition(tmp_path, opener, closer):
    """#1089: `[g]: https://example.com "unterminated` defines nothing, so `[g]` is text and the span is checked."""
    body = f"Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com {opener}unterminated\n"
    review, _ = _review(tmp_path, body)
    assert _missing(review) == ["'scripts/deploy.sh'"]
    body = f"Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com {opener}closed\nlater{closer}\n"
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def test_1089_a_title_on_the_next_line_that_never_closes_leaves_the_definition_without_a_title(tmp_path):
    """#1089: `[g]: https://example.com` is a definition; the next line, `"open`, is a paragraph."""
    body = 'Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com\n"open\n'
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def test_1089_a_definition_inside_a_paragraph_defines_nothing(tmp_path):
    """#1089: a definition cannot interrupt a paragraph, so `[guide]` after prose is text."""
    body = "Run [`scripts/deploy.sh`][guide].\n\nOrdinary paragraph.\n[guide]: https://example.com\n"
    review, _ = _review(tmp_path, body)
    assert _missing(review) == ["'scripts/deploy.sh'"]


def test_1089_consecutive_definitions_at_the_start_of_a_block_all_count(tmp_path):
    """#1089: the second definition of a block starts after the first, not after prose."""
    body = "Run [`scripts/deploy.sh`][b].\n\n[a]: notes.md\n[b]: https://example.com\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert _path_findings(review) == []


def test_1089_many_adjacent_url_links_are_read_in_linear_time():
    """#1089: each label is checked for a nested link, which must not scan every other link."""
    import time

    from dokimi.external.skill_markdown import link_pieces

    line = "[`a/b.py`](https://example.com) " * 40000
    started = time.monotonic()
    assert link_pieces([line]) == (["https://example.com"], [])
    assert time.monotonic() - started < 10


def test_1089_link_shaped_text_in_an_image_title_is_no_link(tmp_path):
    """#1089: `"[guide](notes.md)"` is the image's title, so the outer URL label holds no link."""
    body = 'See [`scripts/deploy.sh` ![badge](badge.svg "[guide](notes.md)")](https://example.com).\n'
    review, _ = _review(tmp_path, body, files=("badge.svg", "notes.md"))
    assert _path_findings(review) == []


def test_1089_a_defined_shortcut_after_an_undefined_full_reference_is_a_link(tmp_path):
    """#1089: `[guide][undefined]` falls back to the shortcut `[guide]`, which sits in the label."""
    body = "Run [`scripts/deploy.sh` and [guide][undefined]](https://example.com).\n\n[guide]: notes.md\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert _missing(review) == ["'scripts/deploy.sh'"]


def test_1089_an_escaped_bang_is_not_an_image_marker(tmp_path):
    """#1089: `\\![guide](notes.md)` is a literal `!` and a link, so the outer brackets are text."""
    body = "Run [`scripts/deploy.sh` and \\![guide](notes.md)](https://example.com).\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert _missing(review) == ["'scripts/deploy.sh'"]
    body = "Run [`scripts/deploy.sh` and ![guide](notes.md)](https://example.com).\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert _path_findings(review) == []


@pytest.mark.parametrize("rule", ["---", "***", "___", "- - -"])
def test_1089_a_definition_after_a_thematic_break_is_a_definition(tmp_path, rule):
    """#1089: a thematic break is a block of its own, so the definition after it starts a block."""
    body = f"See [`scripts/deploy.sh`][guide].\n\n{rule}\n[guide]: https://example.com\n"
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def test_1089_a_definition_after_prose_and_a_thematic_break_is_a_definition(tmp_path):
    """#1089: `***` ends the prose block, so `[guide]: ...` after it starts a block."""
    body = "See [`scripts/deploy.sh`][guide].\n\nIntroductory prose.\n***\n[guide]: https://example.com\n"
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


@pytest.mark.parametrize(
    "body",
    [
        "Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com/(\n",
        "Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com/)\n",
        "Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com/a(b\n",
    ],
)
def test_1089_a_definition_with_unbalanced_parentheses_is_no_definition(tmp_path, body):
    """#1089: a bare destination needs balanced parentheses, so `[g]` is text and the span is checked."""
    review, _ = _review(tmp_path, body)
    assert _missing(review) == ["'scripts/deploy.sh'"]


def test_1089_a_definition_with_balanced_parentheses_is_a_url_definition(tmp_path):
    body = "Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com/a(b)c\n"
    review, _ = _review(tmp_path, body)
    assert _path_findings(review) == []


def test_1089_a_defined_shortcut_after_an_invalid_inline_destination_is_a_link(tmp_path):
    """#1089: `[guide](bad destination)` is no inline link, so `[guide]` is the defined shortcut inside the label."""
    body = "Run [`scripts/deploy.sh` and [guide](bad destination)](https://example.com).\n\n[guide]: notes.md\n"
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert _missing(review) == ["'scripts/deploy.sh'"]


def test_1089_a_definition_label_with_an_escaped_bracket_does_not_define_the_plain_label(tmp_path):
    """#1089: `[g\\]]: ...` defines the label `g]`, not `g`."""
    body = "Run [`scripts/deploy.sh`][g].\n\n[g\\]]: https://example.com\n"
    review, _ = _review(tmp_path, body)
    assert _missing(review) == ["'scripts/deploy.sh'"]


def test_1089_a_reference_use_with_an_escaped_bracket_does_not_use_the_plain_label(tmp_path):
    """#1089: `[g\\]]` is the label `g]`, so the link below it does not point at `[g]`."""
    body = "Run [`scripts/deploy.sh`][g\\]].\n\n[g]: https://example.com\n"
    review, _ = _review(tmp_path, body)
    assert _missing(review) == ["'scripts/deploy.sh'"]


@pytest.mark.parametrize("empty", ["[notes]()", "[notes](<>)"])
def test_1089_a_link_with_an_empty_destination_in_a_label_means_the_label_is_no_link(tmp_path, empty):
    """#1089: `[notes]()` is a link, so links do not nest and the outer brackets are text."""
    review, _ = _review(tmp_path, f"Run [`scripts/deploy.sh` and {empty}](https://example.com).\n")
    assert _missing(review) == ["'scripts/deploy.sh'"]


def test_1089_a_reference_definition_with_escaped_brackets_names_its_file(tmp_path):
    """#1089: `[g]: references/a\\[b\\].md` is a link to the file `references/a[b].md`, which exists."""
    body = "Read [guide][g].\n\n[g]: references/a\\[b\\].md\n"
    review, _ = _review(tmp_path, body, files=("references/a[b].md",))
    assert "link-missing" not in _codes(review)


def test_1089_a_definition_with_an_escaped_bracket_in_its_label_names_its_file(tmp_path):
    """#1089: `[g\\]]: references/missing.md` is a definition of the label `g]`, and its file is missing."""
    body = "Read [guide][g\\]].\n\n[g\\]]: references/missing.md\n"
    review, _ = _review(tmp_path, body)
    assert "link-missing" in _codes(review)


def test_1092_unclosed_labels_after_thematic_breaks_are_read_in_linear_time():
    """#1092: an unclosed `[` after each `---` must not scan the rest of the paragraph for its `]`."""
    import time

    from dokimi.external.skill_markdown import link_pieces

    lines = ["---", "[x"] * 40000
    started = time.monotonic()
    assert link_pieces(lines) == ([], [])
    assert time.monotonic() - started < 10


def test_1092_labels_that_are_only_code_spans_are_different_keys(tmp_path):
    """#1092: `[`b`]` is its own definition (a file), not the first `[`a`]` (a URL), so its label is checked."""
    body = "[`a`]: https://example.com\n[`b`]: examples/b.py\n\n[`examples/c.py`][`b`]\n"
    review, _ = _review(tmp_path, body)
    assert _missing(review) == ["'examples/c.py'"]


@pytest.mark.parametrize("word", ["references/secrets.local.md", "scripts/run.local.md", "references/Keys.LOCAL.MD"])
def test_a_user_local_markdown_file_is_not_a_bundled_path(word):
    # the skill tells the user to create it: a user's own file, absent from the bundle by design
    assert _bundle_path(word) is None


def test_a_user_local_file_the_skill_names_is_not_reported_missing(tmp_path):
    review, _ = _review(tmp_path, "Put the keys in `references/secrets.local.md` (create it yourself).\n")
    assert _path_findings(review) == []


def test_a_plain_file_the_skill_names_is_still_reported_missing(tmp_path):
    review, _ = _review(tmp_path, "Put the keys in `references/secrets.md`.\n")
    assert len(_path_findings(review)) == 1


@pytest.mark.parametrize(
    "body",
    [
        "Run [`scripts/deploy.sh`][g[x].\n\n[g[x]: https://example.com\n",
        "Run [`scripts/deploy.sh`][ ].\n\n[ ]: https://example.com\n",
        "Run [`scripts/deploy.sh`\n***\n](https://example.com)\n",
        "Run [`scripts/deploy.sh`](notes.md:42).\n",
        "Run [`scripts/deploy.sh` and [guide]](https://example.com).\n\n[guide]: notes.md\n",
        "Run [`scripts/deploy.sh` and [guide][]](https://example.com).\n\n[guide]: notes.md\n",
        "Run [`scripts/deploy.sh`][guide].\n\n[guide]: https://example.com unexpected trailing text\n",
    ],
)
def test_1092_a_label_that_is_no_url_link_keeps_its_span_checked(tmp_path, body):
    """#1092: a malformed reference label, a block break, a file with a line suffix, a nested reference or a
    definition with trailing text is no URL link: the missing script is reported."""
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "scripts/deploy.sh" in " ".join(f.message for f in _path_findings(review))


@pytest.mark.parametrize(
    "title",
    ['"a \\"quoted\\" title"', "'it\\'s a title'", "(a \\(paren\\) title)"],
)
def test_1092_a_definition_title_may_hold_an_escaped_delimiter(tmp_path, title):
    """#1092: `[g]: https://example.com "a \\"quoted\\" title"` is a URL definition."""
    review, _ = _review(tmp_path, f"Run [`scripts/deploy.sh`][g].\n\n[g]: https://example.com {title}\n")
    assert _path_findings(review) == []


@pytest.mark.parametrize(
    "body",
    [
        "Run [`scripts/deploy.sh`](notes.md:42).\n",
        "Run [`scripts/deploy.sh` and [guide]](https://example.com).\n\n[guide]: notes.md\n",
        "Run [`scripts/deploy.sh`][guide].\n\n[guide]: https://example.com unexpected trailing text\n",
        "Run [`scripts/deploy.sh`][g].\n\n[ ]: https://example.com\n[g]: https://example.com\n",
    ],
    ids=["line-suffix", "shortcut-nested", "trailing-text", "blank-label-line"],
)
def test_missing_span_is_reported_when_the_destination_is_not_a_url(tmp_path, body):
    """#1089: a label is skipped only for a real URL link; these four forms are not one."""
    review, _ = _review(tmp_path, body, files=("notes.md",))
    assert "scripts/deploy.sh" in " ".join(f.message for f in _path_findings(review))

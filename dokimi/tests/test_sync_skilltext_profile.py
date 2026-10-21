# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The channel-control sync script: `scripts/sync_skilltext_profile.py`.

`--check` is what fails when a skill and the profile mirroring it drift apart, and it names
the plain invocation as the fix. These tests are what make that instruction true — a fix
command that does not actually re-sync is worse than no command, because the next person edits
the profile by hand and gets the frontmatter wrong.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_skilltext_profile.py"


def load_script():
    """Import the script by path — `scripts/` is not a package and must not become one."""
    spec = importlib.util.spec_from_file_location("sync_skilltext_profile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = load_script()


def write_skill(tmp_path, body, name="tmp-skill"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}", encoding="utf-8"
    )
    return d


FRONTMATTER = "---\nid: tmp-skilltext\nname: Text as Prompt\ndescription: Channel control.\n---\n"


# --- frontmatter handling -------------------------------------------------


def test_frontmatter_is_taken_verbatim_not_re_serialized():
    """Re-serializing YAML reorders keys and reflows the description, so every run
    would show a diff and real drift would be indistinguishable from formatting churn."""
    text = FRONTMATTER + "old body\n"
    assert sync.frontmatter_block(text) == FRONTMATTER


def test_no_frontmatter_reads_as_empty():
    assert sync.frontmatter_block("just a body\n") == ""


def test_unterminated_frontmatter_reads_as_empty():
    assert sync.frontmatter_block("---\nid: x\nno terminator\n") == ""


# The block this script copies and the block the loader parses have to be the same block:
# the frontmatter `id` is the arm name every artifact is keyed by, so a disagreement either
# splices body text into the header or drops an `id` the loader can read.


@pytest.mark.parametrize(
    "text",
    [
        FRONTMATTER + "body\n",
        "---\nid: x\n---\n",                       # no body
        "---\nid: x\n---",                         # no trailing newline
        "﻿" + FRONTMATTER + "body\n",         # BOM, which the loader tolerates
        "---\nid: x\n---\nbody\n---\nmore\n",      # a fence in the body
        "---\nid: x\n--- \n",                      # trailing space on the fence
        "---\nid: x\nno terminator\n",             # no fence at all
        "plain body\n",
        "",
        "---\nid: x\n----\nbody\n",                # four dashes: not a fence
    ],
)
def test_the_copied_block_and_the_parsed_block_agree(text):
    from dokimi.utils import parse_frontmatter

    header = sync.frontmatter_block(text)
    meta, body = parse_frontmatter(text)
    if not header:
        # No header to preserve means the loader must have found no frontmatter either.
        assert meta == {}
        return
    # The header is a literal prefix of the file (a newline may be added to the last fence),
    # it ends where the body begins, and re-parsing it recovers the same mapping.
    assert text.lstrip("﻿").startswith(header.rstrip("\n"))
    assert parse_frontmatter(header)[0] == meta
    assert len(header.rstrip("\n")) + len(body) <= len(text.lstrip("﻿"))


def test_a_body_line_starting_with_dashes_is_not_mistaken_for_the_fence():
    """A substring search for '\\n---' matched '----' and '--- ' too, and would have copied
    body text into the header."""
    text = "---\nid: x\n---\nbody\n----\ntrailer\n"
    assert sync.frontmatter_block(text) == "---\nid: x\n---\n"


def test_a_bom_does_not_hide_the_frontmatter():
    """The loader strips a BOM, so refusing the file here would report 'no YAML frontmatter'
    about a file whose id parses fine."""
    assert sync.frontmatter_block("﻿" + FRONTMATTER + "body\n") == FRONTMATTER


def test_a_fence_without_a_trailing_newline_still_ends_in_one(tmp_path):
    """Otherwise the body is spliced onto the fence line and the result has no frontmatter."""
    from dokimi.utils import parse_frontmatter

    skill = write_skill(tmp_path, "Body.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text("---\nid: tmp-skilltext\nname: N\ndescription: D.\n---", encoding="utf-8")
    rendered = sync.render(skill, profile)
    assert parse_frontmatter(rendered)[0]["id"] == "tmp-skilltext"


# --- render ---------------------------------------------------------------


def test_render_keeps_the_frontmatter_and_replaces_the_body(tmp_path):
    skill = write_skill(tmp_path, "New instructions.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale body\n", encoding="utf-8")

    rendered = sync.render(skill, profile)
    assert rendered.startswith(FRONTMATTER)
    assert "New instructions." in rendered
    assert "stale body" not in rendered


def test_rendered_body_is_the_skill_arm_text_byte_for_byte(tmp_path):
    """The point of the control is that both channels carry identical bytes."""
    from dokimi.agent_profiles import load_agent_profile
    from dokimi.skills import load_skill

    skill = write_skill(tmp_path, "Do the thing.\n\n- and the other thing")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale\n", encoding="utf-8")
    profile.write_text(sync.render(skill, profile), encoding="utf-8")

    injected = load_skill(skill).as_context(sync.MAX_CHARS).strip()
    assert load_agent_profile(profile).system_prompt == injected


def test_render_refuses_a_profile_that_does_not_exist(tmp_path):
    """Deriving frontmatter would name the arm after the filename stem behind your back."""
    skill = write_skill(tmp_path, "body")
    with pytest.raises(SystemExit, match="no such profile"):
        sync.render(skill, tmp_path / "absent.md")


def test_render_refuses_a_profile_without_frontmatter(tmp_path):
    """The arm name comes from the frontmatter id; losing it silently renames the arm."""
    skill = write_skill(tmp_path, "body")
    profile = tmp_path / "bare.md"
    profile.write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no YAML frontmatter"):
        sync.render(skill, profile)


# --- the CLI --------------------------------------------------------------


def run_main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["sync_skilltext_profile.py", *argv])
    return sync.main()


def test_check_fails_when_the_skill_was_edited_and_the_profile_was_not(tmp_path, monkeypatch):
    skill = write_skill(tmp_path, "Original.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale\n", encoding="utf-8")
    profile.write_text(sync.render(skill, profile), encoding="utf-8")
    assert run_main(monkeypatch, "--check", "--skill", str(skill), "--profile", str(profile)) == 0

    (skill / "SKILL.md").write_text(
        "---\nname: tmp-skill\ndescription: A skill.\n---\nEdited.", encoding="utf-8"
    )
    assert run_main(monkeypatch, "--check", "--skill", str(skill), "--profile", str(profile)) == 1


def test_check_does_not_write(tmp_path, monkeypatch):
    skill = write_skill(tmp_path, "Original.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale\n", encoding="utf-8")
    before = profile.read_text(encoding="utf-8")
    assert run_main(monkeypatch, "--check", "--skill", str(skill), "--profile", str(profile)) == 1
    assert profile.read_text(encoding="utf-8") == before


def test_sync_then_check_is_clean(tmp_path, monkeypatch):
    skill = write_skill(tmp_path, "Original.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale\n", encoding="utf-8")
    assert run_main(monkeypatch, "--skill", str(skill), "--profile", str(profile)) == 0
    assert run_main(monkeypatch, "--check", "--skill", str(skill), "--profile", str(profile)) == 0


def test_sync_is_idempotent(tmp_path, monkeypatch):
    skill = write_skill(tmp_path, "Original.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale\n", encoding="utf-8")
    run_main(monkeypatch, "--skill", str(skill), "--profile", str(profile))
    once = profile.read_text(encoding="utf-8")
    run_main(monkeypatch, "--skill", str(skill), "--profile", str(profile))
    assert profile.read_text(encoding="utf-8") == once


def test_missing_skill_directory_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="no such skill directory"):
        run_main(monkeypatch, "--skill", str(tmp_path / "nope"), "--profile", str(tmp_path / "p.md"))


def test_both_paths_are_required(monkeypatch, capsys):
    """The skill and the profile are evaluation content: there is no in-repo pair to default
    to, and defaulting to one would silently sync the wrong files."""
    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch)
    assert excinfo.value.code == 2
    assert "--skill" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        run_main(monkeypatch, "--skill", "some-skill")


def test_the_out_of_sync_message_names_both_paths(tmp_path, monkeypatch, capsys):
    """The message is the fix instruction; with required flags a bare command would not run."""
    skill = write_skill(tmp_path, "Original.")
    profile = tmp_path / "tmp-skilltext.md"
    profile.write_text(FRONTMATTER + "stale\n", encoding="utf-8")
    assert run_main(monkeypatch, "--check", "--skill", str(skill), "--profile", str(profile)) == 1
    err = capsys.readouterr().err
    assert f"--skill {skill}" in err
    assert f"--profile {profile}" in err


def test_frontmatter_block_opening_fence_must_be_exactly_three_dashes():
    # parse_frontmatter reads these as body, so the copied "header" must be empty too.
    for first in ("-----", "--- not a fence", "---x", "  ---"):
        assert sync.frontmatter_block(f"{first}\nintro\n---\nrest\n") == ""

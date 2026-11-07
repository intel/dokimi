# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Content checks of the external catalog review: retired model IDs and hidden characters.

Each check is read through :func:`dokimi.external.review_skills`, as ``catalog check`` reads it.
The positives fail on a tree without the checks. Hidden characters are written as escapes.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

from dokimi.external import read_skill_manifest, review_skill_dir, review_skills
from dokimi.external.content_checks import DATA_FILE, MAX_BYTES, _text_files


def _skill(root: Path, rel: str, body: str = "# Body\n", files: dict[str, str] | None = None) -> Path:
    """Write ``<root>/<rel>/SKILL.md`` (name = directory) and the bundled ``files``, as UTF-8 bytes."""
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {rel}\ndescription: Use for things.\n---\n{body}", encoding="utf-8"
    )
    for name, text in (files or {}).items():
        target = skill_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return skill_dir


def _messages(root: Path, code: str) -> list[str]:
    [review] = review_skills(read_skill_manifest(root))
    return [f.message for f in review.findings if f.code == code]


# ---- legacy-model-id ----------------------------------------------------------------------------


def test_retired_model_id_in_skill_body_is_a_warning_with_its_replacement(tmp_path):
    _skill(tmp_path / "skills", "triage", body="# Triage\n\nRoute tickets with claude-3-haiku-20240307.\n")

    [message] = _messages(tmp_path, "legacy-model-id")
    assert message.startswith("SKILL.md line 7:")
    assert "retired by anthropic on 2026-04-20" in message
    assert "use claude-haiku-4-5-20251001" in message


def test_deprecated_id_names_its_retirement_date(tmp_path):
    _skill(tmp_path / "skills", "summary", body="Model: claude-sonnet-4-5\n")

    [message] = _messages(tmp_path, "legacy-model-id")
    assert "deprecated by anthropic, retires 2026-11-30" in message
    assert "use claude-sonnet-5-5" in message


def test_legacy_id_in_bundled_script_names_the_file_and_line(tmp_path):
    _skill(tmp_path / "skills", "fetch", files={"scripts/call.py": 'MODEL = "claude-2.1"\n'})

    [message] = _messages(tmp_path, "legacy-model-id")
    assert message.startswith("scripts/call.py line 1:")


def test_model_id_inside_a_longer_name_or_a_version_step_is_not_matched(tmp_path):
    # "gpt-4" is listed, but "gpt-4.1" is a different model; "-notes" makes the opus ID part of a longer name.
    _skill(
        tmp_path / "skills",
        "naming",
        body="Write to my-claude-3-opus-20240229-notes.md, use gpt-4.1 and claude-opus-4-8.\n",
    )

    assert _messages(tmp_path, "legacy-model-id") == []


def test_model_id_match_is_case_sensitive_so_prose_names_do_not_match(tmp_path):
    _skill(tmp_path / "skills", "prose", body="Compared with Claude 3 Opus and GPT-4 in the docs.\n")

    assert _messages(tmp_path, "legacy-model-id") == []


def test_root_skill_does_not_scan_the_whole_checkout(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\ndescription: Use for things.\n---\n# Root\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.py").write_text('MODEL = "claude-2.1"\n', encoding="utf-8")

    assert _messages(tmp_path, "legacy-model-id") == []


def test_listed_ids_are_unique_and_each_vendor_has_a_source_page():
    data = tomllib.loads(DATA_FILE.read_text(encoding="utf-8"))
    ids = [model_id for entry in data["model"] for model_id in entry["ids"]]

    assert len(ids) == len(set(ids))
    for entry in data["model"]:
        assert entry["vendor"] in data["sources"]
        assert entry["status"] in {"deprecated", "retired"}


# ---- hidden-unicode -----------------------------------------------------------------------------


def test_bidi_override_is_reported_with_line_and_codepoint(tmp_path):
    _skill(tmp_path / "skills", "bidi", body="# Bidi\n\nSafe line.\nRun \u202e this\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert "line 8" in message
    assert "U+202E" in message


def test_tag_characters_are_reported(tmp_path):
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "rm")  # U+E0072 U+E006D: invisible in most renderers
    _skill(tmp_path / "skills", "tags", body=f"# Tags\n\nplain{hidden}\n")

    messages = _messages(tmp_path, "hidden-unicode")
    assert len(messages) == 2
    assert "U+E0072" in messages[0] and "U+E006D" in messages[1]


def test_stray_zero_width_space_is_reported(tmp_path):
    _skill(tmp_path / "skills", "zw", body="# Zw\n\nvisible\u200bgap\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert "line 7" in message
    assert "U+200B" in message


def test_zero_width_joiner_between_latin_letters_is_reported(tmp_path):
    _skill(tmp_path / "skills", "joiner", body="# J\n\na\u200db\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert "U+200D" in message


def test_zero_width_joiner_in_an_emoji_sequence_is_not_reported(tmp_path):
    family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
    _skill(tmp_path / "skills", "family", body=f"# Family\n\n{family} team\n")

    assert _messages(tmp_path, "hidden-unicode") == []


def test_zero_width_non_joiner_in_arabic_text_is_not_reported(tmp_path):
    _skill(tmp_path / "skills", "arabic", body="# Arabic\n\nكتاب\u200cات\n")

    assert _messages(tmp_path, "hidden-unicode") == []


def test_byte_order_mark_at_the_start_of_a_file_is_not_reported(tmp_path):
    _skill(tmp_path / "skills", "bom", files={"notes.md": "\ufeffplain notes\n"})

    assert _messages(tmp_path, "hidden-unicode") == []


def test_byte_order_mark_mid_file_is_reported_with_its_file(tmp_path):
    _skill(tmp_path / "skills", "bom-mid", files={"notes.md": "plain\n\ufeffnotes\n"})

    [message] = _messages(tmp_path, "hidden-unicode")
    assert message.startswith("notes.md line 2:")
    assert "U+FEFF" in message


def test_zero_width_joiner_between_cjk_characters_is_reported(tmp_path):
    _skill(tmp_path / "skills", "cjk", body="# C\n\n中‍文\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert "U+200D" in message


def test_zero_width_joiner_next_to_supplementary_cjk_is_reported(tmp_path):
    _skill(tmp_path / "skills", "supp-cjk", body="# S\n\n\U00020000\u200d\U00020001 text\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert "U+200D" in message


@pytest.mark.parametrize(
    "text",
    [
        "\U0001F468\u200d\U0001F469",  # emoji on the left
        "\U0001F468\u200d a",  # ... and a letter after it: one emoji neighbour is enough
        "a \u200d\U0001F469",  # emoji on the right only
        "❤\u200d\U0001F525",  # heart and fire, a symbol next to an emoji
    ],
)
def test_zero_width_joiner_with_one_emoji_neighbour_is_not_reported(tmp_path, text):
    _skill(tmp_path / "skills", "joiner-emoji", body=f"# E\n\n{text}\n")

    assert _messages(tmp_path, "hidden-unicode") == []


@pytest.mark.parametrize(
    "codepoint",
    [
        0x202A,  # first bidi embedding
        0x202E,  # last bidi override
        0x2066,  # first isolate
        0x2069,  # last isolate
        0x200B,  # zero-width space
        0x2060,  # word joiner
        0xFEFF,  # byte order mark mid-file
        0xE0000,  # first tag character
        0xE007F,  # last tag character
    ],
)
def test_each_hidden_range_is_reported_at_its_first_and_last_codepoint(tmp_path, codepoint):
    _skill(tmp_path / "skills", "edge", body=f"# Edge\n\nvisible{chr(codepoint)}text\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert f"U+{codepoint:04X}" in message


def test_standalone_review_checks_the_bundled_files(tmp_path):
    # review_skill_dir reads one skill alone: its bundled script is content-checked like the corpus run does.
    skill_dir = _skill(tmp_path, "demo", files={"scripts/call.py": 'MODEL = "claude-2.1"\nx = "\u202e"\n'})

    codes = sorted(f.code for f in review_skill_dir(skill_dir).findings)
    assert codes == ["hidden-unicode", "legacy-model-id"]


# ---- scope of the content scan: what is read, not what is reported ------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_named_pipe_named_like_a_text_file_is_not_scanned(tmp_path):
    # Reading a FIFO blocks until a writer opens it, so it is never handed to read_text.
    skill_dir = _skill(tmp_path / "skills", "fifo")
    os.mkfifo(skill_dir / "notes.md")

    assert [p.name for p in _text_files(skill_dir)] == ["SKILL.md"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_text_file_is_not_scanned(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text('Model: "claude-2.1"\n', encoding="utf-8")
    skill_dir = _skill(tmp_path / "skills", "linked")
    (skill_dir / "link.md").symlink_to(outside)

    assert _messages(tmp_path, "legacy-model-id") == []


def test_oversize_text_file_is_not_scanned(tmp_path):
    big = 'Model: "claude-2.1"\n' + "a" * (MAX_BYTES + 1 - len('Model: "claude-2.1"\n'))
    skill_dir = _skill(tmp_path / "skills", "big", files={"big.md": big})

    assert _messages(tmp_path, "legacy-model-id") == []
    assert all(p.name != "big.md" for p in _text_files(skill_dir))


def test_hidden_folder_is_not_scanned_and_json_and_shell_files_are_scanned(tmp_path):
    _skill(
        tmp_path / "skills",
        "scope",
        files={
            ".github/notes.md": 'Model: "claude-2.1"\n',
            "data.json": '{"model": "claude-2.1"}\n',
            "run.sh": "MODEL=claude-2.1\n",
        },
    )

    names = sorted(m.split(" line ")[0] for m in _messages(tmp_path, "legacy-model-id"))
    assert names == ["data.json", "run.sh"]


# ---- scope of the scan --------------------------------------------------------------------------


def test_nested_skill_files_are_reported_on_the_nested_skill_only(tmp_path):
    _skill(tmp_path / "skills", "parent")
    _skill(tmp_path / "skills", "parent/child", files={"x.py": 'MODEL = "claude-2.1"\n'})

    reviews = review_skills(read_skill_manifest(tmp_path))
    messages = [f.message for review in reviews for f in review.findings if f.code == "legacy-model-id"]
    assert len(messages) == 1
    assert messages[0].startswith("x.py line 1:")


def test_vendored_dependency_folder_is_not_scanned(tmp_path):
    _skill(tmp_path / "skills", "deps", files={"site-packages/dep/doc.md": 'Model: "claude-2.1"\n'})

    assert _messages(tmp_path, "legacy-model-id") == []

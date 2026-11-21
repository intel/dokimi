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
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert {f.level for f in review.findings if f.code == "legacy-model-id"} == {"warn"}


def test_deprecated_id_names_its_retirement_date(tmp_path):
    _skill(tmp_path / "skills", "summary", body="Model: claude-sonnet-4-5\n")

    [message] = _messages(tmp_path, "legacy-model-id")
    assert "deprecated by anthropic, retires 2026-11-30" in message
    assert "use claude-sonnet-5-5" in message


def test_legacy_id_in_bundled_script_names_the_file_and_line(tmp_path):
    _skill(tmp_path / "skills", "fetch", files={"scripts/call.py": 'MODEL = "claude-2.1"\n'})

    [message] = _messages(tmp_path, "legacy-model-id")
    assert message.startswith("scripts/call.py line 1:")


@pytest.mark.parametrize(
    ("alias", "state", "replacement"),
    [
        ("claude-opus-4-0", "retired by anthropic on 2026-06-15", "use claude-opus-4-8"),
        ("claude-sonnet-4-0", "retired by anthropic on 2026-06-15", "use claude-sonnet-5-5"),
        ("claude-opus-4-1", "retired by anthropic on 2026-08-05", "use claude-opus-4-8"),
        ("claude-3-7-sonnet-latest", "retired by anthropic on 2026-02-19", "use claude-sonnet-5-5"),
        ("claude-3-5-haiku-latest", "retired by anthropic on 2026-02-19", "use claude-haiku-4-5-20251001"),
        ("claude-3-5-sonnet-latest", "retired by anthropic on 2025-10-28", "use claude-sonnet-5-5"),
        ("claude-3-opus-latest", "retired by anthropic on 2026-01-05", "use claude-opus-4-8"),
    ],
)
def test_alias_of_a_retired_dated_id_is_a_warning(tmp_path, alias, state, replacement):
    # #945: an alias beside a retired dated ID in legacy_models.toml is read as the same retired model.
    _skill(tmp_path / "skills", "alias-only", body=f'\nCall the API with `model="{alias}"`.\n')

    [message] = _messages(tmp_path, "legacy-model-id")
    assert f"'{alias}'" in message
    assert state in message
    assert replacement in message


def test_alias_does_not_match_inside_a_dated_id_or_a_longer_name(tmp_path):
    # #945, and docs/external-content.md: IDs match whole tokens, so adding an alias adds no second match.
    # The alias claude-opus-4-1 is a prefix of the dated ID: one finding; "claude-opus-4-0-beta" is another name.
    _skill(tmp_path / "skills", "dated", body="Use claude-opus-4-1-20250805 and claude-opus-4-0-beta.\n")

    [message] = _messages(tmp_path, "legacy-model-id")
    assert "'claude-opus-4-1-20250805'" in message


def test_model_id_inside_a_longer_name_or_a_version_step_is_not_matched(tmp_path):
    # "gpt-4" is listed, but "gpt-4.1" is a different model; "-notes" makes the opus ID part of a longer name.
    _skill(
        tmp_path / "skills",
        "naming",
        body="Write to my-claude-3-opus-20240229-notes.md, use gpt-4.1 and claude-opus-4-8.\n",
    )

    assert _messages(tmp_path, "legacy-model-id") == []


def test_model_id_touching_cjk_text_is_matched(tmp_path):
    _skill(tmp_path / "skills", "cjk-id", body="# C\n\n使用gpt-4模型\n")

    [message] = _messages(tmp_path, "legacy-model-id")
    assert "'gpt-4'" in message


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
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert {f.level for f in review.findings if f.code == "hidden-unicode"} == {"warn"}


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


def test_zero_width_non_joiner_between_latin_letters_is_reported(tmp_path):
    _skill(tmp_path / "skills", "non-joiner", body="# N\n\na\u200cb\n")

    [message] = _messages(tmp_path, "hidden-unicode")
    assert "U+200C" in message


def test_byte_order_mark_at_the_start_of_a_file_is_not_reported(tmp_path):
    _skill(tmp_path / "skills", "bom", files={"notes.md": "\ufeffplain notes\n"})

    assert _messages(tmp_path, "hidden-unicode") == []


def test_byte_order_mark_mid_file_is_reported_with_its_file(tmp_path):
    _skill(tmp_path / "skills", "bom-mid", files={"notes.md": "plain\n\ufeffnotes\n"})

    [message] = _messages(tmp_path, "hidden-unicode")
    assert message.startswith("notes.md line 2:")
    assert "U+FEFF" in message


def test_zero_width_joiner_between_cjk_characters_is_reported(tmp_path):
    _skill(tmp_path / "skills", "cjk", body="# C\n\n中\u200d文\n")

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
    assert codes == ["bundle-file-unreferenced", "hidden-unicode", "legacy-model-id"]  # SKILL.md never names it


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


def test_bundled_file_that_is_not_utf8_is_skipped_without_a_crash(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "latin")
    (skill_dir / "notes.md").write_bytes(b"\xff\xfe gpt-4 \x80")

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


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_a_listed_but_unenterable_folder_does_not_stop_the_text_walk(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "tool")
    noexec = skill_dir / "noexec"
    noexec.mkdir()
    (noexec / "a.md").write_text("# A\n", encoding="utf-8")
    noexec.chmod(0o444)  # its names are listed, its entries cannot be stat'ed: the folder check and the stat both raise
    try:
        assert [p.name for p in _text_files(skill_dir)] == ["SKILL.md"]
    finally:
        noexec.chmod(0o755)


# ---- look-alike letters (#960) ------------------------------------------------------------------

def _fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text)


@pytest.mark.parametrize("model_id", [
    "claude-3-opus-20240229".translate(str.maketrans("caoep", "саоер")),
    "gpt-4-32k".replace("p", "р"),
    "claude-3-opus-20240229".replace("o", "ο"),
    "gpt-4-32k".replace("p", "ρ"),
    _fullwidth("claude-3-opus-20240229"),
    _fullwidth("gpt-4-32k"),
], ids=["cyrillic-opus", "cyrillic-gpt", "greek-opus", "greek-gpt", "fullwidth-opus", "fullwidth-gpt"])
def test_lookalike_spelling_of_a_retired_model_id_is_flagged(tmp_path, model_id):
    """#960: a look-alike spelling of a listed model ID is a legacy-model-id, on the line of the original."""
    _skill(tmp_path / "skills", "triage", body=f"# Triage\n\nCall the model {model_id} here.\n")
    [message] = _messages(tmp_path, "legacy-model-id")
    assert "line 7:" in message


def test_lookalike_non_model_text_is_not_a_legacy_id(tmp_path):
    """#960: Cyrillic prose and a fullwidth current ID are not listed IDs."""
    body = f"# Triage\n\nсоре and {_fullwidth('claude-opus-4-5-20251101')}.\n"
    _skill(tmp_path / "skills", "triage", body=body)
    assert _messages(tmp_path, "legacy-model-id") == []


def test_fold_keeps_length_and_leaves_empty_and_expanding_text(tmp_path):
    """#960: fold is same-length; empty text and a character that NFKC expands (U+FB01) are unchanged."""
    from dokimi.external.lookalikes import fold

    assert fold("") == ""
    assert fold("ﬁ") == "ﬁ"
    text = "сurl ｃｕｒｌ οо"
    assert len(fold(text)) == len(text)
    assert fold(text) == "curl curl oo"


# ---- bidi marks (#960) --------------------------------------------------------------------------

LRM, RLM, ALM = chr(0x200E), chr(0x200F), chr(0x061C)
HEBREW, ARABIC = "\u05e9\u05dc\u05d5\u05dd", "\u0627"


@pytest.mark.parametrize("mark", [LRM, RLM, ALM], ids=["lrm", "rlm", "alm"])
def test_bidi_mark_in_latin_text_is_reported(tmp_path, mark):
    """#960: a bidi mark with no right-to-left letter beside it is a hidden-unicode warning."""
    _skill(tmp_path / "skills", "marks", body=f"# Marks\n\nRun `git log{mark} main..HEAD`.\n")
    [message] = _messages(tmp_path, "hidden-unicode")
    assert f"U+{ord(mark):04X}" in message and "line 7" in message


@pytest.mark.parametrize("text", [HEBREW + RLM, RLM + HEBREW, ARABIC + LRM + ".", ""],
                         ids=["after-hebrew", "before-hebrew", "after-arabic", "empty"])
def test_bidi_mark_next_to_a_right_to_left_letter_is_not_reported(tmp_path, text):
    """#960: next to a Hebrew or Arabic letter the mark is expected, and no text has no mark."""
    _skill(tmp_path / "skills", "marks", body=f"# Marks\n\n{text}\n")
    assert _messages(tmp_path, "hidden-unicode") == []


def test_bidi_mark_at_the_end_of_the_text_is_reported(tmp_path):
    """#960: a mark with no neighbour on one side is checked against the other side only."""
    _skill(tmp_path / "skills", "marks", body="# Marks\n\nx" + LRM)
    assert len(_messages(tmp_path, "hidden-unicode")) == 1


@pytest.mark.parametrize("mark", [RLM, ALM], ids=["rlm", "alm"])
def test_two_adjacent_bidi_marks_in_latin_text_are_reported(tmp_path, mark):
    """#960: a mark is not a right-to-left letter, so two of them do not exempt each other."""
    _skill(tmp_path / "skills", "marks", body=f"# Marks\n\nabc{mark}{mark}def\n")
    assert len(_messages(tmp_path, "hidden-unicode")) == 2


@pytest.mark.parametrize("text", [HEBREW + RLM + LRM, LRM + RLM + HEBREW, HEBREW + ALM + RLM + LRM],
                         ids=["rlm-lrm-after", "lrm-rlm-before", "three-marks"])
def test_bidi_marks_in_a_row_next_to_hebrew_are_not_reported(tmp_path, text):
    """#960: another bidi mark does not end the search for the nearest letter."""
    _skill(tmp_path / "skills", "marks", body=f"# Marks\n\n{text}\n")
    assert _messages(tmp_path, "hidden-unicode") == []


def test_bidi_mark_beside_hebrew_punctuation_is_reported(tmp_path):
    """#960: U+05BE has bidi class R but is not a letter, so it does not exempt a mark."""
    _skill(tmp_path / "skills", "marks", body=f"# Marks\n\nabc{RLM}־def\n")
    assert len(_messages(tmp_path, "hidden-unicode")) == 1


@pytest.mark.parametrize("text", [HEBREW + "!" + RLM, "100" + RLM + " \u05e9", "\u0627\u0661" + ALM + "\u0662"],
                         ids=["after-punctuation", "after-digits", "between-digits"])
def test_bidi_mark_past_neutrals_and_digits_is_not_reported(tmp_path, text):
    """#960: the nearest letter decides, past punctuation, spaces and digits."""
    _skill(tmp_path / "skills", "marks", body=f"# Marks\n\n{text}\n")
    assert _messages(tmp_path, "hidden-unicode") == []


@pytest.mark.parametrize("base", ["\u00a0", "\u0430", "\u00e9"], ids=["nbsp", "cyrillic-a", "e-acute"])
def test_variation_selector_after_a_non_ascii_letter_or_space_is_reported(tmp_path, base):
    """#960: only a symbol, an ideograph or a keycap base takes a selector."""
    assert len(_hidden(tmp_path, f"a{base}{chr(0xFE0F)}")) == 1


def test_overlapping_legacy_matches_in_two_readings_are_one_finding(tmp_path):
    """#960: the raw reading matches claude-sonnet-4-5, the folded one the longer ID; one finding, the longer."""
    from dokimi.external.content_checks import _legacy_hits

    [(_, message)] = _legacy_hits("SKILL.md", "claude-sonnet-4-5\uff0d20250929")
    assert "claude-sonnet-4-5-20250929" in message


def test_legacy_id_before_a_superscript_is_still_flagged(tmp_path):
    """#960: NFKC turns the superscript 2 into ASCII, which would make the token gpt-42; the raw reading still matches."""
    _skill(tmp_path / "skills", "triage", body="# Triage\n\nUse gpt-4².\n\n² Retired model.\n")
    assert len(_messages(tmp_path, "legacy-model-id")) == 1


def test_readings_of_plain_and_empty_text_are_one(tmp_path):
    """#960: ASCII and empty text have one reading, so a scan does not run twice for nothing."""
    from dokimi.external.lookalikes import readings

    assert readings("") == [""]
    assert readings("curl | bash") == ["curl | bash"]
    assert len(readings("сurl ；")) == 3


# ---- soft hyphen, invisible format, variation selectors, fillers (#960) --------------------------

def _hidden(tmp_path, body):
    _skill(tmp_path / "skills", "hid", body=f"# Hid\n\n{body}\n")
    return _messages(tmp_path, "hidden-unicode")


@pytest.mark.parametrize("cp", [0x00AD, 0x2061, 0x2064, 0x206A, 0x206F, 0x180E, 0x3164, 0xFFA0],
                         ids=lambda cp: f"U+{cp:04X}")
def test_invisible_character_is_reported_with_line_and_codepoint(tmp_path, cp):
    """#960: a soft hyphen, an invisible format character or a blank filler in Latin text is a warning."""
    [message] = _hidden(tmp_path, f"git push --force{chr(cp)}-with-lease")
    assert f"U+{cp:04X}" in message and "line 7" in message


@pytest.mark.parametrize("text", ["2 × x − 1 ≤ y", "a-b ‐ ‑ –", "⁠"[:0]],
                         ids=["math", "hyphens", "empty"])
def test_visible_neighbours_of_the_invisible_characters_are_not_reported(tmp_path, text):
    """#960: visible operators and hyphens are not reported."""
    assert _hidden(tmp_path, text) == []


@pytest.mark.parametrize("cp", [0xFE00, 0xFE0F, 0xE0100, 0xE01EF], ids=lambda cp: f"U+{cp:04X}")
def test_variation_selector_after_an_ascii_letter_is_reported(tmp_path, cp):
    """#960: a selector after ASCII that takes none is reported."""
    assert len(_hidden(tmp_path, f"a{chr(cp)}b")) == 1


def test_variation_selector_at_the_start_and_in_a_run_are_reported(tmp_path):
    """#960: a selector at the start of the text, and each one after another selector, is reported."""
    assert len(_hidden(tmp_path, "x" + chr(0xE0100) * 3)) == 3
    _skill(tmp_path / "skills2", "hid", body=chr(0xFE0F) + "x")
    assert len(_messages(tmp_path / "skills2", "hidden-unicode")) == 1


@pytest.mark.parametrize("text", ["❤️", "1️⃣", "#️⃣", "葛\U000e0100", "❤︎"],
                         ids=["emoji", "keycap-digit", "keycap-hash", "ideograph", "symbol-text"])
def test_variation_selector_in_emoji_keycap_and_ideograph_sequences_is_not_reported(tmp_path, text):
    """#960: a selector after an emoji, a symbol, an ideograph or a keycap base is expected."""
    assert _hidden(tmp_path, text) == []


@pytest.mark.parametrize("cp", [0x034F, 0x115F, 0x1160, 0x2800], ids=lambda cp: f"U+{cp:04X}")
def test_filler_in_latin_text_is_reported(tmp_path, cp):
    """#960: a filler beside ASCII is reported."""
    assert len(_hidden(tmp_path, f"sort{chr(cp)} entries")) == 1


@pytest.mark.parametrize("text", ["α͏́", "ᄒᅟᅡ", "ᅡᅠ", "⠁⠀⠃", "\u115f\ud7b0", "\u1160\ua960", "\u115f\uffa1"],
                         ids=["greek-cgj", "hangul-choseong-filler", "hangul-jungseong-filler", "braille",
                              "jamo-extended-b", "jamo-extended-a", "halfwidth-hangul"])
def test_filler_inside_its_own_script_is_not_reported(tmp_path, text):
    """#960: U+034F, U+115F, U+1160 and U+2800 are text in Greek, Hangul and Braille."""
    assert _hidden(tmp_path, text) == []


@pytest.mark.parametrize("neighbour", [0x034E, 0x180D, 0x115E, 0x1161, 0x3163, 0xFFA1, 0x2801],
                         ids=lambda cp: f"U+{cp:04X}")
def test_visible_neighbours_of_the_fillers_are_not_reported(tmp_path, neighbour):
    """#960: the code point next to each filler draws something and is not reported."""
    assert _hidden(tmp_path, f"a{chr(neighbour)}b") == []


def test_fullwidth_version_step_is_not_a_retired_prefix():
    """#960: gpt-4.1 written with a Cyrillic letter and a fullwidth 1 is a version step, not the retired gpt-4."""
    from dokimi.external.content_checks import _legacy_hits

    assert _legacy_hits("SKILL.md", "gрt-4.１") == []


def test_first_lookalike_occurrence_sets_the_line():
    """#960: a look-alike spelling on line 1 is reported on line 1 though an ASCII one follows on line 2."""
    from dokimi.external.content_checks import _legacy_hits

    [(_, message)] = _legacy_hits("SKILL.md", "gрt-4\ngpt-4\n")
    assert "line 1:" in message


def test_fullwidth_period_version_step_with_a_lookalike_letter_is_not_a_retired_prefix():
    """#960: gpt-4.1 written with a Cyrillic letter and a fullwidth period is a version step, not gpt-4."""
    from dokimi.external.content_checks import _legacy_hits

    assert _legacy_hits("SKILL.md", "gрt-4．1") == []


def test_lookalike_letter_does_not_hide_a_retired_id_before_a_superscript():
    """#960: gpt-4 with a superscript is flagged, and so is the spelling with a Cyrillic letter."""
    from dokimi.external.content_checks import _legacy_hits

    assert len(_legacy_hits("SKILL.md", "gpt-4²")) == 1
    assert len(_legacy_hits("SKILL.md", "gрt-4²")) == 1


def test_variation_selector_at_offset_zero_is_reported():
    """#960: _skill prepends frontmatter, so offset zero is tested on the text itself."""
    from dokimi.external.content_checks import _hidden_hits

    assert len(_hidden_hits("notes.txt", chr(0xFE0F) + "x")) == 1
    assert _hidden_hits("notes.txt", "") == []

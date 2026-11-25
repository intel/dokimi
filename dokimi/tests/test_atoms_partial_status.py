# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`load_atoms_record` refuses a partial atoms file by its `status` as well as by its `partial` block."""

from __future__ import annotations

import json

import pytest

from dokimi.novelty.atoms import load_atoms_record
from dokimi.novelty.atoms_refusal import has_visible_text
from dokimi.novelty.client import SpendCap, SpendCapReached
from dokimi.files import InputError

_BLOCK = {"reason": "spend_cap", "message": "stopped"}


@pytest.mark.parametrize("record", [
    {"schema_version": "novelty-atoms.v1", "status": "partial", "partial": _BLOCK},
    {"schema_version": "novelty-atoms.v1", "partial": _BLOCK},
], ids=["both", "block-only"])
def test_a_partial_atoms_file_is_refused(tmp_path, record):
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(InputError) as exc:
        load_atoms_record(path)
    assert str(exc.value).startswith(f"{path} is a partial atoms file: ")
    assert "Rerun with a higher --max-usd" in str(exc.value)


@pytest.mark.parametrize("record", [
    {"schema_version": "novelty-atoms.v1", "status": "partial"},
    {"schema_version": "novelty-atoms.v1", "partial": "stopped"},
    {"schema_version": "novelty-atoms.v1", "partial": {}},
], ids=["status-only", "block-not-a-dict", "block-empty"])
def test_a_partial_marker_without_a_block_is_not_blamed_on_a_cap(tmp_path, record):
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(InputError) as exc:
        load_atoms_record(path)
    assert str(exc.value) == f"{path} is marked partial with no `partial` block: truncated or hand-edited"
    assert "--max-usd" not in str(exc.value)


def test_a_partial_file_that_stopped_on_a_missing_price_says_so(tmp_path):
    path = tmp_path / "atoms.json"
    record = {"status": "partial", "partial": {"reason": "no_price", "message": "no price for m"}}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(InputError, match="no price for m, so it is not a finished atom set"):
        load_atoms_record(path)


_NO_PRICE = "a call with no known price stopped the run"
_PERMANENT = "a permanent provider error stopped the run"
_ADVICE = {"no_price": "Drop --max-usd to run unmetered", "permanent_error": "Fix the error and rerun"}
_FALLBACK = {"no_price": _NO_PRICE, "permanent_error": _PERMANENT}
_MISSING = object()


@pytest.mark.parametrize("reason", ["no_price", "permanent_error"])
@pytest.mark.parametrize("message", [_MISSING, "", "  ", "\n\t", 5, None, ["x"], "\u200b",
                                     "\u3164", "\u115f", "\uffa0", "\u2800", "\u0301", "\u034f"],
                         ids=["absent", "empty", "spaces", "whitespace", "int", "null", "list", "zero-width",
                              "u3164", "u115f", "uffa0", "u2800", "u0301", "u034f"])
def test_a_partial_file_without_a_usable_message_names_its_stop(tmp_path, reason, message):
    # docs/skill-novelty.md:1216-1217 (`probe` and the other atom readers refuse a partial atoms file)
    path = tmp_path / "atoms.json"
    block = {"reason": reason} if message is _MISSING else {"reason": reason, "message": message}
    path.write_text(json.dumps({"status": "partial", "partial": block}), encoding="utf-8")
    with pytest.raises(InputError) as exc:
        load_atoms_record(path)
    assert str(exc.value) == (f"{path} is a partial atoms file: {_FALLBACK[reason]}, "
                              f"so it is not a finished atom set. {_ADVICE[reason]}")


@pytest.mark.parametrize("reason", ["no_price", "permanent_error"])
@pytest.mark.parametrize("message", [" bad key ", "\u3164 quota exceeded"], ids=["plain", "filler-then-text"])
def test_a_partial_file_with_a_real_message_prints_it_as_is(tmp_path, reason, message):
    # docs/skill-novelty.md:1216-1217
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps({"status": "partial", "partial": {"reason": reason, "message": message}}),
                    encoding="utf-8")
    with pytest.raises(InputError) as exc:
        load_atoms_record(path)
    assert str(exc.value) == (f"{path} is a partial atoms file: {message}, so it is not a finished atom set. "
                              f"{_ADVICE[reason]}")


def _refusal(tmp_path, reason, message):
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps({"status": "partial", "partial": {"reason": reason, "message": message}}),
                    encoding="utf-8")
    with pytest.raises(InputError) as exc:
        load_atoms_record(path)
    return path, str(exc.value)


def _real_no_price_message():
    cap = SpendCap(limit_usd=1.0)
    with pytest.raises(SpendCapReached) as exc:
        cap.record(None, "m")
    return str(exc.value)


def test_a_no_price_message_that_ends_with_the_advice_gets_it_once(tmp_path):
    # docs/skill-novelty.md:1216-1217 (#921)
    message = _real_no_price_message()
    assert message.endswith(_ADVICE["no_price"])
    path, text = _refusal(tmp_path, "no_price", message)
    assert text == f"{path} is a partial atoms file: {message}; it is not a finished atom set."
    assert text.count(_ADVICE["no_price"]) == 1


@pytest.mark.parametrize("tail", [".", " ", ".\n", " . ", " . .", ". .  .\t", "\u00a0", ".\u2028", "\u3000."],
                         ids=["period", "space", "period-newline", "spaced-period", "mixed", "mixed-long",
                              "no-break-space", "line-separator", "ideographic-space"])
def test_a_message_with_trailing_periods_or_whitespace_after_the_advice_gets_it_once(tmp_path, tail):
    # docs/skill-novelty.md:1216-1217 (#921)
    message = _real_no_price_message() + tail
    path, text = _refusal(tmp_path, "no_price", message)
    assert text == f"{path} is a partial atoms file: {message}; it is not a finished atom set."
    assert text.count(_ADVICE["no_price"]) == 1


def test_a_permanent_error_message_that_ends_with_the_advice_gets_it_once(tmp_path):
    # docs/skill-novelty.md:1216-1217 (#921)
    message = "bad key for m. Fix the error and rerun"
    path, text = _refusal(tmp_path, "permanent_error", message)
    assert text == f"{path} is a partial atoms file: {message}; it is not a finished atom set."
    assert text.count(_ADVICE["permanent_error"]) == 1


def test_a_message_with_the_advice_in_the_middle_still_gets_it_appended(tmp_path):
    # docs/skill-novelty.md:1216-1217 (#921)
    message = "Drop --max-usd to run unmetered, then retry"
    path, text = _refusal(tmp_path, "no_price", message)
    assert text == (f"{path} is a partial atoms file: {message}, so it is not a finished atom set. "
                    f"{_ADVICE['no_price']}")
    assert text.count(_ADVICE["no_price"]) == 2


@pytest.mark.parametrize(("text", "visible"), [
    ("\u0301", False), ("e\u0301", True), ("\u3164", False), ("\u3164\u200b \u0301", False),
    ("\u3164a", True), ("\u2800", False), ("\u2800.", True), ("7", True), ("$", True),
    ("\u00ad", False), ("\ue000", False), ("\u0378", False), ("\x07", False), ("", False),
    ("\u115f", False), ("\u1160", False), ("\uffa0", False), ("\u034f", False), ("\u00a0", False),
])
def test_has_visible_text_needs_a_letter_number_punctuation_or_symbol_that_shows(text, visible):
    # changelog.d/20261118_120000_invisible-partial-message.md (#881): a usable message has a letter, number, punctuation or symbol that shows
    assert has_visible_text(text) is visible


@pytest.mark.parametrize("reason", [["no_price"], {"a": 1}, 3, None])
def test_a_partial_file_with_a_reason_that_is_not_text_is_a_cap_stop(tmp_path, reason):
    # docs/skill-novelty.md:1216-1217
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps({"status": "partial", "partial": {"reason": reason, "message": "m"}}),
                    encoding="utf-8")
    with pytest.raises(InputError, match="Rerun with a higher --max-usd"):
        load_atoms_record(path)


def test_a_finished_status_is_not_refused(tmp_path):
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps({"atoms": [], "spans": [], "status": "complete"}), encoding="utf-8")
    assert load_atoms_record(path)[:2] == ([], [])

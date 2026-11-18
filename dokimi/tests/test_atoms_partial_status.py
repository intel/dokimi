# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`load_atoms_record` refuses a partial atoms file by its `status` as well as by its `partial` block."""

from __future__ import annotations

import json

import pytest

from dokimi.novelty.atoms import load_atoms_record
from dokimi.utils import InputError

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
@pytest.mark.parametrize("message", [_MISSING, "", "  ", "\n\t", 5, None, ["x"], "\u200b"],
                         ids=["absent", "empty", "spaces", "whitespace", "int", "null", "list", "zero-width"])
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
def test_a_partial_file_with_a_real_message_prints_it_as_is(tmp_path, reason):
    # docs/skill-novelty.md:1216-1217
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps({"status": "partial", "partial": {"reason": reason, "message": " bad key "}}),
                    encoding="utf-8")
    with pytest.raises(InputError) as exc:
        load_atoms_record(path)
    assert str(exc.value) == (f"{path} is a partial atoms file:  bad key , so it is not a finished atom set. "
                              f"{_ADVICE[reason]}")


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

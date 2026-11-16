# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""`skill novelty items` refuses a partial probe file with the advice its stop reason calls for (#789)."""
from __future__ import annotations

import json

import pytest

from dokimi.novelty.items import ProbeFileError, load_probe_cells

_ENDING = "it is not a finished measurement"


def _refusal(tmp_path, record: dict) -> str:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"status": "partial", "runs": [], **record}), encoding="utf-8")
    with pytest.raises(ProbeFileError) as caught:
        load_probe_cells([str(path)])
    text = str(caught.value)
    assert text.startswith(f"{path} is a partial probe result (") and text.endswith(_ENDING)
    return text


@pytest.mark.parametrize(("reason", "message", "named", "absent"), [
    ("spend_cap", "--max-usd stopped the probes with 3 of 9 answers", "--max-usd stopped the measurement", None),
    ("no_price", "no price for model m", "no price for model m", "--max-usd"),
    ("permanent_error", "permanent error: bad key", "permanent error: bad key", "--max-usd"),
])
def test_each_reason_is_refused_with_its_own_message(tmp_path, reason, message, named, absent):
    text = _refusal(tmp_path, {"partial": {"reason": reason, "message": message}})
    assert named in text
    if absent:
        assert absent not in text


def test_a_no_price_stop_with_no_message_says_what_cures_it(tmp_path):
    text = _refusal(tmp_path, {"partial": {"reason": "no_price"}})
    assert "no known price" in text and "price the model or drop --max-usd" in text
    assert "higher" not in text


def test_a_permanent_error_with_no_message_keeps_its_fallback(tmp_path):
    text = _refusal(tmp_path, {"partial": {"reason": "permanent_error"}})
    assert "a permanent provider error stopped the measurement" in text and "--max-usd" not in text


@pytest.mark.parametrize("record", [
    {},
    {"partial": {"message": "no price for model m"}},
    {"partial": ["no_price"]},
    {"partial": "no_price"},
    {"partial": {"reason": None, "message": "x"}},
])
def test_a_partial_record_with_no_reason_keeps_the_cap_wording(tmp_path, record):
    assert "(--max-usd stopped the measurement)" in _refusal(tmp_path, record)


@pytest.mark.parametrize("message", [None, 7, ["a"], {"a": 1}, ""])
@pytest.mark.parametrize("reason", ["no_price", "permanent_error", "other_reason"])
def test_a_message_that_is_not_text_is_a_refusal_not_a_crash(tmp_path, reason, message):
    text = _refusal(tmp_path, {"partial": {"reason": reason, "message": message}})
    assert "stopped the measurement" in text or "no known price" in text


def test_an_unknown_reason_with_a_message_names_the_message(tmp_path):
    assert "(disk gone)" in _refusal(tmp_path, {"partial": {"reason": "disk_full", "message": "disk gone"}})

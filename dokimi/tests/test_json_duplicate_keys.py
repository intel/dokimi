# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``json_utf8_bytes`` refuses an object that would hold two equal keys once a reader loads it (#1054, #1066)."""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import save_artifact
from dokimi.process_io import json_utf8_bytes

PAIR = chr(0xD83D) + chr(0xDE00)  # two code points, as a YAML double-quoted escape of U+1F600 loads
CHAR = "\U0001F600"
_MATCH = "two object keys are the same"


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_json_utf8_bytes_refuses_a_pair_key_beside_its_character_key(ensure_ascii):
    """With ``ensure_ascii=True`` both keys are the escape ``\\ud83d\\ude00``, which ``json.loads`` joins itself (#1054)."""
    text = json.dumps({"x": {PAIR: 1, CHAR: 2}}, indent=2, ensure_ascii=ensure_ascii)

    with pytest.raises(ValueError, match=_MATCH):
        json_utf8_bytes(text)


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_save_artifact_refuses_a_pair_key_beside_its_character_key_and_keeps_the_file(tmp_path, ensure_ascii):
    out = tmp_path / "q.json"
    out.write_text("old")
    extra = {PAIR: 1, CHAR: 2}

    with pytest.raises(ValueError, match=_MATCH):
        save_artifact("questions", {"questions": [{"id": "q1", "question": "Q", "extra": extra}]}, out,
                      ensure_ascii=ensure_ascii)

    assert out.read_text() == "old" and [p.name for p in tmp_path.iterdir()] == ["q.json"]


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_save_artifact_writes_a_pair_key_alone_and_a_lone_surrogate_key(tmp_path, ensure_ascii):
    """No repeated key: the check refuses nothing, and the file loads to the payload."""
    out = tmp_path / "q.json"
    extra = {PAIR: 1, "\ud800": 2, "?": 3}

    save_artifact("questions", {"questions": [{"id": "q1", "question": "Q", "extra": extra}]}, out, ensure_ascii=ensure_ascii)

    assert json.loads(out.read_bytes().decode("utf-8"))["questions"][0]["extra"] == {CHAR: 1, "\ud800": 2, "?": 3}


def test_the_same_key_in_different_objects_is_written_unchanged():
    payload = {"a": {"k": 1}, "b": {"k": 2}, "rows": [{"k": 1}, {"k": 2}], "k": {"k": {"k": 3}}}
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    assert json_utf8_bytes(text) == text.encode("utf-8")

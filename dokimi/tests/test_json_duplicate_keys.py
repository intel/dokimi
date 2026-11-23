# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``json_utf8_bytes`` refuses an object that would hold two equal keys once a reader loads it (#1054, #1066)."""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import save_artifact
from dokimi.datasets_export import to_jsonl
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


@pytest.mark.parametrize("keys", [(1, "1"), (True, "true"), (False, "false"), (1.5, "1.5"), (None, "null")])
def test_json_utf8_bytes_refuses_a_key_beside_the_string_json_dumps_writes_it_as(keys):
    """``json.dumps({1: "a", "1": "b"})`` is ``{"1": "a", "1": "b"}``: a reader keeps one entry (#1066)."""
    text = json.dumps({"rows": [{keys[0]: "a", keys[1]: "b"}]}, ensure_ascii=False)

    with pytest.raises(ValueError, match=_MATCH):
        json_utf8_bytes(text)


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_save_artifact_refuses_an_integer_key_beside_its_string_and_keeps_the_file(tmp_path, ensure_ascii):
    out = tmp_path / "q.json"
    out.write_text("old")

    with pytest.raises(ValueError, match=_MATCH):
        save_artifact("questions", {"questions": [{"id": "q1", "question": "Q", "extra": {1: "a", "1": "b"}}]}, out,
                      ensure_ascii=ensure_ascii)

    assert out.read_text() == "old" and [p.name for p in tmp_path.iterdir()] == ["q.json"]


def test_to_jsonl_refuses_an_integer_key_beside_its_string_before_it_writes_a_row(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"kept": 1}\n')

    with pytest.raises(ValueError, match=_MATCH):
        to_jsonl([{"a": 1}, {1: "a", "1": "b"}], path)
    with pytest.raises(ValueError, match=_MATCH):
        to_jsonl([{1: "a", "1": "b"}], tmp_path / "new.jsonl")

    assert path.read_bytes() == b'{"kept": 1}\n' and not (tmp_path / "new.jsonl").exists()


def test_an_integer_key_alone_is_written_as_its_string():
    assert json_utf8_bytes(json.dumps({1: "a", "2": "b"})) == b'{"1": "a", "2": "b"}'


def test_the_same_key_in_different_objects_is_written_unchanged():
    payload = {"a": {"k": 1}, "b": {"k": 2}, "rows": [{"k": 1}, {"k": 2}], "k": {"k": {"k": 3}}}
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    assert json_utf8_bytes(text) == text.encode("utf-8")

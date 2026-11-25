# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The JSON writers that do not go through ``json_utf8_bytes`` refuse two equal object keys (#1163; #1054, #1066)."""

from __future__ import annotations

import argparse
import json

import pytest

from dokimi.atomic_write import write_json_atomic
from dokimi.commands.novelty import cmd_novelty_items
from dokimi.commands.novelty_routing_report import save_result
from dokimi.commands.tools import _write_items
from dokimi.novelty.atoms import save_atoms
from dokimi.report.json_report import write_json
from dokimi.utils import InputError

PAIR = chr(0xD83D) + chr(0xDE00)
CHAR = "\U0001F600"
_MATCH = "two object keys are the same"
_COLLISIONS = [{1: "a", "1": "b"}, {PAIR: 1, CHAR: 2}]
_IDS = ["int-beside-string", "pair-beside-character"]


def _only(tmp_path, name):
    assert [p.name for p in tmp_path.iterdir()] == [name]


@pytest.mark.parametrize("keys", _COLLISIONS, ids=_IDS)
def test_write_json_atomic_refuses_equal_keys_and_keeps_the_file(tmp_path, keys):
    out = tmp_path / "a.json"
    out.write_text("old")

    with pytest.raises(ValueError, match=_MATCH):
        write_json_atomic(out, {"x": [keys]})

    assert out.read_text() == "old"
    _only(tmp_path, "a.json")


@pytest.mark.parametrize("keys", [{chr(0xD800): 1, "?": 2}, {chr(0xD800): 1, chr(0xD801): 2}], ids=["lone-beside-question-mark", "two-lone"])
def test_write_json_atomic_refuses_keys_that_are_equal_once_a_lone_surrogate_is_written_as_a_question_mark(tmp_path, keys):
    out = tmp_path / "a.json"
    out.write_text("old")

    with pytest.raises(ValueError, match=_MATCH):
        write_json_atomic(out, keys)

    assert out.read_text() == "old"
    _only(tmp_path, "a.json")


def test_write_json_atomic_writes_the_same_bytes_for_a_payload_with_no_repeated_key(tmp_path):
    out = tmp_path / "a.json"

    write_json_atomic(out, {"k": {"k": 1}, "rows": [{"k": 2}], 3: "é", "lone": "\ud800"})

    assert out.read_bytes() == b'{\n  "k": {\n    "k": 1\n  },\n  "rows": [\n    {\n      "k": 2\n    }\n  ],\n  "3": "\xc3\xa9",\n  "lone": "?"\n}\n'


@pytest.mark.parametrize("keys", _COLLISIONS, ids=_IDS)
def test_report_write_json_refuses_equal_keys_and_creates_nothing(tmp_path, keys):
    out = tmp_path / "sub" / "r.json"

    with pytest.raises(ValueError, match=_MATCH):
        write_json({"x": keys}, out)

    assert list(tmp_path.iterdir()) == []


def test_report_write_json_keeps_an_existing_file_on_a_refusal_and_writes_a_valid_payload(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("old")

    with pytest.raises(ValueError, match=_MATCH):
        write_json({1: "a", "1": "b"}, out)
    assert out.read_text() == "old"

    write_json({"a": {"a": 1}, 2: "é"}, out)
    assert out.read_bytes() == b'{\n  "a": {\n    "a": 1\n  },\n  "2": "\\u00e9"\n}'


@pytest.mark.parametrize("keys", _COLLISIONS, ids=_IDS)
def test_save_atoms_refuses_equal_keys_in_the_bundle_and_keeps_the_file(tmp_path, keys):
    out = tmp_path / "atoms.json"
    out.write_text("old")

    with pytest.raises(ValueError, match=_MATCH):
        save_atoms([], [], out, bundle=keys)

    assert out.read_text() == "old"
    _only(tmp_path, "atoms.json")


def test_save_atoms_writes_a_bundle_with_distinct_keys(tmp_path):
    out = tmp_path / "atoms.json"

    save_atoms([], [], out, bundle={"a": 1, "b": {"a": 2}})

    assert json.loads(out.read_text())["bundle"] == {"a": 1, "b": {"a": 2}}
    assert out.read_text().startswith('{\n  "extractor": ')


@pytest.mark.parametrize("keys", _COLLISIONS, ids=_IDS)
def test_save_result_refuses_equal_keys_as_an_input_error_and_keeps_the_file(tmp_path, keys):
    out = tmp_path / "routing.json"
    out.write_text("old")

    with pytest.raises(InputError, match=_MATCH):
        save_result({"result": {"own_rate": keys}}, out, {})

    assert out.read_text() == "old"
    _only(tmp_path, "routing.json")


def test_save_result_writes_a_result_with_distinct_keys_as_before(tmp_path):
    out = tmp_path / "routing.json"
    payload = {"result": {"own_rate": {"a": 1, "b": {"a": 2}}}}

    assert save_result(payload, out, {}) == out

    assert out.read_text() == json.dumps(payload, indent=2)


def test_save_result_refuses_equal_keys_before_it_picks_a_partial_sidecar(tmp_path):
    out = tmp_path / "routing.json"
    out.write_text(json.dumps({"finished": True}))

    with pytest.raises(InputError, match=_MATCH):
        save_result({"result": {1: "a", "1": "b"}}, out, {"partial": {"calls_done": 1, "calls_planned": 2, "message": "m"}})

    _only(tmp_path, "routing.json")


@pytest.mark.parametrize("keys", _COLLISIONS, ids=_IDS)
def test_write_items_exits_2_on_equal_keys_and_keeps_the_file(tmp_path, capsys, keys):
    out = tmp_path / "items.json"
    out.write_text("old")

    with pytest.raises(SystemExit) as stop:
        _write_items(str(out), [{"parameters": keys}])

    assert stop.value.code == 2
    assert "cannot write --items-out" in capsys.readouterr().err
    assert out.read_text() == "old"


def test_write_items_writes_the_items_for_distinct_keys(tmp_path):
    out = tmp_path / "items.json"

    _write_items(str(out), [{"parameters": {"a": 1, "b": 2}}])

    assert out.read_bytes() == b'[\n {\n  "parameters": {\n   "a": 1,\n   "b": 2\n  }\n }\n]'


def _items_out(tmp_path, monkeypatch, sources):
    """Run ``skill novelty items --out`` on a canned analysis; returns the analysis and the file it is saved to."""
    monkeypatch.setattr("dokimi.novelty.items.load_probe_cells", lambda probes: ([], []))
    unmeasured = {"measured": False, "reason": "no cells"}
    analysis = {"sources": sources, "kind_agreement": {}, "discrimination": {"lenient": unmeasured, "strict": unmeasured}}
    monkeypatch.setattr("dokimi.novelty.items.item_analysis", lambda cells, arm: analysis)
    out = tmp_path / "sub" / "deeper" / "items.json"
    cmd_novelty_items(argparse.Namespace(probes=[str(tmp_path / "p.json")], arm="with", out=str(out)))
    return analysis, out


@pytest.mark.parametrize("keys", _COLLISIONS, ids=_IDS)
def test_novelty_items_out_exits_2_on_equal_keys_and_writes_nothing(tmp_path, monkeypatch, keys):
    with pytest.raises(InputError, match=_MATCH):
        _items_out(tmp_path, monkeypatch, keys)

    assert list(tmp_path.iterdir()) == []


def test_novelty_items_out_writes_an_analysis_with_distinct_keys_as_before(tmp_path, monkeypatch):
    analysis, out = _items_out(tmp_path, monkeypatch, {"a": 1})

    assert out.read_text() == json.dumps(analysis, indent=2)

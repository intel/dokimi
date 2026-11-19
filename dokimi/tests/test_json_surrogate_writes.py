# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A JSON file of a replay or graph task holds a loaded string with a lone surrogate: it is written, with a ``?``.

A ``\\ud800`` escape in a review comment of the gold or in a task field loads as a lone surrogate, which a strict
UTF-8 encode refuses (https://github.com/intel/dokimi/issues/913).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from dokimi.atomic_write import write_json_atomic
from dokimi.repos import graph_tasks
from dokimi.repos.replay import authoring, build
from tests.test_eval_tasks_generate_graph import TREE, _callers_docs, _index, _opts

LONE = "n\ud800"
PAIR = chr(0xD83D) + chr(0xDE00)  # two code points, as a JSON escape of U+1F600 loads
JOINED = chr(0x1F600)
CASES = [pytest.param(LONE, "n?", id="lone"), pytest.param(PAIR, JOINED, id="pair"),
         pytest.param("é", "é", id="non-ascii")]


def test_json_is_indented_keeps_non_ascii_and_ends_in_one_newline(tmp_path):
    path = tmp_path / "x.json"
    write_json_atomic(path, {"a": ["é", 1], "b": {}})
    expected = b'{\n  "a": [\n    "\xc3\xa9",\n    1\n  ],\n  "b": {}\n}\n'
    assert path.read_bytes() == expected.replace(b"\n", os.linesep.encode("ascii"))


def test_json_ends_each_line_with_the_platform_line_separator(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    monkeypatch.setattr(os, "linesep", "\r\n")
    write_json_atomic(path, {"a": [1], "b": "x\ny"})
    assert path.read_bytes() == b'{\r\n  "a": [\r\n    1\r\n  ],\r\n  "b": "x\\ny"\r\n}\r\n'


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_json_writes_a_surrogate_as_its_character_or_a_question_mark(tmp_path, value, expected):
    path = tmp_path / "x.json"
    write_json_atomic(path, [{"body": value}])
    assert json.loads(path.read_bytes().decode("utf-8")) == [{"body": expected}]


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_replay_build_writes_a_gold_comment_with_a_surrogate(tmp_path, value, expected):
    build._write(tmp_path, {"a": 1}, [{"body": value}], b"x")
    assert json.loads((tmp_path / build.GOLD).read_text(encoding="utf-8")) == [{"body": expected}]
    assert json.loads((tmp_path / "task.json").read_text(encoding="utf-8")) == {"a": 1}


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_replay_build_writes_a_task_field_with_a_surrogate(tmp_path, value, expected):
    build._write(tmp_path, {"a": value}, [], b"x")
    assert json.loads((tmp_path / "task.json").read_text(encoding="utf-8")) == {"a": expected}


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_replay_authoring_writes_a_task_field_with_a_surrogate(tmp_path, value, expected):
    authoring._write(tmp_path, {"a": value}, {})
    assert json.loads((tmp_path / "task.json").read_text(encoding="utf-8")) == {"a": expected}


@pytest.mark.parametrize(("suffix", "expected"), [(LONE, "n?"), (PAIR, JOINED)])
def test_graph_tasks_write_a_task_and_gold_of_a_symbol_with_a_surrogate(tmp_path: Path, suffix, expected):
    opts = _opts(tmp_path)
    p = graph_tasks.plan(_index(_callers_docs()), TREE, opts)
    (outcome,) = p.outcomes
    assert outcome.status == "written"
    outcome.candidate = replace(outcome.candidate, name="run" + suffix, gold=("src/api" + suffix + ".py", "src/cli.py"))
    graph_tasks.write(p, opts, {"tool": "scip-python 0.6.8", "sha256": "0" * 64})
    out = opts.out / outcome.task
    task = json.loads((out / "task.json").read_text(encoding="utf-8"))
    gold = json.loads((out / graph_tasks.GOLD).read_text(encoding="utf-8"))
    assert f"run{expected}" in task["prompt"]
    assert gold["gold"] == [f"src/api{expected}.py", "src/cli.py"]

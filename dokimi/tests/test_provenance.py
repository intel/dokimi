# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The optional ``version`` and ``provenance`` of tasks and question-set items (dokimi.provenance).

Absent is unknown, never an error. A malformed value is refused where the artifact is checked (task.json in
the repo check, questions.v2 by its schema) and warned about in a terminal-bench task.toml, which may already
carry the key. The repo report shows the status of each task (docs/repo-evaluation-contract.md §3).
"""

from __future__ import annotations

import json

import pytest

from dokimi import provenance
from dokimi._version import __version__
from dokimi.artifacts import schema_errors
from dokimi.cli import build_parser
from dokimi.harnesses.tasks import load_task
from dokimi.repos.manifest import _provenance_problems

GOOD_BLOCKS = [
    {"source": "hand-written"},
    {"source": "generated:dokimi-tasks-generate@0.2.0", "status": "candidate"},
    {"source": "imported:inspect-ai", "reviewed_by": "alice", "status": "frozen"},
    {"status": "validated"},
]
BAD_BLOCKS = [
    "validated",
    {"sauce": "hand-written"},
    {"source": "made-by-hand"},
    {"source": "generated:dokimi-tasks-generate"},
    {"source": "hand-written\n"},
    {"status": "done"},
    {"reviewed_by": "   "},
    {"status": None},
]


@pytest.mark.parametrize("record", [{}, {"provenance": {}}, {"provenance": None}, {"provenance": "validated"}])
def test_absent_or_unusable_provenance_reads_as_unknown(record):
    assert provenance.status_of(record) == provenance.UNKNOWN


@pytest.mark.parametrize("status", provenance.STATUSES)
def test_each_status_reads_back(status):
    assert provenance.status_of({"provenance": {"status": status}}) == status


def test_an_unknown_status_value_reads_as_unknown():
    assert provenance.status_of({"provenance": {"status": "done"}}) == provenance.UNKNOWN


@pytest.mark.parametrize("block", GOOD_BLOCKS)
def test_a_well_formed_block_parses_to_itself(block):
    assert provenance.parse_provenance(block) == block


@pytest.mark.parametrize("block", BAD_BLOCKS)
def test_a_malformed_block_is_refused(block):
    with pytest.raises(provenance.ProvenanceError):
        provenance.parse_provenance(block)


def test_a_block_with_a_non_string_key_is_refused_not_a_type_error():
    with pytest.raises(provenance.ProvenanceError):
        provenance.parse_provenance({1: "legacy", "extra": "value"})


def test_status_of_an_invalid_block_reads_as_unknown_even_with_a_valid_status():
    record = {"provenance": {"source": "made-by-hand", "status": "validated"}}
    assert provenance.status_of(record) == provenance.UNKNOWN


@pytest.mark.parametrize("value", ["2", "2.1", 3, 0])
def test_a_version_is_a_non_empty_string_or_an_integer(value):
    assert provenance.parse_version(value) == value


@pytest.mark.parametrize("value", ["", "  ", True, 1.5, []])
def test_a_malformed_version_is_refused(value):
    with pytest.raises(provenance.ProvenanceError):
        provenance.parse_version(value)


def test_a_generator_stamp_names_the_tool_and_the_dokimi_version():
    stamp = provenance.generated_by("tasks-generate")
    assert stamp == {"source": f"generated:dokimi-tasks-generate@{__version__}"}
    assert provenance.parse_provenance(stamp) == stamp


@pytest.mark.parametrize("block", GOOD_BLOCKS)
def test_questions_schema_accepts_what_the_parser_accepts(block):
    item = {"id": "q1", "question": "How?", "provenance": block}
    assert schema_errors("questions.v2", {"questions": [item]}) == []


@pytest.mark.parametrize("block", BAD_BLOCKS)
def test_questions_schema_refuses_what_the_parser_refuses(block):
    item = {"id": "q1", "question": "How?", "provenance": block}
    assert schema_errors("questions.v2", {"questions": [item]})


@pytest.mark.parametrize("value", ["2", 3])
def test_questions_schema_accepts_a_version(value):
    assert schema_errors("questions.v2", {"questions": [{"id": "q1", "question": "How?", "version": value}]}) == []


@pytest.mark.parametrize("value", ["  ", True, None])
def test_questions_schema_refuses_a_malformed_version(value):
    assert schema_errors("questions.v2", {"questions": [{"id": "q1", "question": "How?", "version": value}]})


@pytest.mark.parametrize("block", GOOD_BLOCKS)
def test_task_json_with_a_good_block_has_no_problem(block):
    assert _provenance_problems({"provenance": block, "version": "1"}) == []


def test_task_json_without_the_keys_has_no_problem():
    assert _provenance_problems({"id": "t"}) == []


@pytest.mark.parametrize("record", [{"version": None}, {"provenance": None}])
def test_task_json_with_a_null_key_is_refused(record):
    assert _provenance_problems(record)


def test_task_json_with_a_bad_status_names_the_key():
    (problem,) = _provenance_problems({"provenance": {"status": "done"}})
    assert "provenance.status" in problem


def test_a_bad_task_toml_metadata_warns_and_still_loads(tmp_path):
    task = tmp_path / "t1"
    task.mkdir()
    (task / "task.toml").write_text('version = "1.0"\n\n[metadata]\nprovenance = { status = "done" }\n')
    (task / "instruction.md").write_text("Do it.\n")
    with pytest.warns(UserWarning, match="metadata provenance.status must be one of"):
        spec = load_task(task)
    assert spec.metadata["provenance"] == {"status": "done"}


def _cell(tmp_path, name: str, files: dict[str, object]) -> None:
    for rel, content in files.items():
        path = tmp_path / "b1" / name / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding="utf-8")


_RESULT = {"grade": {"status": "ok", "score_only": False, "success": True}, "arm": "a", "model": "m",
           "agent": {"timed_out": False, "is_error": False, "over_budget": False, "cost_usd": 0.1, "num_turns": 2}}


def test_repo_report_shows_each_tasks_status_and_unknown_without_one(tmp_path, capsys):
    _cell(tmp_path, "t1__a__m__r1", {"result.json": {**_RESULT, "task": "t1"},
                                     "task.json": {"id": "t1", "provenance": {"status": "validated"}}})
    _cell(tmp_path, "t2__a__m__r1", {"result.json": {**_RESULT, "task": "t2"}})
    parser = build_parser()
    args = parser.parse_args(["repo", "report", str(tmp_path / "b1"), "--json"])
    args.func(args)
    rows = {r["task"]: r["status"] for r in json.loads(capsys.readouterr().out)["data"]["rows"]}
    assert rows == {"t1": "validated", "t2": provenance.UNKNOWN}


def test_a_corrupt_task_copy_reads_as_unknown_and_keeps_the_report(tmp_path, capsys):
    _cell(tmp_path, "t3__a__m__r1", {"result.json": {**_RESULT, "task": "t3"}, "task.json": "not an object"})
    parser = build_parser()
    args = parser.parse_args(["repo", "report", str(tmp_path / "b1"), "--json"])
    args.func(args)
    rows = {r["task"]: r["status"] for r in json.loads(capsys.readouterr().out)["data"]["rows"]}
    assert rows == {"t3": provenance.UNKNOWN}

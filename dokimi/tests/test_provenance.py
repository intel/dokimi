# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The optional ``version`` and ``provenance`` of tasks and question-set items (dokimi.provenance).

Absent is unknown, never an error. A malformed value is refused where the artifact is checked (task.json in
the repo check) and warned about where it is read (a terminal-bench task.toml, which may already carry the key,
and a questions.v2 item, whose schema accepts any value so that old files keep loading). The repo report shows
the status of each task as the cell's result.json recorded it (docs/repo-evaluation-contract.md §3).
"""

from __future__ import annotations

import json
import logging

import pytest

from dokimi import provenance
from dokimi._version import __version__
from dokimi.artifacts import schema_errors
from dokimi.cli import build_parser
from dokimi.harnesses.tasks import load_task
from dokimi.questions.artifact import load_questions
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
def test_questions_schema_accepts_a_malformed_provenance_the_parser_warns_about(block):
    item = {"id": "q1", "question": "How?", "provenance": block}
    assert schema_errors("questions.v2", {"questions": [item]}) == []


@pytest.mark.parametrize("value", ["2", 3, "  ", True, 1.5, None])
def test_questions_schema_accepts_any_version_so_old_files_still_load(value):
    assert schema_errors("questions.v2", {"questions": [{"id": "q1", "question": "How?", "version": value}]}) == []


@pytest.mark.parametrize(("version", "block"), [(1.5, "hand-written"), ("2", {"status": "done"}), (True, None)])
def test_a_malformed_question_version_or_provenance_warns_and_the_set_still_loads(tmp_path, caplog, version, block):
    path = tmp_path / "questions.json"
    question = {"id": "q1", "question": "How?", "version": version, "provenance": block}
    path.write_text(json.dumps({"schema_version": "questions.v2", "questions": [question]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="dokimi.questions.artifact"):
        loaded = load_questions(path)
    assert loaded.artifact["questions"][0]["version"] == version
    assert loaded.artifact["questions"][0]["provenance"] == block
    assert "q1" in caplog.text


def test_a_well_formed_question_version_and_provenance_do_not_warn(tmp_path, caplog):
    path = tmp_path / "questions.json"
    question = {"id": "q1", "question": "How?", "version": "2.1", "provenance": {"source": "hand-written"}}
    path.write_text(json.dumps({"schema_version": "questions.v2", "questions": [question]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="dokimi.questions.artifact"):
        load_questions(path)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


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
    _cell(tmp_path, "t1__a__m__r1", {"result.json": {**_RESULT, "task": "t1", "task_status": "validated"}})
    _cell(tmp_path, "t2__a__m__r1", {"result.json": {**_RESULT, "task": "t2"}})
    parser = build_parser()
    args = parser.parse_args(["repo", "report", str(tmp_path / "b1"), "--json"])
    args.func(args)
    rows = {r["task"]: r["status"] for r in json.loads(capsys.readouterr().out)["data"]["rows"]}
    assert rows == {"t1": "validated", "t2": provenance.UNKNOWN}


def test_a_task_copy_the_grader_rewrote_does_not_change_the_status(tmp_path, capsys):
    # the run dir is read-write for the grader, so its task.json copy is not evidence of the status
    _cell(tmp_path, "t4__a__m__r1", {"result.json": {**_RESULT, "task": "t4", "task_status": "candidate"},
                                     "task.json": {"id": "t4", "provenance": {"status": "frozen"}}})
    parser = build_parser()
    args = parser.parse_args(["repo", "report", str(tmp_path / "b1"), "--json"])
    args.func(args)
    rows = {r["task"]: r["status"] for r in json.loads(capsys.readouterr().out)["data"]["rows"]}
    assert rows == {"t4": "candidate"}

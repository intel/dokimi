# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo report --hits` (each credited replay pair) and the parse-error count of `repo report`, over hand-written
batches graded by the real ``dokimi:review_replay`` grader."""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.graders import main, review
from dokimi.repos.report import format_hits, format_report, load_hits, summarize
from tests.test_repo_builtin_graders import GOLD, _f, fence
from tests.test_repo_compare import _cell

REPLAY = "dokimi:review_replay"


def _graded(tmp: Path, answer: str, *, old: bool = False, grader: str = REPLAY) -> dict:
    """``grade.json`` as the entry script writes it for ``answer`` against ``GOLD``; ``old``: as graded before
    ``details.matches`` existed."""
    (tmp / "gold").mkdir(exist_ok=True)
    (tmp / "gold" / "gold.json").write_text(json.dumps(GOLD))
    metrics, details = main.split_result(review.grade_replay({}, tmp / "gold", answer))
    if old:
        details.pop("matches")
    return {"schema": "repo_grade.v1", "grader": grader, "pass": None, "metrics": metrics,
            "details": {**details, "grader_version": review.VERSION}, "errors": []}


def _put(batch: Path, task: str, arm: str, rep: int, grade: dict) -> None:
    _cell(batch, task, arm, rep, score=grade["metrics"].get("recall") or 0.0)
    (batch / f"{task}__{arm.replace(':', '-')}__m-1__r{rep}" / "grade.json").write_text(json.dumps(grade))


def _batch(tmp: Path) -> Path:
    batch = tmp / "b1"
    todo = _f(6, message="The TODO is repeated five\ntimes and has no issue URL")
    _put(batch, "pr3098-t2", "guidance:none", 1, _graded(tmp, fence([todo])))
    _put(batch, "pr3098-t2", "guidance:none", 2, _graded(tmp, fence([_f(4, message="x" * 150), _f(5, message="mul adds")])))
    _put(batch, "pr3098-t2", "guidance:raw", 1, _graded(tmp, fence([_f(5)]), old=True))
    # the parse error the dpnp run hit: a "line" JSON cannot read, so the whole fence is unreadable
    unparsed = '```json\n[{"file": "dpnp/dpnp_iface_indexing.py", "line": 1:1, "message": "m"}]\n```'
    _put(batch, "pr3014-t1", "guidance:none", 2, _graded(tmp, unparsed))
    _put(batch, "seeds", "guidance:none", 1, _graded(tmp, fence([_f(5)]), grader="dokimi:review_seeds"))
    return batch


def test_hits_list_each_credited_pair_by_task_arm_and_rep(tmp_path):
    batch = _batch(tmp_path)
    # by directory name r10 sorts before r2; the hits follow rep order
    _put(batch, "pr3098-t2", "guidance:none", 10, _graded(tmp_path, fence([_f(5)])))
    found = load_hits(batch)
    assert (found["replay_cells"], found["without_matches"]) == (5, 1)  # the parse error has no hit to miss
    assert [(h["task"], h["arm"], h["rep"], h["thread"], h["finding"], h["distance"]) for h in found["hits"]] == [
        ("pr3098-t2", "guidance:none", 1, "t_op", 0, 1),
        ("pr3098-t2", "guidance:none", 2, "t_doc", 0, 0), ("pr3098-t2", "guidance:none", 2, "t_op", 1, 0),
        ("pr3098-t2", "guidance:none", 10, "t_op", 0, 0)]
    assert found["hits"][0] == {
        "task": "pr3098-t2", "arm": "guidance:none", "model": "m-1", "rep": 1, "cell": "pr3098-t2__guidance-none__m-1__r1",
        "thread": "t_op", "thread_file": "src/calc.py", "thread_line": 5,
        "comment": "mul adds instead of multiplying\nsecond line", "finding": 0, "file": "src/calc.py", "line": 6,
        "severity": "minor", "message": "The TODO is repeated five\ntimes and has no issue URL", "distance": 1}
    assert format_hits(found).splitlines() == [
        f"{batch}: 4 credited hit(s) in 5 {REPLAY} cell(s)",
        "pr3098-t2",
        "  guidance:none  m-1  r1",
        "    t_op src/calc.py:5  mul adds instead of multiplying second line",
        "      finding 0 src/calc.py:6 minor, 1 line(s) away: The TODO is repeated five times and has no issue URL",
        "  guidance:none  m-1  r2",
        "    t_doc src/calc.py:4  docstring?",
        "      finding 0 src/calc.py:4 minor, 0 line(s) away: " + "x" * 97 + "...",
        "    t_op src/calc.py:5  mul adds instead of multiplying second line",
        "      finding 1 src/calc.py:5 minor, 0 line(s) away: mul adds",
        "  guidance:none  m-1  r10",
        "    t_op src/calc.py:5  mul adds instead of multiplying second line",
        "      finding 0 src/calc.py:5 minor, 0 line(s) away: ",
        "1 cell(s) graded without hit details"]


def _cli(capsys, *argv: str) -> str:
    args = build_parser().parse_args(["repo", "report", *argv])
    args.func(args)
    return capsys.readouterr().out


def test_report_hits_flag_prints_the_pairs_and_the_default_report_stays_the_table(tmp_path, capsys):
    batch = _batch(tmp_path)
    text = _cli(capsys, str(batch), "--hits")
    assert text.startswith(f"{batch}: 3 credited hit(s)") and "finding 0 src/calc.py:6" in text
    records = json.loads(_cli(capsys, str(batch), "--hits", "--json"))
    assert [(h["cell"], h["thread"]) for h in records["hits"]][:2] == [
        ("pr3098-t2__guidance-none__m-1__r1", "t_op"), ("pr3098-t2__guidance-none__m-1__r2", "t_doc")]
    assert records["without_matches"] == 1
    table = _cli(capsys, str(batch))
    assert table.startswith(f"{batch}: 5 finished cells") and "credited" not in table and "finding 0" not in table


def test_a_match_key_named_like_the_cell_key_does_not_move_the_hit(tmp_path, capsys):
    batch = tmp_path / "b1"
    grade = _graded(tmp_path, fence([_f(5)]))
    grade["details"]["matches"][0].update(task="other-task", arm="guidance:raw", model="m-9", rep=99, cell="elsewhere")
    _put(batch, "a", "guidance:none", 1, grade)
    records = json.loads(_cli(capsys, str(batch), "--hits", "--json"))
    assert [(h["task"], h["arm"], h["model"], h["rep"], h["cell"], h["thread"]) for h in records["hits"]] == [
        ("a", "guidance:none", "m-1", 1, "a__guidance-none__m-1__r1", "t_op")]
    assert _cli(capsys, str(batch), "--hits").splitlines()[1:3] == ["a", "  guidance:none  m-1  r1"]


def test_hits_on_a_batch_with_no_replay_cell_say_so(tmp_path):
    _cell(tmp_path, "a", "guidance:none", 1)
    found = load_hits(tmp_path)
    assert found["hits"] == [] and found["replay_cells"] == 0
    assert format_hits(found) == f"{tmp_path}: no {REPLAY} cell"


def test_report_counts_answers_that_did_not_parse_per_row(tmp_path):
    batch = _batch(tmp_path)
    rows = {(r["task"], r["arm"]): r for r in summarize(batch)["rows"]}
    unparsed = rows[("pr3014-t1", "guidance:none")]
    assert unparsed["parse_errors"] == 1 and unparsed["grader_failures"] == 0  # the agent failed, not the grader
    assert [r["parse_errors"] for k, r in rows.items() if k[0] != "pr3014-t1"] == [0, 0, 0]
    lines = format_report(summarize(batch)).splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("pr3014-t1"))
    assert lines[at + 1].strip() == "1 agent answer(s) whose findings JSON did not parse (scored 0)"  # in the mean
    assert sum("did not parse" in line for line in lines) == 1


def test_the_parse_error_note_of_a_gate_row_says_counted_as_failed(tmp_path):
    _cell(tmp_path, "gate", "guidance:none", 1, success=False, metrics={"parse_error": True})
    _cell(tmp_path, "gate", "guidance:none", 2)
    lines = format_report(summarize(tmp_path)).splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("gate"))
    assert "1/2 pass" in lines[at]
    assert lines[at + 1].strip() == "1 agent answer(s) whose findings JSON did not parse (counted as failed)"


def test_hits_and_report_skip_unreadable_grades_and_list_a_match_without_text(tmp_path):
    batch = tmp_path / "b1"
    bad = {1: "{not json", 2: "[]",
           3: json.dumps({"grader": REPLAY, "metrics": {"parse_error": "yes"}, "details": []}),
           4: json.dumps({"grader": REPLAY, "metrics": [], "details": {"threads_hit": ["t"],
                          "matches": [None, "t", {"thread": "t", "finding": 0, "distance": 0}]}})}
    for rep, text in bad.items():
        _cell(batch, "a", "guidance:none", rep)
        (batch / f"a__guidance-none__m-1__r{rep}" / "grade.json").write_text(text)
    found = load_hits(batch)
    assert (found["replay_cells"], found["without_matches"]) == (2, 0)  # 1 and 2 name no grader
    assert [(h["rep"], h["thread"]) for h in found["hits"]] == [(4, "t")]
    assert format_hits(found).splitlines()[-2:] == [
        "    t None:None  ", "      finding 0 None:None None, 0 line(s) away: "]
    assert [r["parse_errors"] for r in summarize(batch)["rows"]] == [0]  # a non-boolean parse_error is not one


def test_hits_refuse_a_cell_with_no_rep_as_the_table_refuses_a_malformed_cell(tmp_path, capsys):
    batch = _batch(tmp_path)
    path = batch / "pr3098-t2__guidance-none__m-1__r1" / "result.json"
    rec = json.loads(path.read_text())
    del rec["rep"]  # the table never reads it; --hits sorts by it
    path.write_text(json.dumps(rec))
    with pytest.raises(SystemExit) as stopped:
        _cli(capsys, str(batch), "--hits")
    assert stopped.value.code == 2  # an input refusal exits 2 (the exit-code audit)
    assert capsys.readouterr().err.strip() == f"✗ {path} has no usable `rep`"


def test_a_grade_nested_too_deep_or_linked_out_of_the_batch_is_no_grade(tmp_path):
    batch = tmp_path / "b1"
    outside = tmp_path / "outside.json"  # a link out of the batch is not followed
    outside.write_text(json.dumps({"grader": REPLAY, "metrics": {"parse_error": True},
                                   "details": {"matches": [{"thread": "outside", "finding": 0}]}}))
    _put(batch, "a", "guidance:none", 1, _graded(tmp_path, fence([_f(5)])))
    _cell(batch, "a", "guidance:none", 2, score=0.0)
    _cell(batch, "a", "guidance:none", 3, score=0.0)
    (batch / "a__guidance-none__m-1__r2" / "grade.json").write_text("[" * 200000)  # RecursionError to parse
    link = batch / "a__guidance-none__m-1__r3" / "grade.json"
    link.unlink()
    link.symlink_to(outside)
    assert summarize(batch)["rows"][0]["parse_errors"] == 0
    found = load_hits(batch)
    assert (found["replay_cells"], [h["rep"] for h in found["hits"]]) == (1, [1])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_a_grade_that_is_a_fifo_is_no_grade_and_does_not_block_the_report(tmp_path):
    batch = tmp_path / "b1"
    _put(batch, "a", "guidance:none", 1, _graded(tmp_path, fence([_f(5)])))
    _cell(batch, "a", "guidance:none", 2, score=0.0)
    fifo = batch / "a__guidance-none__m-1__r2" / "grade.json"
    fifo.unlink()
    os.mkfifo(fifo)
    out: dict = {}
    reader = threading.Thread(target=lambda: out.update(summary=summarize(batch), hits=load_hits(batch)),
                              daemon=True)  # a read of a FIFO waits for a writer: the thread is kept apart
    reader.start()
    reader.join(10)
    blocked = reader.is_alive()
    for _ in range(30):  # open the write end so each blocked read ends (the table's, then the one of --hits)
        if not reader.is_alive():
            break
        with contextlib.suppress(OSError):  # ENXIO: no read of the FIFO is waiting right now
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(1)
    assert not blocked, "a grade.json that is a FIFO blocked the report"
    assert out["summary"]["rows"][0]["parse_errors"] == 0
    assert (out["hits"]["replay_cells"], [h["rep"] for h in out["hits"]["hits"]]) == (1, [1])


def test_hits_credit_no_match_of_a_cell_the_table_scores_as_a_grader_failure(tmp_path):
    batch = tmp_path / "b1"  # written, then the run failed: the table counts the cell as a grader failure
    _cell(batch, "a", "guidance:none", 1, failure=True)
    (batch / "a__guidance-none__m-1__r1" / "grade.json").write_text(json.dumps(_graded(tmp_path, fence([_f(5)]))))
    _put(batch, "a", "guidance:none", 2, _graded(tmp_path, fence([_f(5)])))
    assert [r["grader_failures"] for r in summarize(batch)["rows"]] == [1]
    found = load_hits(batch)
    assert (found["replay_cells"], [h["rep"] for h in found["hits"]]) == (2, [2])


def test_parse_errors_leave_out_a_cell_the_table_counts_as_a_grader_failure(tmp_path):
    batch = tmp_path / "b1"
    unparsed = _graded(tmp_path, "no findings JSON here")
    assert unparsed["metrics"]["parse_error"] is True
    _cell(batch, "a", "guidance:none", 1, failure=True, score=0.0)  # the grade was written, then the run failed
    (batch / "a__guidance-none__m-1__r1" / "grade.json").write_text(json.dumps(unparsed))
    _put(batch, "a", "guidance:none", 2, unparsed)
    row = summarize(batch)["rows"][0]
    assert (row["grader_failures"], row["parse_errors"]) == (1, 1)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo report --hits` (each credited replay pair) and the parse-error count of `repo report`, over hand-written
batches graded by the real ``dokimi:review_replay`` grader."""

from __future__ import annotations

import json
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
    found = load_hits(batch)
    assert (found["replay_cells"], found["without_matches"]) == (4, 1)  # the parse error has no hit to miss
    assert [(h["task"], h["arm"], h["rep"], h["thread"], h["finding"], h["distance"]) for h in found["hits"]] == [
        ("pr3098-t2", "guidance:none", 1, "t_op", 0, 1),
        ("pr3098-t2", "guidance:none", 2, "t_doc", 0, 0), ("pr3098-t2", "guidance:none", 2, "t_op", 1, 0)]
    assert found["hits"][0] == {
        "task": "pr3098-t2", "arm": "guidance:none", "model": "m-1", "rep": 1, "cell": "pr3098-t2__guidance-none__m-1__r1",
        "thread": "t_op", "thread_file": "src/calc.py", "thread_line": 5,
        "comment": "mul adds instead of multiplying\nsecond line", "finding": 0, "file": "src/calc.py", "line": 6,
        "severity": "minor", "message": "The TODO is repeated five\ntimes and has no issue URL", "distance": 1}
    assert format_hits(found).splitlines() == [
        f"{batch}: 3 credited hit(s) in 4 {REPLAY} cell(s)",
        "pr3098-t2",
        "  guidance:none  m-1  r1",
        "    t_op src/calc.py:5  mul adds instead of multiplying second line",
        "      finding 0 src/calc.py:6 minor, 1 line(s) away: The TODO is repeated five times and has no issue URL",
        "  guidance:none  m-1  r2",
        "    t_doc src/calc.py:4  docstring?",
        "      finding 0 src/calc.py:4 minor, 0 line(s) away: " + "x" * 97 + "...",
        "    t_op src/calc.py:5  mul adds instead of multiplying second line",
        "      finding 1 src/calc.py:5 minor, 0 line(s) away: mul adds",
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
    assert lines[at + 1].strip() == "1 agent answer(s) whose findings JSON did not parse (counted as failed)"
    assert sum("did not parse" in line for line in lines) == 1


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
    assert stopped.value.code == 1
    assert capsys.readouterr().err.strip() == f"✗ {path} has no usable `rep`"

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare --metric judge:recall_judged`: judged recall read from each cell's hits_judged.json. No LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi import caching, llm
from dokimi.artifacts import save_artifact
from dokimi.cli import build_parser
from dokimi.repos.compare import CompareError, compare, format_compare
from dokimi.repos.hits_judge import cell_doc
from dokimi.repos.report import REPLAY
from tests import test_repo_hits_judge as hits
from tests.test_repo_compare import _cell, _row
from tests.cli_refusal import refusal

JUDGED = "metric:judge:recall_judged"
NONE, RAW = "guidance:none", "guidance:raw"


def _name(task: str, arm: str, rep: int, model: str = "m-1") -> str:
    return f"{task}__{arm.replace(':', '-')}__{model}__r{rep}"


def _judged(batch: Path, task: str, arm: str, rep: int, same: int, *, recall: float = 1.0,
            unjudged: int = 0, write: bool = True, grade: dict | None = None,
            credited: list[tuple[int, int]] | None = None, model: str = "m-1", threads: int = 4,
            judge: tuple[str, str] = ("openai", "judge-1")) -> Path:
    """A replay cell graded with ``recall`` over 4 threads and, with ``write``, its hits_judged.json from the
    real writer: ``same`` pairs labelled same point and ``unjudged`` pairs with no label. ``credited`` is the
(thread, finding) pairs grade.json credits now (default: the judged ones)."""
    _cell(batch, task, arm, rep, metrics={"recall": recall, "n_threads": threads, **(grade or {})}, model=model)
    rd = batch / _name(task, arm, rep, model)
    pairs = ([{"thread": i, "finding": i, "label": "same point", "reason": "same"} for i in range(same)]
             + [{"thread": 10 + i, "finding": 10 + i, "label": None, "reason": None, "error": "judge call failed"}
                for i in range(unjudged)])
    now = [(p["thread"], p["finding"]) for p in pairs] if credited is None else credited
    doc = json.loads((rd / "grade.json").read_text())
    doc["details"], doc["grader"] = {"matches": [{"thread": t, "finding": f} for t, f in now]}, REPLAY
    (rd / "grade.json").write_text(json.dumps(doc))
    if write:
        rec = {"cell": rd.name, "task": task, "arm": arm, "model": model, "rep": rep, "n_threads": threads,
               "recall": recall, "parse_error": False}
        save_artifact("repo_hits_judged", cell_doc(rec, judge, pairs), rd / "hits_judged.json")
    return rd


def test_judged_recall_is_compared_next_to_recall_per_task(tmp_path):
    b = tmp_path / "b1"
    # (task, arm, rep, same point pairs of 4 threads, recall)
    for task, arm, rep, same, recall in [("t1", NONE, 1, 1, 0.5), ("t1", NONE, 2, 1, 0.5), ("t1", RAW, 1, 2, 0.75),
                                         ("t1", RAW, 2, 3, 0.75), ("t2", NONE, 1, 2, 0.5), ("t2", RAW, 1, 2, 1.0),
                                         ("t3", NONE, 1, 0, 0.25), ("t3", RAW, 1, 1, 0.25)]:
        _judged(b, task, arm, rep, same, recall=recall)
    s = compare([b], NONE, metrics=("recall", "judge:recall_judged"))
    judged = _row(s, JUDGED)
    # t1: 0.25 -> (0.5 + 0.75) / 2 = 0.625; t2: 0.5 -> 0.5; t3: 0 -> 0.25
    assert [(t["task"], t["baseline"], t["treatment"], t["n_baseline"], t["n_treatment"]) for t in judged["tasks"]] \
        == [("t1", 0.25, 0.625, 2, 2), ("t2", 0.5, 0.5, 1, 1), ("t3", 0.0, 0.25, 1, 1)]
    assert judged["n_tasks"] == 3 and judged["delta"]["mean"] == round((0.375 + 0 + 0.25) / 3, 4)  # as printed
    assert (judged["up"], judged["down"], judged["tied"]) == (2, 0, 1)
    recall = _row(s, "metric:recall")  # grade.json's recall, read as before
    assert [(t["baseline"], t["treatment"]) for t in recall["tasks"]] == [(0.5, 0.75), (0.5, 1.0), (0.25, 0.25)]
    assert s["judged_cells"] == {"metrics": ["judge:recall_judged"], "arms": {
        arm: {"cells": n, "read": n, "no_file": 0, "not_judged": 0, "stale": 0, "other_cell": 0, "no_threads": 0, "unjudged": 0, "unreadable": 0}
        for arm, n in ((NONE, 4), (RAW, 4))}, "judges": {NONE: ["openai/judge-1"], RAW: ["openai/judge-1"]}}
    assert not [w for w in s["warnings"] if "judges" in w]  # one judge throughout
    text = format_compare(s).splitlines()
    assert not [line for line in text if "cells read" in line]  # every cell read: no line per arm
    assert [line for line in text if line.startswith("note: ")] == [
        "note: judge:recall_judged is a judge's labels of the credited pairs, at most each read cell's recall "
        "(recall's row also keeps the cells an arm line above leaves out); it is uncalibrated "
        "unless repo hits judge --labels reported agreement for these arms (contract §9)"]


def test_credited_pairs_that_changed_with_the_same_recall_make_the_file_stale(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    _judged(b, "t1", RAW, 1, 1)  # judged (0, 0); grade.json credits (0, 0) still
    _judged(b, "t1", RAW, 2, 1, credited=[(1, 1)])  # regraded: another thread and finding, recall still the same
    _judged(b, "t1", RAW, 3, 1, grade={"n_threads": 5})  # the task's gold changed size
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    assert s["judged_cells"]["arms"][RAW] == {"cells": 3, "read": 1, "no_file": 0, "not_judged": 0, "stale": 2, "other_cell": 0,
                                             "no_threads": 0, "unjudged": 0, "unreadable": 0}
    assert _row(s, JUDGED)["tasks"][0]["n_treatment"] == 1


def test_a_regrade_that_rewords_a_credited_thread_makes_the_file_stale(tmp_path, monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    batch = hits._batch(tmp_path)
    args = build_parser().parse_args(["repo", "hits", "judge", str(batch), "--judge", hits.JUDGE])
    with llm.use_adapter(llm.FakeAdapter(respond=hits._reply)):  # the real writer, a scripted judge
        args.func(args)
    gold = batch / "_eval" / "content" / "tasks" / hits.TASK / "gold.json"
    gold.write_text(json.dumps([{**g, "comment": "mul should multiply"} if g["id"] == "t_op" else g
                                for g in hits.GOLD]))
    rd = batch / _name(hits.TASK, NONE, 1, hits.MODEL)
    hits._grade(batch, rd)  # the same (thread, finding) pairs and recall, t_op's comment reworded
    s = compare([batch], NONE, metrics=("judge:recall_judged",))
    assert (s["judged_cells"]["arms"][NONE]["stale"], s["judged_cells"]["arms"][RAW]["read"]) == (1, 1)
    doc = json.loads((rd / "hits_judged.json").read_text())
    doc["pairs"] = [{k: v for k, v in p.items() if k not in ("comment", "message")} for p in doc["pairs"]]
    (rd / "hits_judged.json").write_text(json.dumps(doc))  # written before the pairs recorded the cuts
    assert compare([batch], NONE, metrics=("judge:recall_judged",))["judged_cells"]["arms"][NONE]["read"] == 1


def test_a_cell_with_no_gold_thread_is_counted_as_that_not_as_an_unjudged_pair(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    _judged(b, "t1", RAW, 1, 1)
    _judged(b, "t3", RAW, 1, 0, recall=0.0, threads=0)  # no gold thread: recall_judged null, no pair to judge
    _judged(b, "t1", RAW, 2, 1, unjudged=1)
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    counts = s["judged_cells"]["arms"][RAW]
    assert (counts["read"], counts["no_threads"], counts["unjudged"]) == (1, 1, 1)
    line = next(x for x in format_compare(s).splitlines() if "cells read" in x)
    assert "1 with no gold thread, 1 with an unjudged pair" in line


def test_cells_with_no_gold_thread_alone_get_no_rerun_hint(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    _judged(b, "t1", RAW, 1, 1)
    _judged(b, "t3", RAW, 1, 0, recall=0.0, threads=0)  # a rerun writes the same null
    _judged(b, "t3", NONE, 1, 0, recall=0.0, threads=0)
    _judged(b, "t3", NONE, 2, 0, recall=0.0, threads=0, write=False)  # a rerun writes this one
    lines = [x for x in format_compare(compare([b], NONE, metrics=("judge:recall_judged",))).splitlines()
             if "cells read" in x]
    assert lines == [f"judge:recall_judged, {NONE}: 1 of 3 cells read (1 no hits_judged.json, 1 with no gold "
                     "thread); run repo hits judge again",
                     f"judge:recall_judged, {RAW}: 1 of 2 cells read (1 with no gold thread)"]
    c = tmp_path / "b2"
    for arm in (NONE, RAW):
        _judged(c, "t3", arm, 1, 0, recall=0.0, threads=0)
    with pytest.raises(CompareError) as exc:
        compare([c], NONE, metrics=("judge:recall_judged",))
    assert str(exc.value) == "no finished cell reports --metric judge:recall_judged (2 with no gold thread)"


def test_a_non_object_match_in_the_grade_is_skipped_as_the_judge_skips_it(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    rd = _judged(b, "t1", RAW, 1, 1)
    grade = json.loads((rd / "grade.json").read_text())
    grade["details"]["matches"].append("not an object")  # a hand-edited grade.json
    (rd / "grade.json").write_text(json.dumps(grade))
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    assert s["judged_cells"]["arms"][RAW]["read"] == 1
    assert _row(s, JUDGED)["tasks"][0]["n_treatment"] == 1


def test_a_parse_error_cell_has_no_matches_and_reports_its_measured_0(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    rd = _judged(b, "t1", RAW, 1, 0, recall=0.0, write=False)
    (rd / "grade.json").write_text(json.dumps({"metrics": {"recall": 0.0, "n_threads": 4, "parse_error": True}}))
    rec = {"cell": rd.name, "task": "t1", "arm": RAW, "model": "m-1", "rep": 1, "n_threads": 4, "recall": 0.0,
           "parse_error": True}
    save_artifact("repo_hits_judged", cell_doc(rec, ("openai", "judge-1"), []), rd / "hits_judged.json")
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    task = _row(s, JUDGED)["tasks"][0]
    assert (s["judged_cells"]["arms"][RAW]["read"], task["treatment"], task["n_treatment"]) == (1, 0.0, 1)


@pytest.mark.parametrize("change", ["matches not a list", "a pair fewer in the file"])
def test_judged_pairs_that_do_not_line_up_with_the_grade_are_stale(tmp_path, change):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    if change == "matches not a list":  # load_hits leaves this grade out, so repo hits judge removes its file
        rd = _judged(b, "t1", RAW, 1, 0, recall=0.0)
        grade = json.loads((rd / "grade.json").read_text())
        (rd / "grade.json").write_text(json.dumps({**grade, "details": {"matches": {"0": {"thread": 0}}}}))
    else:  # a hand-edited file, the grade's recall unchanged
        rd = _judged(b, "t1", RAW, 1, 2, recall=0.5)
        doc = json.loads((rd / "hits_judged.json").read_text())
        (rd / "hits_judged.json").write_text(json.dumps({**doc, "pairs": doc["pairs"][:1]}))
    _judged(b, "t1", RAW, 2, 1)
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    assert (s["judged_cells"]["arms"][RAW]["stale"], s["judged_cells"]["arms"][RAW]["read"]) == (1, 1)


def test_a_plain_compare_has_no_judged_cells_and_no_note(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 1)
    _judged(b, "t1", RAW, 1, 2)
    s = compare([b], NONE, metrics=("recall",))
    assert s["judged_cells"] is None and JUDGED not in {r["measure"] for r in s["comparisons"]}
    assert "note: " not in format_compare(s)


def test_a_cell_whose_file_does_not_hold_up_is_left_out_and_counted(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 2)
    good = _judged(b, "t1", RAW, 1, 1)  # 0.25: the one treatment cell read
    _judged(b, "t1", RAW, 2, 4, recall=1.0, grade={"recall": 0.5})  # regraded since judging: stale
    copied = _judged(b, "t1", RAW, 3, 4, write=False)
    (copied / "hits_judged.json").write_text((good / "hits_judged.json").read_text())  # another cell's file
    _judged(b, "t1", RAW, 4, 1, unjudged=1)  # recall_judged null: absent, not 0
    bad = {5: "{not json", 6: None, 7: "[]", 8: "[" * 100_000, 9: b"\xff\xfe"}
    for rep, content in bad.items():
        rd = _judged(b, "t1", RAW, rep, 4)
        if rep == 6:
            doc = json.loads((rd / "hits_judged.json").read_text())
            (rd / "hits_judged.json").write_text(json.dumps({**doc, "schema_version": "repo_hits_judged.v0"}))
        elif isinstance(content, bytes):
            (rd / "hits_judged.json").write_bytes(content)
        else:
            (rd / "hits_judged.json").write_text(content)
    folder = _judged(b, "t1", RAW, 10, 4, write=False)
    (folder / "hits_judged.json").mkdir()
    # no file; a grade.json metric of the same name is not the judge's
    _judged(b, "t1", RAW, 11, 4, write=False, grade={"judge:recall_judged": 0.9})
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    task = _row(s, JUDGED)["tasks"][0]
    assert (task["baseline"], task["treatment"], task["n_baseline"], task["n_treatment"]) == (0.5, 0.25, 1, 1)
    assert s["judged_cells"]["arms"][RAW] == {"cells": 11, "read": 1, "no_file": 1, "not_judged": 0, "stale": 1, "other_cell": 1,
                                             "no_threads": 0, "unjudged": 1, "unreadable": 6}
    assert s["judged_cells"]["arms"][NONE]["read"] == 1
    lines = [line for line in format_compare(s).splitlines() if "cells read" in line]
    assert lines == ["judge:recall_judged, guidance:raw: 1 of 11 cells read (1 no hits_judged.json, 1 stale: "
                     "regraded since judging, 1 not this cell's (cell, task, arm or model differ), 1 with an "
                     "unjudged pair, 6 unreadable or not repo_hits_judged.v1); run repo hits judge again"]


def test_a_task_with_no_reporting_cell_in_one_arm_is_unpaired(tmp_path):
    b = tmp_path / "b1"
    for task in ("t1", "t2"):
        _judged(b, task, NONE, 1, 2)
    _judged(b, "t1", RAW, 1, 3)
    _judged(b, "t2", RAW, 1, 3, recall=1.0, grade={"recall": 0.75})  # its only cell is stale
    _judged(b, "t2", RAW, 2, 3, unjudged=1)  # and its other one has an unjudged pair
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    assert [t["task"] for t in _row(s, JUDGED)["tasks"]] == ["t1"]
    assert s["unpaired"] == [{"treatment": RAW, "model": "m-1", "task": "t2", "measure": JUDGED,
                              "reason": f"no {JUDGED} in treatment"}]
    assert _row(s, "gate")["n_tasks"] == 2  # the other measures keep the task


@pytest.mark.parametrize("name", ["judge:recall", "judge:n_threads", "judge:"])
def test_a_judge_field_other_than_recall_judged_is_refused(tmp_path, capsys, name):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 1)
    _judged(b, "t1", RAW, 1, 2)
    with pytest.raises(CompareError, match=f"--metric {name}: .* the only one accepted is judge:recall_judged$"):
        compare([b], NONE, metrics=("recall", name))
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", NONE, "--metric", name])
    with refusal() as exc:
        args.func(args)
    assert exc.code == 2 and "only one accepted is judge:recall_judged" in capsys.readouterr().err


def test_no_cell_with_a_usable_file_is_refused_with_the_hits_judge_hint(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 1, write=False)
    _judged(b, "t1", RAW, 1, 2, write=False)
    _judged(b, "t1", RAW, 2, 2, recall=1.0, grade={"recall": 0.5})
    with pytest.raises(CompareError) as exc:
        compare([b], NONE, metrics=("recall", "judge:recall_judged"))
    assert str(exc.value) == ("no finished cell reports --metric judge:recall_judged (2 no hits_judged.json, "
                              "1 stale: regraded since judging): run `repo hits judge <batch> --judge "
                              "PROVIDER/MODEL` first")
    with pytest.raises(CompareError, match=r"reports a numeric or boolean --metric typo$"):  # plain wording kept
        compare([b], NONE, metrics=("typo",))


def test_a_file_only_an_excluded_arm_or_model_has_does_not_satisfy_the_metric(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 1, write=False)
    _judged(b, "t1", RAW, 1, 2, write=False)
    _judged(b, "t1", "guidance:other", 1, 2)
    with pytest.raises(CompareError, match=r"no finished cell reports --metric judge:recall_judged \(2 no "):
        compare([b], NONE, treatments=[RAW], metrics=("judge:recall_judged",))
    assert compare([b], NONE, metrics=("judge:recall_judged",))["judged_cells"]["arms"]["guidance:other"]["read"] == 1
    c = tmp_path / "b2"
    for arm in (NONE, RAW):
        _judged(c, "t1", arm, 1, 1, write=False)
        _judged(c, "t1", arm, 1, 1, model="m-2")
    with pytest.raises(CompareError, match=r"no finished cell reports --metric judge:recall_judged"):
        compare([c], NONE, metrics=("judge:recall_judged",), models=["m-1"])


def test_task_narrows_the_cells_read_and_the_refusal(tmp_path):
    b = tmp_path / "b1"
    for arm, same in ((NONE, 1), (RAW, 2)):
        _judged(b, "t1", arm, 1, same)
        _judged(b, "t2", arm, 1, same, write=False)
    with pytest.raises(CompareError, match=r"--metric judge:recall_judged among the --task selection \(2 no "):
        compare([b], NONE, metrics=("judge:recall_judged",), tasks=["t2"])
    s = compare([b], NONE, metrics=("judge:recall_judged",), tasks=["t1"])
    assert {arm: (c["cells"], c["read"]) for arm, c in s["judged_cells"]["arms"].items()} == {NONE: (1, 1),
                                                                                             RAW: (1, 1)}
    assert _row(s, JUDGED)["delta"]["mean"] == pytest.approx(0.25)


def test_cli_compares_judged_recall_as_json(tmp_path, capsys):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 1)
    _judged(b, "t1", RAW, 1, 3)
    _judged(b, "t1", RAW, 2, 3, write=False)
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", NONE, "--metric", "recall",
                                      "--metric", "judge:recall_judged", "--json"])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    rows = {r["measure"]: r for r in out["comparisons"]}
    assert rows[JUDGED]["delta"]["mean"] == pytest.approx(0.5) and rows[JUDGED]["tasks"][0]["n_treatment"] == 1
    assert rows["metric:recall"]["tasks"][0]["n_treatment"] == 2
    assert out["judged_cells"]["arms"][RAW] == {"cells": 2, "read": 1, "no_file": 1, "not_judged": 0, "stale": 0, "other_cell": 0,
                                               "no_threads": 0, "unjudged": 0, "unreadable": 0}


def test_cells_repo_hits_judge_never_judges_get_no_rerun_hint(tmp_path, monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    batch = hits._batch(tmp_path)  # r2: a grader failure (none) and a grade without matches (raw)
    args = build_parser().parse_args(["repo", "hits", "judge", str(batch), "--judge", hits.JUDGE])
    with llm.use_adapter(llm.FakeAdapter(respond=hits._reply)):
        args.func(args)
    s = compare([batch], NONE, metrics=("judge:recall_judged",))
    assert {arm: (c["read"], c["not_judged"], c["no_file"]) for arm, c in s["judged_cells"]["arms"].items()}         == {NONE: (1, 1, 0), RAW: (1, 1, 0)}
    assert [x for x in format_compare(s).splitlines() if "cells read" in x] == [
        f"judge:recall_judged, {arm}: 1 of 2 cells read (1 not judgeable by repo hits judge)" for arm in (NONE, RAW)]
    hits._judged(batch, RAW, 1).unlink()  # a cell it judges, its file gone: a rerun writes it
    lines = [x for x in format_compare(compare([batch], NONE, metrics=("judge:recall_judged",))).splitlines()
             if "cells read" in x]
    assert lines[1] == (f"judge:recall_judged, {RAW}: 0 of 2 cells read (1 no hits_judged.json, 1 not judgeable "
                        "by repo hits judge); run repo hits judge again")


def test_a_selection_of_cells_never_judged_is_refused_without_the_hint(tmp_path):
    b = tmp_path / "b1"
    for arm in (NONE, RAW):
        rd = _judged(b, "t1", arm, 1, 1, write=False)
        grade = json.loads((rd / "grade.json").read_text())
        (rd / "grade.json").write_text(json.dumps({**grade, "grader": "other:grader"}))  # load_hits skips it
    with pytest.raises(CompareError) as exc:
        compare([b], NONE, metrics=("judge:recall_judged",))
    assert str(exc.value) == "no finished cell reports --metric judge:recall_judged (2 not judgeable by repo hits judge)"


def test_cells_read_from_different_judges_are_warned_about(tmp_path):
    b = tmp_path / "b1"
    _judged(b, "t1", NONE, 1, 1)
    _judged(b, "t1", RAW, 1, 2, judge=("anthropic", "judge-2"))
    _judged(b, "t1", RAW, 2, 2, judge=("anthropic", "judge-3"), credited=[(5, 5)])  # stale: its judge is not read
    s = compare([b], NONE, metrics=("judge:recall_judged",))
    assert s["judged_cells"]["judges"] == {NONE: ["openai/judge-1"], RAW: ["anthropic/judge-2"]}
    warning = ("judge:recall_judged: the compared cells were judged by different judges "
               "(anthropic/judge-2, openai/judge-1)")
    assert warning in s["warnings"] and f"warning: {warning}" in format_compare(s).splitlines()

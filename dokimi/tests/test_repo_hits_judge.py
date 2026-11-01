# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo hits judge` and `repo hits sheet` (#291): judged recall over the credited pairs of replay cells graded by
the real ``dokimi:review_replay`` grader, with the judge a scripted fake LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi import caching, llm
from dokimi.artifacts import ArtifactValidationError, load_artifact, validate_artifact
from dokimi.cli import build_parser
from dokimi.metrics.usage import UsageRecord
from dokimi.repos.graders import main, review
from dokimi.repos.hits_calibration import calibrate, cohen_kappa, sheet
from dokimi.repos.hits_judge import HITS_JUDGED, Pair, pair_key, read_pairs
from tests.test_repo_compare import _cell

TASK = "review_replay_pr3098"
MODEL = "claude-sonnet-5-5"
JUDGE = "anthropic/claude-opus-5-5"
LONG = ("This loop is new and has no owner: " + "please " * 40
        + "add a TODO that names the follow-up issue, so it is removed once the API settles.")
GOLD = [{"id": "t_todo", "file": "src/calc.py", "line": 10, "kind": "convention", "comment": LONG, "addressed": True},
        {"id": "t_op", "file": "src/calc.py", "line": 30, "kind": "correctness",
         "comment": "mul adds instead of multiplying", "addressed": True},
        {"id": 7, "file": "src/io.py", "line": 5, "kind": "docs", "comment": "docstring?", "addressed": False}]
#: The judge's reply to a prompt holding each finding's message.
REPLIES = {"The TODO is repeated": "unrelated", "mul adds": "same point", "Add a TODO naming the issue": "same point",
           "read() has no docstring": "related"}


def _f(line, message, file="src/calc.py", **extra):
    return {"file": file, "line": line, "severity": "minor", "rule": "", "message": message, **extra}


def _answer(findings) -> str:
    return "Review done.\n```json\n" + json.dumps(findings) + "\n```\n"


def _put(batch: Path, arm: str, rep: int, findings, *, failure: bool = False, old: bool = False,
         model: str = MODEL) -> Path:
    """One replay cell graded by the built-in grader against the task's gold file under ``_eval/content``."""
    task_dir = batch / "_eval" / "content" / "tasks" / TASK
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "gold.json").write_text(json.dumps(GOLD))
    spec = {"id": TASK, "family": "review", "grader": "dokimi:review_replay", "score": {"metric": "recall", "max": 1}}
    (task_dir / "task.json").write_text(json.dumps(spec))
    answer = _answer(findings)
    metrics, details = main.split_result(review.grade_replay(spec, task_dir, answer))
    if old:
        details.pop("matches")
    _cell(batch, TASK, arm, rep, score=metrics["recall"], failure=failure, model=model)
    rd = batch / f"{TASK}__{arm.replace(':', '-')}__{model}__r{rep}"
    (rd / "task.json").write_text(json.dumps(spec))
    (rd / "answer.txt").write_text(answer)
    (rd / "grade.json").write_text(json.dumps({"schema": "repo_grade.v1", "grader": "dokimi:review_replay",
                                               "pass": None, "metrics": metrics, "details": details, "errors": []}))
    return rd


def _batch(tmp: Path) -> Path:
    """Two arms with two credited pairs each, a grader failure and a cell graded before ``matches``."""
    batch = tmp / "replay"
    _put(batch, "guidance:none", 1, [_f(11, "The TODO is repeated five times"), _f(30, "mul adds")])
    _put(batch, "guidance:raw", 1, [_f(10, "Add a TODO naming the issue"),
                                    _f(5, "read() has no docstring", file="src/io.py", rule="docs",
                                       evidence="def read(path):")])
    _put(batch, "guidance:none", 2, [_f(30, "grader-failure finding")], failure=True)
    _put(batch, "guidance:raw", 2, [_f(30, "old-grade finding")], old=True)
    return batch


def _reply(request) -> str:
    prompt = llm.prompt_text(request)
    label = next(v for k, v in REPLIES.items() if f"\n{k}" in prompt)
    return json.dumps({"label": label, "reason": f"the judge read {label}"})


@pytest.fixture
def judge_llm(monkeypatch):
    """The fake judge: answers by the finding in the prompt, each call priced $0.40; the response cache is off."""
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    fake = llm.FakeAdapter(respond=_reply, usage=UsageRecord(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                                                             cost_usd=0.4))
    with llm.use_adapter(fake):
        yield fake


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    args = build_parser().parse_args(["repo", "hits", *argv])
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def _judged(batch: Path, arm: str, rep: int) -> Path:
    return batch / f"{TASK}__{arm.replace(':', '-')}__{MODEL}__r{rep}" / HITS_JUDGED


def test_judge_reads_the_full_texts_and_writes_recall_judged_per_cell(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--json")
    assert code == 0
    assert len(LONG) > review.CUT
    todo = next(p for p in judge_llm.prompts if "The TODO is repeated" in p)
    assert LONG in todo and "HUMAN THREAD at src/calc.py:10" in todo and "FINDING at src/calc.py:11" in todo
    docs = next(p for p in judge_llm.prompts if "read() has no docstring" in p)
    assert "rule: docs\nevidence: def read(path):" in docs
    none = load_artifact("repo_hits_judged", _judged(batch, "guidance:none", 1))
    assert [(p["thread"], p["finding"], p["label"]) for p in none["pairs"]] == [
        ("t_op", 1, "same point"), ("t_todo", 0, "unrelated")]
    assert none["counts"] == {"same point": 1, "related": 0, "unrelated": 1, "unjudged": 0}
    assert (none["n_threads"], none["recall"], none["recall_judged"]) == (3, 0.667, 0.333)
    raw = load_artifact("repo_hits_judged", _judged(batch, "guidance:raw", 1))
    assert raw["counts"] == {"same point": 1, "related": 1, "unrelated": 0, "unjudged": 0}
    assert raw["judge"] == {"provider": "anthropic", "model": "claude-opus-5-5"}
    result = json.loads(out)
    assert [(r["arm"], r["cells"], r["pairs"], r["recall"], r["recall_judged"]) for r in result["rows"]] == [
        ("guidance:none", 1, 2, 0.667, 0.333), ("guidance:raw", 1, 2, 0.667, 0.333)]
    assert (result["calls"], result["spent_usd"], result["calibration"]) == (4, 1.6, None)
    assert result["uncalibrated_arms"] == ["guidance:none", "guidance:raw"]  # no --labels: no arm is calibrated


def test_a_cell_with_no_credited_pair_is_written_and_counted_in_the_means(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    _put(batch, "guidance:raw", 3, [_f(100, "far from every thread")])
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--json")
    assert code == 0 and len(judge_llm.requests) == 4
    empty = load_artifact("repo_hits_judged", _judged(batch, "guidance:raw", 3))
    assert (empty["pairs"], empty["n_threads"], empty["recall"], empty["recall_judged"]) == ([], 3, 0.0, 0.0)
    raw = next(r for r in json.loads(out)["rows"] if r["arm"] == "guidance:raw")
    assert (raw["cells"], raw["pairs"], raw["recall"], raw["recall_judged"]) == (2, 2, 0.3335, 0.1665)


def test_cells_without_matches_and_grader_failures_are_counted_not_judged(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert code == 0
    assert not any("grader-failure finding" in p or "old-grade finding" in p for p in judge_llm.prompts)
    assert not _judged(batch, "guidance:none", 2).exists() and not _judged(batch, "guidance:raw", 2).exists()
    lines = out.splitlines()
    assert lines[0] == (f"{batch}: 4 credited pair(s) in 4 dokimi:review_replay cell(s), judge {JUDGE}; "
                        f"2 cell(s) written ({HITS_JUDGED})")
    assert "not judged: 1 cell(s) graded without hit details, 1 grader failure(s)" in lines
    assert lines[-2].startswith("recall_judged is uncalibrated: label a sample of pairs (`repo hits sheet`)")


def test_a_reply_that_never_parses_leaves_the_pair_unjudged(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    batch = _batch(tmp_path)

    def reply(request):
        return "no idea" if "mul adds" in llm.prompt_text(request) else _reply(request)

    with llm.use_adapter(llm.FakeAdapter(respond=reply)) as fake:
        code, _, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert code == 0 and len(fake.requests) == 3 + 1 + 2  # the redraws of the unparsed pair, then the rest
    none = json.loads(_judged(batch, "guidance:none", 1).read_text())
    assert none["counts"] == {"same point": 0, "related": 0, "unrelated": 1, "unjudged": 1}
    unparsed = next(p for p in none["pairs"] if p["thread"] == "t_op")
    assert (unparsed["label"], unparsed["reason"]) == (None, None) and unparsed["error"].startswith("no reply parsed")
    assert none["recall_judged"] == 0.0  # an unjudged pair is not counted as the same point


@pytest.mark.parametrize("judge", [f"anthropic/{MODEL}", "claude-cli/sonnet", "claude-cli/haiku"])
def test_a_judge_that_runs_an_answer_model_is_refused_before_any_call(tmp_path, capsys, judge):
    batch = _batch(tmp_path)
    meta = batch / f"{TASK}__guidance-raw__{MODEL}__r1" / "meta.json"
    meta.write_text(json.dumps({"model": "claude-haiku-5-5"}))  # what the harness ran, as meta.json records it
    with llm.use_adapter(llm.FakeAdapter()) as fake:  # an unscripted call fails the test
        code, out, err = _run(capsys, "judge", str(batch), "--judge", judge)
    assert (code, out, fake.requests) == (2, "", [])
    assert err.startswith(f"✗ judge {judge} runs the same model as the answer model")
    assert not list(batch.glob(f"*/{HITS_JUDGED}"))


@pytest.mark.parametrize("record", ["result.json", "meta.json"])
@pytest.mark.parametrize("resolved", ["claude-opus-5-5", ["claude-sonnet-5-5", "claude-opus-5-5"]])
def test_a_judge_that_answered_a_cell_under_another_name_is_refused_before_any_call(tmp_path, capsys, record,
                                                                                    resolved):
    batch = _batch(tmp_path)
    path = batch / f"{TASK}__guidance-raw__{MODEL}__r1" / record
    rec = json.loads(path.read_text())
    path.write_text(json.dumps({**rec, "model_resolved": resolved}))  # asked of sonnet, the trace says opus answered
    with llm.use_adapter(llm.FakeAdapter()) as fake:  # an unscripted call fails the test
        code, out, err = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert (code, out, fake.requests) == (2, "", [])
    assert err.startswith(f"✗ judge {JUDGE} runs the same model as the answer model 'claude-opus-5-5'")
    assert not list(batch.glob(f"*/{HITS_JUDGED}"))


def test_a_judge_of_another_family_is_taken(tmp_path, capsys, judge_llm):
    code, _, _ = _run(capsys, "judge", str(_batch(tmp_path)), "--judge", "anthropic/claude-haiku-5-5")
    assert code == 0 and len(judge_llm.requests) == 4


def test_dry_run_counts_the_pairs_and_prices_them_with_no_call(tmp_path, capsys, monkeypatch):
    batch = _batch(tmp_path)
    priced = []
    monkeypatch.setattr("dokimi.llm_pricing.list_cost_usd",
                        lambda model, provider, tokens_in, tokens_out: priced.append((model, provider)) or 0.01)
    with llm.use_adapter(llm.FakeAdapter()) as fake:
        code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--dry-run", "--json")
    assert (code, fake.requests) == (0, [])
    assert json.loads(out) == {"batch": str(batch), "judge": {"provider": "anthropic", "model": "claude-opus-5-5"},
                               "cells": 2, "pairs": 4, "to_judge": 4, "estimated_usd": 0.04}
    assert priced == [("claude-opus-5-5", "anthropic")] * 4
    assert not list(batch.glob(f"*/{HITS_JUDGED}"))


def test_max_usd_starts_no_call_once_spent_and_a_cell_left_gets_no_file(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    stale = _judged(batch, "guidance:raw", 1)
    stale.write_text("{}")  # an earlier run's file is not left to read as this run's
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--max-usd", "1")
    assert code == 1
    assert len(judge_llm.requests) == 3  # $0.40 each: the third starts at $0.80, the fourth would at $1.20
    assert _judged(batch, "guidance:none", 1).is_file() and not stale.exists()
    assert (f"1 cell(s) not fully judged (--max-usd spent), no {HITS_JUDGED}: "
            f"{TASK}__guidance-raw__{MODEL}__r1") in out.splitlines()


def test_a_file_that_cannot_be_written_stops_the_run_with_exit_2_and_no_later_call(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    _put(batch, "guidance:raw", 3, [_f(30, "mul adds")])  # one more pair, judged after the failing cell
    blocked = _judged(batch, "guidance:raw", 1)
    blocked.mkdir()  # a directory where the file goes: the save fails
    code, out, err = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert (code, out) == (2, "")
    assert len(judge_llm.requests) == 4  # none r1 and raw r1; raw r3's call never starts
    assert err.startswith(f"✗ cannot write {blocked}: ")
    assert err.rstrip().endswith(f"stopped there after 4 judge call(s), $1.6000; "
                                 f"the 1 cell(s) written before keep their {HITS_JUDGED}")
    assert _judged(batch, "guidance:none", 1).is_file() and blocked.is_dir()
    assert not _judged(batch, "guidance:raw", 3).exists()


def test_an_earlier_file_that_cannot_be_removed_stops_the_run_with_exit_2(tmp_path, capsys, judge_llm, monkeypatch):
    batch = _batch(tmp_path)
    _put(batch, "guidance:raw", 3, [_f(100, "far from every thread")])  # no pair: written unless the run stopped
    stale = _judged(batch, "guidance:raw", 1)
    stale.write_text("{}")
    unlink = Path.unlink

    def refuse(self, missing_ok=False):
        if self.name == HITS_JUDGED:
            raise PermissionError(13, "Permission denied")
        return unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    code, out, err = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--max-usd", "1")
    assert (code, out) == (2, "")
    assert len(judge_llm.requests) == 3  # the budget stops raw r1 at its second pair
    assert err.startswith(f"✗ cannot remove {stale}: Permission denied; stopped there after 3 judge call(s), "
                          f"$1.2000; the 1 cell(s) written before keep their {HITS_JUDGED}")
    assert _judged(batch, "guidance:none", 1).is_file() and stale.read_text() == "{}"
    assert not _judged(batch, "guidance:raw", 3).exists()


def _regrade(batch: Path, arm: str, rep: int, state: str) -> None:
    """Regrade a cell into a state `load_hits` leaves out: a grader failure, or a grade with no ``matches``."""
    rd = _judged(batch, arm, rep).parent
    name = "result.json" if state == "grader failure" else "grade.json"
    doc = json.loads((rd / name).read_text())
    if state == "grader failure":
        doc["grade"]["status"] = "grader_failure"
    else:
        doc["details"] = {"parse_error": "no findings JSON"}
    (rd / name).write_text(json.dumps(doc))


@pytest.mark.parametrize("state", ["grader failure", "no matches"])
def test_a_judged_cell_regraded_out_of_the_hits_loses_its_earlier_file(tmp_path, capsys, judge_llm, state):
    batch = _batch(tmp_path)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert code == 0 and not any(line.startswith("removed the earlier") for line in out.splitlines())
    stale, kept = _judged(batch, "guidance:raw", 1), _judged(batch, "guidance:none", 1)
    earlier = stale.read_text()
    _regrade(batch, "guidance:raw", 1, state)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert code == 0 and not stale.exists() and kept.is_file()
    assert (f"removed the earlier {HITS_JUDGED} of 1 cell(s) not judged now (a grader failure, a grade without hit "
            f"details, another grader): {TASK}__guidance-raw__{MODEL}__r1") in out.splitlines()
    stale.write_text(earlier)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--json")
    assert code == 0 and not stale.exists() and kept.is_file()
    assert json.loads(out)["stale_removed"] == [f"{TASK}__guidance-raw__{MODEL}__r1"]
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--json")
    assert (code, json.loads(out)["stale_removed"]) == (0, [])  # nothing left to remove


def test_an_earlier_file_of_a_regraded_cell_that_cannot_be_removed_stops_the_run_before_any_call(tmp_path, capsys,
                                                                                               judge_llm):
    batch = _batch(tmp_path)
    assert _run(capsys, "judge", str(batch), "--judge", JUDGE)[0] == 0
    kept, stale = _judged(batch, "guidance:none", 1), _judged(batch, "guidance:raw", 1)
    _regrade(batch, "guidance:raw", 1, "grader failure")
    stale.unlink()
    stale.mkdir()  # a directory in the file's place: the removal fails
    with llm.use_adapter(llm.FakeAdapter()) as fake:  # an unscripted call fails the test
        code, out, err = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert (code, out, fake.requests) == (2, "", [])
    assert err.startswith(f"✗ cannot remove {stale}: ")
    assert err.rstrip().endswith(f"stopped there after 0 judge call(s), $0.0000; "
                                 f"the 0 cell(s) written before keep their {HITS_JUDGED}")
    assert stale.is_dir() and kept.is_file()


def test_dry_run_removes_no_earlier_file(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    assert _run(capsys, "judge", str(batch), "--judge", JUDGE)[0] == 0
    _regrade(batch, "guidance:raw", 1, "no matches")
    code, _, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--dry-run")
    assert code == 0 and _judged(batch, "guidance:raw", 1).is_file()


def test_a_pair_whose_full_text_is_missing_is_reported_and_not_judged(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    (batch / f"{TASK}__guidance-raw__{MODEL}__r1" / "answer.txt").write_text("no findings any more")
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert code == 0 and len(judge_llm.requests) == 2
    raw = json.loads(_judged(batch, "guidance:raw", 1).read_text())
    assert raw["counts"]["unjudged"] == 2 and raw["recall_judged"] == 0.0
    assert raw["pairs"][0]["error"].startswith("not judged: ") and "holds no findings JSON" in raw["pairs"][0]["error"]
    assert sum(line.startswith("not judged, full text not found: ") for line in out.splitlines()) == 2


def test_kappa_and_agreement_on_a_known_table():
    person = ["same point"] * 4 + ["related"] * 2 + ["unrelated"] * 4
    judge = ["same point"] * 3 + ["related", "related", "unrelated"] + ["unrelated"] * 3 + ["same point"]
    # observed 0.7; both raters 4/2/4, so chance 0.16 + 0.04 + 0.16 = 0.36: (0.7 - 0.36) / 0.64
    assert cohen_kappa(person, judge) == 0.531
    assert cohen_kappa(["related"] * 3, ["related"] * 3) is None  # chance agreement 1: kappa is undefined
    pairs = [Pair({"cell": "c", "arm": "a" if i < 10 else "b", "thread": i}) for i in range(12)]
    rated = calibrate({p.key: person[i] if i < 10 else "related" for i, p in enumerate(pairs)},
                      {p.key: judge[i] for i, p in enumerate(pairs) if i < 10}, pairs)
    assert rated == [{"arm": "a", "labelled": 10, "agreement": 0.7, "kappa": 0.531},
                     {"arm": "b", "labelled": 0, "agreement": None, "kappa": None}]  # the judge left b unjudged


def test_labels_are_compared_per_arm_and_an_unknown_thread_is_refused(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    sheet_file = tmp_path / "sheet.json"
    assert _run(capsys, "sheet", str(batch), "--out", str(sheet_file))[0] == 0
    entries = json.loads(sheet_file.read_text())
    person = {"t_todo": "unrelated", "t_op": "related", 7: "related"}  # t_todo of raw is left unlabelled
    for e in entries:
        e["label"] = None if (e["arm"], e["thread"]) == ("guidance:raw", "t_todo") else person[e["thread"]]
    sheet_file.write_text(json.dumps(entries))
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(sheet_file), "--json")
    assert code == 0
    assert json.loads(out)["calibration"] == [  # none: agree on t_todo, not on t_op; raw: agree on thread 7
        {"arm": "guidance:none", "labelled": 2, "agreement": 0.5, "kappa": 0.333},
        {"arm": "guidance:raw", "labelled": 1, "agreement": 1.0, "kappa": None}]
    assert json.loads(out)["uncalibrated_arms"] == []
    text = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(sheet_file))[1]
    assert "kappa 0.333" in text and "uncalibrated" not in text
    judge_llm.requests.clear()
    entries[1]["thread"] = "t_gone"
    sheet_file.write_text(json.dumps(entries))
    code, out, err = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(sheet_file))
    assert (code, out, judge_llm.requests) == (2, "", [])
    assert err.strip() == (f"✗ {sheet_file} entry 1: thread 't_gone' is not a credited pair of cell "
                           f"{entries[1]['cell']}")


def _sheet(tmp_path: Path, capsys, batch: Path, label=lambda entry: "related") -> Path:
    """A `repo hits sheet` file of every pair of the batch, each labelled ``label(entry)``."""
    sheet_file = tmp_path / "sheet.json"
    assert _run(capsys, "sheet", str(batch), "--out", str(sheet_file))[0] == 0
    sheet_file.write_text(json.dumps([{**e, "label": label(e)} for e in json.loads(sheet_file.read_text())]))
    return sheet_file


UNCALIBRATED = ("recall_judged is uncalibrated (no pair carries both a person's and the judge's label): label a "
                "sample of pairs (`repo hits sheet`) and pass it with --labels before quoting it")


@pytest.mark.parametrize("case", ["unfilled sheet", "empty list", "labels only on unjudged pairs"])
def test_labels_no_pair_shares_with_the_judge_leave_recall_judged_uncalibrated(tmp_path, capsys, monkeypatch, case):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    batch = _batch(tmp_path)
    labels = _sheet(tmp_path, capsys, batch, label=lambda e: None if case == "unfilled sheet" else "related")
    if case == "empty list":
        labels.write_text("[]")
    unparsed = case == "labels only on unjudged pairs"  # the judge never replies with a label: every pair unjudged
    with llm.use_adapter(llm.FakeAdapter(respond=(lambda _: "no idea") if unparsed else _reply)):
        code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels), "--json")
        assert code == 0
        result = json.loads(out)
        text = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels))[1].splitlines()
    assert result["uncalibrated_arms"] == ["guidance:none", "guidance:raw"]
    assert result["calibration"] == ([{"arm": arm, "labelled": 0, "agreement": None, "kappa": None}
                                      for arm in ("guidance:none", "guidance:raw")] if unparsed else [])
    assert text[-2] == UNCALIBRATED and not any(line.startswith("calibration against") for line in text)


def test_labels_on_one_arm_only_name_the_arm_left_uncalibrated(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    labels = _sheet(tmp_path, capsys, batch, label=lambda e: "related" if e["arm"] == "guidance:none" else None)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels), "--json")
    assert code == 0
    result = json.loads(out)
    assert [c["arm"] for c in result["calibration"]] == ["guidance:none"]
    assert result["uncalibrated_arms"] == ["guidance:raw"]
    text = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels))[1].splitlines()
    assert text[-4:-1] == ["calibration against the labels (person vs judge):",
                           "  guidance:none: 2 labelled pair(s), agreement 0.000, kappa 0.000",
                           "recall_judged is uncalibrated in guidance:raw: no pair of it carries both a person's "
                           "and the judge's label"]


def test_a_label_on_another_finding_than_the_credited_one_is_refused_before_any_call(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    labels = _sheet(tmp_path, capsys, batch)
    entries = json.loads(labels.read_text())
    entries[1]["finding"] = 99  # a hand-edit, or a sheet written before a regrade credited another finding
    labels.write_text(json.dumps(entries))
    code, out, err = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels))
    assert (code, out, judge_llm.requests) == (2, "", [])
    credited = next(p.hit["finding"] for p in read_pairs(batch)[1]
                    if p.key == pair_key(entries[1]["cell"], entries[1]["thread"]))
    assert err.strip() == (f"✗ {labels} entry 1: the sheet labels finding 99 of thread {entries[1]['thread']!r}, but "
                           f"the batch credits finding {credited} for it in cell {entries[1]['cell']}; write a new "
                           "sheet with `repo hits sheet`")


@pytest.mark.parametrize("entry, says", [
    ({"cell": "elsewhere", "thread": "t_op", "finding": 1, "label": "related"}, "cell 'elsewhere' has no credited pair"),
    ({"thread": "t_op", "finding": 1, "label": "related"}, "is not an object with `cell`, `thread`, `finding` and"),
    ({"cell": f"{TASK}__guidance-none__{MODEL}__r1", "thread": "t_op", "label": "related"},
     "is not an object with `cell`, `thread`, `finding` and `label`"),
    ("t_op", "is not an object with `cell`, `thread`, `finding` and `label`")])
def test_a_label_entry_naming_no_pair_is_refused(tmp_path, capsys, judge_llm, entry, says):
    batch = _batch(tmp_path)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([entry]))
    code, _, err = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels))
    assert code == 2 and err.startswith(f"✗ {labels} entry 0") and says in err
    assert judge_llm.requests == []


def test_a_label_outside_the_three_is_refused(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    labels = tmp_path / "labels.json"
    cell = f"{TASK}__guidance-none__{MODEL}__r1"
    labels.write_text(json.dumps([{"cell": cell, "thread": "t_op", "finding": 1, "label": "maybe"}]))
    code, _, err = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--labels", str(labels))
    assert (code, judge_llm.requests) == (2, [])
    assert err.strip() == f"✗ {labels} entry 0: label 'maybe' is not one of 'same point', 'related', 'unrelated'"


def _pairs(n_a: int, n_b: int) -> list[Pair]:
    return [Pair({"cell": f"c{i}", "task": "t", "arm": arm, "model": "m", "rep": 1, "thread": i, "finding": 0},
                 comment="c", text="f") for i, arm in enumerate(["a"] * n_a + ["b"] * n_b)]


def test_the_sheet_is_stratified_by_arm_capped_per_arm_and_seeded():
    pairs = _pairs(30, 3)
    drawn = sheet(pairs, per_arm=25, seed=0)
    assert [e["arm"] for e in drawn] == ["a"] * 25 + ["b"] * 3  # every pair of the arm with fewer
    assert len({e["thread"] for e in drawn}) == 28
    assert sheet(pairs, per_arm=25, seed=0) == drawn
    assert sheet(pairs, per_arm=25, seed=1) != drawn
    assert all(e["label"] is None and e["comment"] == "c" and e["finding_text"] == "f" for e in drawn)
    pairs[0].problem = "gone"
    assert 0 not in [e["thread"] for e in sheet(pairs, per_arm=40)]  # a pair with no text cannot be labelled


def test_sheet_command_writes_the_full_texts_for_a_person(tmp_path, capsys):
    batch = _batch(tmp_path)
    out_file = tmp_path / "labels" / "sheet.json"
    code, out, _ = _run(capsys, "sheet", str(batch), "--out", str(out_file), "--per-arm", "1", "--seed", "3")
    assert code == 0
    entries = json.loads(out_file.read_text())
    assert [e["arm"] for e in entries] == ["guidance:none", "guidance:raw"]
    assert out.strip() == f"wrote 2 pair(s) to label to {out_file}; guidance:none: 1 of 2; guidance:raw: 1 of 2"
    found, pairs = read_pairs(batch)
    full = {pair_key(p.cell, p.hit["thread"]): (p.comment, p.text) for p in pairs}
    for e in entries:
        assert (e["comment"], e["finding_text"]) == full[pair_key(e["cell"], e["thread"])]
        assert e["label"] is None and set(e) >= {"cell", "task", "arm", "model", "rep", "thread", "finding"}
    assert any(p.comment == LONG for p in pairs) and found["grader_failures"] == 1


def test_the_schema_validates_a_written_file_and_rejects_a_fourth_label(tmp_path, capsys, judge_llm):
    batch = _batch(tmp_path)
    assert _run(capsys, "judge", str(batch), "--judge", JUDGE)[0] == 0
    doc = json.loads(_judged(batch, "guidance:none", 1).read_text())
    validate_artifact("repo_hits_judged", doc)
    doc["pairs"][0]["label"] = "maybe"
    with pytest.raises(ArtifactValidationError, match="maybe"):
        validate_artifact("repo_hits_judged", doc)


def test_a_rerun_rewrites_the_file_from_the_response_cache(tmp_path, capsys, monkeypatch):
    batch = _batch(tmp_path)
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path / "cache"))
    with llm.use_adapter(llm.FakeAdapter(respond=_reply)) as first:
        _run(capsys, "judge", str(batch), "--judge", JUDGE)
    written = _judged(batch, "guidance:none", 1).read_text()
    _judged(batch, "guidance:none", 1).write_text("{}")
    with llm.use_adapter(llm.FakeAdapter()) as second:  # an unscripted call fails the test
        code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE)
    assert (len(first.requests), second.requests, code) == (4, [], 0)
    assert _judged(batch, "guidance:none", 1).read_text() == written
    assert out.splitlines()[-1] == "0 judge call(s), $0.0000"


def test_judge_takes_provider_slash_model_only(tmp_path, capsys):
    for bad in ("claude-opus-5-5", "nowhere/model", "anthropic/"):
        with pytest.raises(SystemExit) as stopped:
            build_parser().parse_args(["repo", "hits", "judge", str(tmp_path), "--judge", bad])
        assert stopped.value.code == 2
        assert "takes PROVIDER/MODEL" in capsys.readouterr().err

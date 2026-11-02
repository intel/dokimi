# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``dokimi.eval.headroom`` and ``eval headroom``: ceiling, floor and provisional items.

Runs are built as arms.v1 payloads in memory; no model is called.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pytest

from dokimi import records
from dokimi.cli import build_parser
from dokimi.commands.headroom import cmd_eval_headroom
from dokimi.eval.headroom import (
    CEILING, DEFAULT_CEILING, DEFAULT_FLOOR, DEFAULT_MIN_REPS, FLOOR, _fmt, check_thresholds, format_table,
    headroom,
)


def _run(items, baseline="baseline"):
    """An arms.v1 payload: ``items`` is ``[(question_id, {arm: aggregate-or-None})]``."""
    answers, evaluations = [], []
    for qid, scores in items:
        answers.append({"question_id": qid, "question_text": f"question {qid}",
                        "arms": {arm: {"answer": "x"} for arm in scores}})
        evaluations.append({"question_id": qid,
                            "scores": {arm: ({"aggregate": v} if v is not None else None)
                                       for arm, v in scores.items()}})
    return records.read({"baseline_arm": baseline, "answers": answers, "evaluations": evaluations},
                        source="test")


def _flags(result):
    return {i["question_id"]: i["flag"] for i in result["items"]}


def test_no_skill_at_the_ceiling_is_flagged_and_just_below_is_not():
    run = _run([("1", {"baseline": 90, "skill": 95}), ("2", {"baseline": 89.9, "skill": 99})])
    assert _flags(headroom([run])) == {"1": CEILING, "2": None}


def test_the_threshold_compares_the_unrounded_mean():
    # 89.996 is below 0.9; rounding the mean to 4 decimals would have flagged it.
    run = _run([("1", {"baseline": 89.996, "skill": 99})])
    assert _flags(headroom([run])) == {"1": None}


def test_a_mean_exactly_at_the_ceiling_is_flagged_though_float_division_undershoots():
    # Two runs at 85 and 95 average to exactly 90; 0.85 + 0.95 over 2 is 0.8999999999999999 in floats.
    runs = [_run([("1", {"baseline": 85, "skill": 85})]), _run([("1", {"baseline": 95, "skill": 95})])]
    assert _flags(headroom(runs, min_reps=1)) == {"1": CEILING}


def test_a_mean_exactly_at_the_floor_is_flagged_though_float_division_overshoots():
    # Two runs at 20 and 40 average to exactly 30; every arm at 30 is at the floor 0.3.
    runs = [_run([("1", {"baseline": 20, "skill": 20})]), _run([("1", {"baseline": 40, "skill": 40})])]
    assert _flags(headroom(runs, floor=0.3, min_reps=1)) == {"1": FLOOR}


def test_a_mean_a_hair_below_the_ceiling_is_not_a_ceiling():
    # 89.99999995 on 0-100 is 0.8999999995: a real 5e-10 below 0.9, not float error, so unflagged.
    run = _run([("1", {"baseline": 89.99999995, "skill": 95})])
    assert _flags(headroom([run], min_reps=2)) == {"1": None}


def test_a_mean_a_hair_above_zero_is_not_the_floor():
    # 0.00000005 on 0-100 is 5e-10 above a floor of 0: every arm is positive, so unflagged.
    run = _run([("1", {"baseline": 0.00000005, "skill": 0.00000005})])
    assert _flags(headroom([run], min_reps=2)) == {"1": None}


def test_floor_needs_every_arm_at_zero_not_just_the_baseline():
    run = _run([("1", {"baseline": 0, "skill": 0}), ("2", {"baseline": 0, "skill": 40})])
    assert _flags(headroom([run])) == {"1": FLOOR, "2": None}


def test_floor_needs_the_baseline_at_the_floor_too_not_just_the_skill_arms():
    # The baseline answers question 1 (40) though the skill arm scores 0: no floor.
    run = _run([("1", {"baseline": 40, "skill": 0}), ("2", {"baseline": 0, "skill": 0})])
    assert _flags(headroom([run])) == {"1": None, "2": FLOOR}


def test_thresholds_are_configurable():
    run = _run([("1", {"baseline": 60, "skill": 10}), ("2", {"baseline": 5, "skill": 15})])
    result = headroom([run], ceiling=0.5, floor=0.2)
    assert _flags(result) == {"1": CEILING, "2": FLOOR}
    assert result["thresholds"] == {"ceiling": 0.5, "floor": 0.2, "min_reps": 3}


def test_a_single_run_is_provisional_and_three_runs_are_not():
    one = _run([("1", {"baseline": 95, "skill": 95})])
    item = headroom([one])["items"][0]
    assert (item["reps"], item["provisional"]) == (1, True)
    three = [_run([("1", {"baseline": 95, "skill": 95})]) for _ in range(3)]
    item = headroom(three)["items"][0]
    assert (item["reps"], item["provisional"], item["flag"]) == (3, False, CEILING)


def test_provisional_flag_is_kept_next_to_the_ceiling_flag():
    result = headroom([_run([("1", {"baseline": 95, "skill": 95})])])
    assert result["summary"] == {"items": 1, "ceiling": 1, "floor": 0, "provisional": 1,
                                 "flagged_provisional": 1, "no_flag": 0}


def test_summary_counts_each_kind_of_item_apart():
    # Three runs. "f1", "f2" and "ok" are in all three; "cp" and "up" only in the first, so they are provisional.
    steady = [("f1", {"baseline": 0, "skill": 0}), ("f2", {"baseline": 0, "skill": 0}),
              ("ok", {"baseline": 50, "skill": 60})]
    first = [("cp", {"baseline": 95, "skill": 95}), ("up", {"baseline": 50, "skill": 60}), *steady]
    result = headroom([_run(first), _run(steady), _run(steady)])
    assert _flags(result) == {"cp": CEILING, "up": None, "f1": FLOOR, "f2": FLOOR, "ok": None}
    assert result["summary"] == {"items": 5, "ceiling": 1, "floor": 2, "provisional": 2,
                                 "flagged_provisional": 1, "no_flag": 2}


def test_items_list_numeric_ids_in_numeric_order_then_the_other_ids():
    scores = {"baseline": 50, "skill": 60}
    run = _run([(qid, scores) for qid in ["q1", "10", "b", "2", "a", "1"]])
    assert [i["question_id"] for i in headroom([run])["items"]] == ["1", "2", "10", "a", "b", "q1"]


def test_the_ceiling_reads_the_mean_over_runs():
    # One run at the top does not make a ceiling: the mean (0.85) is below 0.9.
    runs = [_run([("1", {"baseline": 100, "skill": 100})]), _run([("1", {"baseline": 70, "skill": 70})])]
    item = headroom(runs, min_reps=1)["items"][0]
    assert item["no_skill"] == 0.85 and item["flag"] is None
    runs[1] = _run([("1", {"baseline": 90, "skill": 90})])
    item = headroom(runs, min_reps=1)["items"][0]
    assert item["no_skill"] == 0.95 and item["flag"] == CEILING


def test_reps_count_the_runs_that_scored_the_baseline():
    # The skill arm missed one run; the baseline scored all three, so the item has three reps.
    runs = [_run([("1", {"baseline": 50, "skill": 50})]) for _ in range(3)]
    runs[2] = _run([("1", {"baseline": 50, "skill": None})])
    item = headroom(runs)["items"][0]
    assert item["reps"] == 3 and item["provisional"] is False


def test_reps_follow_the_baseline_even_when_a_skill_arm_was_scored_in_more_runs():
    runs = [_run([("1", {"baseline": 50, "skill": 50})]), _run([("1", {"baseline": None, "skill": 50})]),
            _run([("1", {"baseline": None, "skill": 50})])]
    item = headroom(runs)["items"][0]
    assert item["reps"] == 1 and item["provisional"] is True


def test_an_item_the_baseline_never_scored_has_no_reps_and_no_flag():
    # Item 2 keeps the baseline scored somewhere, so the run is not refused as unjudged.
    runs = [_run([("1", {"baseline": None, "skill": 95}), ("2", {"baseline": 40, "skill": 95})]) for _ in range(3)]
    item = {i["question_id"]: i for i in headroom(runs)["items"]}["1"]
    assert (item["reps"], item["no_skill"], item["flag"]) == (0, None, None)


def test_an_arm_no_run_scored_is_listed_as_unscored():
    run = _run([("1", {"baseline": 40, "skill": 60, "other": None})])
    item = headroom([run], min_reps=1)["items"][0]
    assert item["unscored_arms"] == ["other"] and item["arms"]["other"] is None


def test_an_arm_scored_in_some_runs_is_not_unscored():
    runs = [_run([("1", {"baseline": 40, "skill": 60, "other": 50})]),
            _run([("1", {"baseline": 40, "skill": 60, "other": None})])]
    item = headroom(runs, min_reps=1)["items"][0]
    assert item["unscored_arms"] == [] and item["arms"]["other"] == 0.5


def test_floor_leaves_out_an_arm_no_run_scored():
    # "other" never answered; the arms that did are all at zero, so the item is a floor.
    run = _run([("1", {"baseline": 0, "skill": 0, "other": None})])
    assert _flags(headroom([run])) == {"1": FLOOR}


def test_floor_needs_a_non_baseline_arm_that_scored():
    # Only the baseline answered (at zero): nothing shows the question is unanswerable for a skill.
    assert _flags(headroom([_run([("1", {"baseline": 0})])])) == {"1": None}
    assert _flags(headroom([_run([("1", {"baseline": 0, "skill": None})])])) == {"1": None}


def test_a_given_no_skill_arm_replaces_the_declared_baseline():
    # The runs declare "baseline"; the caller reads "skill" as the control. Every verdict follows "skill".
    runs = [_run([("1", {"baseline": 10, "skill": 95}), ("2", {"baseline": 30, "skill": 0}),
                  ("3", {"baseline": 0, "skill": 0}), ("4", {"baseline": 40, "skill": None})])
            for _ in range(2)]
    runs.append(_run([("1", {"baseline": 10, "skill": None}), ("2", {"baseline": 30, "skill": 0}),
                      ("3", {"baseline": 0, "skill": 0}), ("4", {"baseline": 40, "skill": None})]))
    declared = headroom(runs)
    assert declared["no_skill_arm"] == "baseline" and _flags(declared) == {"1": None, "2": None, "3": FLOOR, "4": None}
    given = headroom(runs, no_skill_arm="skill")
    assert given["no_skill_arm"] == "skill"
    assert _flags(given) == {"1": CEILING, "2": None, "3": FLOOR, "4": None}
    by_id = {i["question_id"]: i for i in given["items"]}
    assert (by_id["1"]["no_skill"], by_id["1"]["reps"], by_id["1"]["provisional"]) == (0.95, 2, True)
    assert (by_id["2"]["reps"], by_id["2"]["provisional"]) == (3, False)
    assert (by_id["4"]["no_skill"], by_id["4"]["reps"]) == (None, 0)


def test_unknown_no_skill_arm_is_refused_and_lists_the_arms():
    # A mistyped arm used to read as "no ceiling anywhere" with exit 0.
    run = _run([("1", {"baseline": 99, "skill": 99})])
    with pytest.raises(ValueError, match="'nope'.*the arms are: baseline, skill"):
        headroom([run], no_skill_arm="nope")


def test_repeated_question_id_in_one_run_is_refused():
    run = records.read({"baseline_arm": "baseline", "answers": [
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "a"}}},
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "b"}}}]})
    with pytest.raises(ValueError, match="question_id '1' more than once"):
        headroom([run], no_skill_arm="baseline")


def test_a_non_ascii_digit_question_id_is_kept_and_sorted_as_text():
    # '²'.isdigit() is true and int('²') raises: the id is an ordinary id, sorted after the numeric ones.
    run = _run([("²", {"baseline": 50, "skill": 50}), ("10", {"baseline": 50, "skill": 50}),
                ("2", {"baseline": 50, "skill": 50})])
    assert [i["question_id"] for i in headroom([run])["items"]] == ["2", "10", "²"]


def test_a_digit_question_id_past_the_int_limit_is_sorted_not_refused():
    # int() refuses more than 4300 digits (Python's str-to-int limit): a long numeric id still sorts by value.
    long_id = "1" * 5000
    run = _run([(long_id, {"baseline": 50, "skill": 50}), ("10", {"baseline": 50, "skill": 50}),
                ("0002", {"baseline": 50, "skill": 50})])
    assert [i["question_id"] for i in headroom([run])["items"]] == ["0002", "10", long_id]


def test_an_arm_named_with_docs_that_no_run_scored_is_listed_as_unscored():
    # arms.v1 allows with_docs as an arm name; arm_names() drops it, so an unscored one went unlisted.
    run = _run([("1", {"baseline": 40, "skill": 60, "with_docs": None})])
    item = headroom([run], min_reps=1)["items"][0]
    assert item["unscored_arms"] == ["with_docs"] and item["arms"]["with_docs"] is None


def test_the_table_and_json_do_not_round_0_89996_up_to_the_ceiling():
    run = _run([("1", {"baseline": 89.996, "skill": 99})])
    result = headroom([run], min_reps=2)
    assert result["items"][0]["no_skill"] == pytest.approx(0.89996) and result["items"][0]["flag"] is None
    table = format_table(result)
    assert "0.89" in table and "0.90" not in table
    assert _fmt(0.8999999996) == "0.899999999"


def test_the_table_lists_the_control_arm_once():
    result = headroom([_run([("1", {"baseline": 40, "skill": 60})])], min_reps=2)
    header = format_table(result).splitlines()[0].split()
    assert header == ["question", "reps", "no-skill", "skill", "flag", "provisional"]


@pytest.mark.parametrize("kwargs, message", [
    ({"ceiling": 0.0}, r"ceiling must be in \(0, 1\], not 0\.0"),
    ({"ceiling": -0.1}, r"ceiling must be in \(0, 1\], not -0\.1"),
    ({"ceiling": 1.5}, r"ceiling must be in \(0, 1\], not 1\.5"),
    ({"floor": 0.9}, r"floor must be in \[0, ceiling\), not 0\.9 \(ceiling 0\.9\)"),
    ({"floor": -0.1}, r"floor must be in \[0, ceiling\), not -0\.1"),
    ({"min_reps": 0}, r"min_reps must be at least 1, not 0"),
    ({"min_reps": -1}, r"min_reps must be at least 1, not -1"),
    ({"ceiling": 1e-7}, r"ceiling must be at least 1e-06, not 1e-07"),
    ({"floor": 1e-7}, r"floor must be 0 or at least 1e-06, not 1e-07"),
])
def test_unusable_thresholds_are_refused(kwargs, message):
    run = _run([("1", {"baseline": 50, "skill": 50})])
    with pytest.raises(ValueError, match=message):
        headroom([run], **kwargs)


def test_the_threshold_boundaries_are_accepted():
    # ceiling 1.0 is the top of (0, 1]; floor 0 is the bottom of [0, ceiling); one rep is the least.
    run = _run([("1", {"baseline": 100, "skill": 100}), ("2", {"baseline": 99, "skill": 99})])
    result = headroom([run], ceiling=1.0, floor=0.0, min_reps=1)
    assert _flags(result) == {"1": CEILING, "2": None}
    assert result["thresholds"] == {"ceiling": 1.0, "floor": 0.0, "min_reps": 1}
    assert headroom([run], ceiling=0.5, floor=0.49)["thresholds"]["floor"] == 0.49
    assert headroom([run], ceiling=0.01, floor=0.0, min_reps=1)["thresholds"]["ceiling"] == 0.01


def test_no_runs_are_refused():
    with pytest.raises(ValueError, match="no runs to read"):
        headroom([])


def test_a_run_with_no_questions_has_no_items():
    result = headroom([_run([])])
    assert result["items"] == [] and result["runs"] == 1
    assert result["summary"] == {"items": 0, "ceiling": 0, "floor": 0, "provisional": 0,
                                 "flagged_provisional": 0, "no_flag": 0}


def test_a_run_nothing_was_judged_in_is_refused():
    # The answers are there but nothing was judged: no item could be read as a ceiling or a floor.
    run = records.read({"baseline_arm": "baseline", "answers": [
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "a"}, "skill": {"answer": "b"}}}]})
    with pytest.raises(ValueError, match="no run scored the no-skill arm 'baseline'"):
        headroom([run])


def test_a_baseline_named_differently_from_the_scores_is_refused():
    run = _run([("1", {"no_skill": 100, "skill": 50})], baseline="control")
    with pytest.raises(ValueError, match="no run scored the no-skill arm 'control'"):
        headroom([run])


def test_binary_answers_are_refused():
    binary = records.read({"answers": [{"question_id": "1", "question_text": "q",
                                        "with_docs": {"answer": "a"}, "without_docs": {"answer": "b"}}]})
    with pytest.raises(ValueError, match="arms runs only"):
        headroom([binary], no_skill_arm="without_docs")


def test_runs_that_declare_different_baselines_need_an_explicit_arm():
    a = _run([("1", {"baseline": 50, "skill": 50})], baseline="baseline")
    b = _run([("1", {"control": 50, "skill": 50})], baseline="control")
    with pytest.raises(ValueError, match="--baseline-arm"):
        headroom([a, b])
    result = headroom([a, b], no_skill_arm="baseline")
    assert result["no_skill_arm"] == "baseline" and result["runs"] == 2
    item = result["items"][0]
    # "baseline" was scored in run a only, so one rep; "control" is just another arm here.
    assert (item["reps"], item["provisional"], item["no_skill"], item["flag"]) == (1, True, 0.5, None)
    assert item["arms"] == {"baseline": 0.5, "control": 0.5, "skill": 0.5}
    assert headroom([a, b], no_skill_arm="control")["items"][0]["reps"] == 1


def test_table_notes_the_arms_no_run_scored():
    run = _run([("1", {"baseline": 40, "skill": 60, "other": None}), ("2", {"baseline": 40, "skill": 60})])
    table = format_table(headroom([run], min_reps=1))
    assert "no run scored" in table and "other in 2 items" in table


def test_table_names_each_flagged_item():
    result = headroom([_run([("7", {"baseline": 97, "skill": 98}), ("8", {"baseline": 40, "skill": 60})])],
                      min_reps=1)
    table = format_table(result)
    assert "ceiling" in table and "\n7 " in table and "\n8 " not in table
    assert "ceiling 1, floor 0" in table


def _args(paths, **overrides):
    base = {"arms": [str(p) for p in paths], "no_skill_arm": None, "ceiling": 0.9, "floor": 0.0,
            "min_reps": 3, "output": None}
    return argparse.Namespace(**{**base, **overrides})


def test_command_writes_a_headroom_artifact_and_prints_the_table(tmp_path, capsys):
    arms_path = tmp_path / "arms.json"
    arms_path.write_text(json.dumps({"baseline_arm": "baseline", "answers": [
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "a"}, "skill": {"answer": "b"}}}],
        "evaluations": [{"question_id": "1", "scores": {"baseline": {"aggregate": 93}, "skill": {"aggregate": 94}}}]}))
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([arms_path], output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "headroom.v1"
    assert saved["items"][0]["flag"] == CEILING and saved["inputs"] == [str(arms_path)]
    printed = capsys.readouterr().out
    assert "ceiling" in printed and f"Wrote headroom artifact to {out}" in printed


def test_command_exits_2_on_an_unreadable_input(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([tmp_path / "missing.json"]))
    assert exc.value.code == 2
    assert "file not found" in capsys.readouterr().err


def _arms_file(path, scores, baseline="baseline"):
    """An arms.v1 file on disk: ``scores`` is ``{question_id: {arm: aggregate}}``."""
    payload = {"baseline_arm": baseline,
               "answers": [{"question_id": qid, "question_text": f"question {qid}",
                            "arms": {arm: {"answer": "x"} for arm in arms}}
                           for qid, arms in scores.items()],
               "evaluations": [{"question_id": qid,
                                "scores": {arm: {"aggregate": v} for arm, v in arms.items()}}
                               for qid, arms in scores.items()]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_command_accepts_a_threshold_below_one_score_step(tmp_path):
    # Aggregates 0.4 and 0.6 are 0.004 and 0.006 on 0-1: a floor of 0.005 tells them apart.
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 0.4, "skill": 0.4}, "2": {"baseline": 0.6, "skill": 0.4}})
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([path], floor=0.005, min_reps=1, output=out))
    flags = {i["question_id"]: i["flag"] for i in json.loads(out.read_text(encoding="utf-8"))["items"]}
    assert flags == {"1": FLOOR, "2": None}


def test_a_threshold_below_one_score_step_splits_scores_the_table_shows_exactly():
    # 0.9049 is below a ceiling of 0.905 and must show as such; a value shown as 0.90 would read as above it.
    run = _run([("1", {"baseline": 90.5, "skill": 90.5}), ("2", {"baseline": 90.49, "skill": 90.49}),
                ("3", {"baseline": 0.004, "skill": 0.004})])
    result = headroom([run], ceiling=0.905, min_reps=9)
    assert _flags(result) == {"1": CEILING, "2": None, "3": None}
    table = format_table(result)
    assert "0.905 " in table and "0.9049" in table and "0.00004" in table


def test_an_arm_the_run_declares_but_an_item_lacks_is_unscored_for_that_item():
    payload = {"baseline_arm": "baseline", "arms": ["baseline", "skill", "other"],
               "answers": [{"question_id": "1", "question_text": "q",
                            "arms": {"baseline": {"answer": "x"}, "skill": {"answer": "x"}}}],
               "evaluations": [{"question_id": "1",
                                "scores": {"baseline": {"aggregate": 0}, "skill": {"aggregate": 0}}}]}
    item = headroom([records.read(payload, source="test")], min_reps=1)["items"][0]
    assert item["unscored_arms"] == ["other"] and item["flag"] == FLOOR


def test_command_refuses_an_unknown_no_skill_arm_and_lists_the_arms(tmp_path, capsys):
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 99, "skill": 99}})
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], no_skill_arm="basline"))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "'basline'" in err and "the arms are: baseline, skill" in err


def test_repeated_input_files_count_as_one_run(tmp_path, capsys):
    one = _arms_file(tmp_path / "run1.json", {"1": {"baseline": 95, "skill": 95}})
    copy = tmp_path / "copy.json"
    copy.write_bytes(one.read_bytes())
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([one, one, one, copy], output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["runs"] == 1 and saved["inputs"] == [str(one)]
    assert saved["items"][0]["reps"] == 1 and saved["items"][0]["provisional"] is True
    err = capsys.readouterr().err
    assert err.count("counted once") == 3 and f"skipped {copy}: same content as {one}, counted once" in err


def test_distinct_input_files_each_count_as_a_run(tmp_path):
    paths = [_arms_file(tmp_path / f"run{i}.json", {"1": {"baseline": 95 - i, "skill": 95}}) for i in range(3)]
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args(paths, output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    item = saved["items"][0]
    assert (saved["runs"], item["reps"], item["provisional"], item["flag"]) == (3, 3, False, CEILING)


@pytest.mark.parametrize("field, value", [("model", "other-model"), ("judge_model", "other-judge"),
                                          ("library_name", "other-library"), ("run_config", {"max_tokens": 9})])
def test_command_refuses_runs_of_different_configurations(tmp_path, capsys, field, value):
    one = _arms_file(tmp_path / "run1.json", {"1": {"baseline": 95, "skill": 95}})
    two = _arms_file(tmp_path / "run2.json", {"1": {"baseline": 94, "skill": 95}})
    for path, extra in ((one, {}), (two, {field: value})):
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "model": "m", "judge_model": "j", "library_name": "lib",
                                    "run_config": {"max_tokens": 5, "seed": 1}, **extra}), encoding="utf-8")
    out = tmp_path / "headroom.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([one, two], output=out))
    assert exc.value.code == 2 and not out.exists()
    assert f"Error: {two} is not a repetition of {one}" in capsys.readouterr().err


def test_runs_that_differ_only_in_the_seed_are_repetitions(tmp_path):
    paths = []
    for seed in (1, 2):
        path = _arms_file(tmp_path / f"run{seed}.json", {"1": {"baseline": 95, "skill": 95 - seed}})
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "model": "m", "run_config": {"max_tokens": 5, "seed": seed}}),
                        encoding="utf-8")
        paths.append(path)
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args(paths, output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert (saved["runs"], saved["items"][0]["reps"]) == (2, 2)


def test_command_exits_2_when_the_output_cannot_be_written(tmp_path, capsys):
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 95, "skill": 95}})
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory, so --out cannot be written beneath it", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], output=blocker / "headroom.json"))
    assert exc.value.code == 2  # the exit-code table in docs/quickstart.md: an unwritable output is 2
    assert capsys.readouterr().err.startswith(f"Error: cannot write {blocker / 'headroom.json'}: ")


@pytest.mark.parametrize("spelling", ["dir", "dot", "dot-slash", "absent-with-slash", "absent-with-slash-dot",
                                       "file-with-slash"])
def test_command_exits_2_when_the_output_is_a_directory(tmp_path, monkeypatch, capsys, spelling):
    # A directory output would raise ValueError ("."), fail on the directory, or (spelled with a
    # slash, not existing yet) write a plain file under the directory's name: refuse all up front.
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 95, "skill": 95}})
    (tmp_path / "out").mkdir()
    (tmp_path / "existing.json").write_text("keep", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = {"dir": str(tmp_path / "out"), "dot": ".", "dot-slash": "./",
           "absent-with-slash": str(tmp_path / "newdir") + "/",
           "absent-with-slash-dot": str(tmp_path / "newdir") + "/.",
           "file-with-slash": str(tmp_path / "existing.json") + "/"}[spelling]
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], output=out))
    assert exc.value.code == 2
    assert "is not a file name" in capsys.readouterr().err
    assert not (tmp_path / "newdir").exists()
    assert (tmp_path / "existing.json").read_text(encoding="utf-8") == "keep"


def test_an_output_whose_directory_check_cannot_stat_does_not_crash_the_guard(tmp_path, monkeypatch, capsys):
    # A parent without search permission makes is_dir() raise PermissionError on Python 3.12 and 3.13.
    # The check must treat that path as not a directory and let the save report it, as before.
    import pathlib

    arms = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 99, "skill": 99}})
    out = tmp_path / "locked.json"
    real_is_dir = pathlib.Path.is_dir

    def is_dir(self):
        if self.name == out.name:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_dir(self)

    monkeypatch.setattr(pathlib.Path, "is_dir", is_dir)
    try:
        cmd_eval_headroom(_args([arms], output=out))
        code = 0
    except SystemExit as exc:
        code = exc.code
    assert code == 0 and out.exists()  # the simulated stat error is not a refusal: the save writes


def test_command_refuses_an_output_spelled_through_a_dotdot_to_an_input(tmp_path, capsys):
    # The same file under another spelling: the input as given, the output as sub/../arms.json.
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 99, "skill": 99}})
    (tmp_path / "sub").mkdir()
    before = path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([str(path)], output=str(tmp_path / "sub" / ".." / "arms.json")))
    assert exc.value.code == 2
    assert path.read_bytes() == before
    assert "overwrite" in capsys.readouterr().err


def test_command_refuses_a_question_id_repeated_within_one_file(tmp_path, capsys):
    path = tmp_path / "dup.json"
    path.write_text(json.dumps({"baseline_arm": "baseline", "answers": [
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "a"}}},
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "b"}}}]}))
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert str(path) in err and "question_id '1' appears more than once" in err


def test_command_passes_non_default_thresholds_to_the_analysis(tmp_path):
    # With the defaults neither item is flagged: 0.6 < 0.9, and 0.15 > 0.0.
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 60, "skill": 10},
                                               "2": {"baseline": 5, "skill": 15}})
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([path], ceiling=0.5, floor=0.2, min_reps=1, output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert {i["question_id"]: i["flag"] for i in saved["items"]} == {"1": CEILING, "2": FLOOR}
    assert saved["thresholds"] == {"ceiling": 0.5, "floor": 0.2, "min_reps": 1}
    assert [i["provisional"] for i in saved["items"]] == [False, False]


def test_the_registered_options_default_to_the_documented_thresholds(tmp_path):
    args = build_parser().parse_args(["eval", "headroom", str(tmp_path / "a.json")])
    assert (args.ceiling, args.floor, args.min_reps) == (DEFAULT_CEILING, DEFAULT_FLOOR, DEFAULT_MIN_REPS)
    assert (args.no_skill_arm, args.output) == (None, None)
    assert args.func is cmd_eval_headroom


def test_the_registered_options_reach_the_analysis_through_the_real_parser(tmp_path):
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 60, "skill": 10},
                                               "2": {"baseline": 5, "skill": 15}})
    out = tmp_path / "headroom.json"
    args = build_parser().parse_args(["eval", "headroom", str(path), "--ceiling", "0.5", "--floor", "0.2",
                                      "--min-reps", "1", "--baseline-arm", "baseline", "--out", str(out)])
    args.func(args)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["thresholds"] == {"ceiling": 0.5, "floor": 0.2, "min_reps": 1}
    assert {i["question_id"]: i["flag"] for i in saved["items"]} == {"1": CEILING, "2": FLOOR}


def test_command_reads_a_file_with_no_questions(tmp_path, capsys):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"baseline_arm": "baseline", "answers": [], "evaluations": []}), encoding="utf-8")
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([path], output=out))
    assert "(no item is flagged or provisional)" in capsys.readouterr().out
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["items"] == [] and saved["summary"]["items"] == 0 and saved["runs"] == 1


def test_command_refuses_a_file_nothing_was_judged_in(tmp_path, capsys):
    path = tmp_path / "unjudged.json"
    path.write_text(json.dumps({"baseline_arm": "baseline", "answers": [
        {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "a"}, "skill": {"answer": "b"}}}]}),
        encoding="utf-8")
    out = tmp_path / "headroom.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], output=out))
    assert exc.value.code == 2 and not out.exists()
    assert "no run scored the no-skill arm 'baseline'" in capsys.readouterr().err


def test_command_refuses_a_wrong_baseline_name_with_exit_2(tmp_path, capsys):
    path = _arms_file(tmp_path / "arms.json", {"1": {"no_skill": 100, "skill": 50}}, baseline="control")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert "pass --baseline-arm" in capsys.readouterr().err


def test_command_exits_2_on_an_input_nested_too_deep_to_read(tmp_path, capsys):
    path = tmp_path / "deep.json"
    path.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert "Error:" in capsys.readouterr().err


@pytest.mark.parametrize(("field", "value", "kind"), [
    ("run_config", "bad", "object"), ("run_config", 5, "object"), ("run_config", ["max_tokens"], "object"),
    ("arms", 5, "array"), ("arms", "baseline", "array"), ("arms", {"baseline": 1}, "array"),
    ("arms", [1, "baseline"], "array of strings"),
])
def test_command_exits_2_when_top_level_metadata_has_the_wrong_shape(tmp_path, capsys, field, value, kind):
    path = _arms_file(tmp_path / "run.json", {"1": {"baseline": 95}})
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, field: value}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert f"Error: {path}: {field} must be a JSON {kind}" in capsys.readouterr().err


def test_command_exits_2_when_runs_were_judged_under_different_contexts(tmp_path, capsys):
    base = _arms_file(tmp_path / "a.json", {"1": {"baseline": 95}})
    other = tmp_path / "b.json"
    data = json.loads(base.read_text(encoding="utf-8"))
    other.write_text(json.dumps({**data, "judge_context": "treatment_material.v1"}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([base, other]))
    assert exc.value.code == 2
    assert f"Error: {other} is not a repetition of {base}" in capsys.readouterr().err


def test_command_exits_2_on_a_mixed_judge_context(tmp_path, capsys):
    from dokimi.eval.judge_context import PROTOCOL_MIXED

    path = _arms_file(tmp_path / "run.json", {"1": {"baseline": 95}})
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "judge_context": PROTOCOL_MIXED}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert f"Error: {path}: judged some answers on the stored treatment material" in capsys.readouterr().err


def test_min_reps_must_be_a_positive_integer_at_the_parser(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["eval", "headroom", str(tmp_path / "a.json"), "--min-reps", "0"])
    assert exc.value.code == 2
    assert "--min-reps" in capsys.readouterr().err


def test_command_refuses_an_output_that_is_one_of_its_inputs(tmp_path, capsys):
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 99, "skill": 99}})
    before = path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], output=path))
    assert exc.value.code == 2
    assert path.read_bytes() == before
    assert "overwrite" in capsys.readouterr().err


def test_command_refuses_an_output_whose_rejected_sibling_is_an_input(tmp_path, capsys):
    # save_artifact deletes <output>.rejected after a good save: an input under that name would be lost.
    run = _arms_file(tmp_path / "run.json", {"1": {"baseline": 99, "skill": 99}})
    stale = tmp_path / "headroom.json.rejected"
    stale.write_bytes(run.read_bytes())
    before = stale.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([stale], output=tmp_path / "headroom.json"))
    assert exc.value.code == 2
    assert stale.read_bytes() == before
    assert "overwrite" in capsys.readouterr().err


def test_an_output_whose_path_cannot_be_resolved_does_not_crash_the_guard(tmp_path, monkeypatch):
    # Python 3.12 and older raise RuntimeError from resolve() on a symlink loop; 3.13+ do not.
    # The guard must not turn that into a traceback: an unresolvable path is not an input.
    import pathlib

    arms = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 99, "skill": 99}})
    out = tmp_path / "loop.json"
    real_resolve = pathlib.Path.resolve

    def resolve(self, *a, **k):
        if self.name == out.name:
            raise RuntimeError(f"Symlink loop from {self}")
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "resolve", resolve)
    cmd_eval_headroom(_args([arms], output=out))
    assert out.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_an_output_that_is_a_symlink_to_an_input_is_refused(tmp_path, capsys):
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 99, "skill": 99}})
    alias = tmp_path / "alias.json"
    os.symlink(path.name, alias)
    before = path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], output=alias))
    assert exc.value.code == 2
    assert path.read_bytes() == before
    assert "overwrite" in capsys.readouterr().err


def test_a_reserialized_copy_of_a_run_counts_once(tmp_path, capsys):
    arms = _arms_file(tmp_path / "run1.json", {"1": {"baseline": 95, "skill": 40}})
    copy = tmp_path / "copy.json"
    copy.write_text(json.dumps(json.loads(arms.read_text(encoding="utf-8")), indent=2, sort_keys=True),
                    encoding="utf-8")
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([arms, copy], output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["runs"] == 1 and saved["items"][0]["reps"] == 1
    assert "counted once" in capsys.readouterr().err


def test_table_note_counts_the_unscored_items_per_arm_instead_of_listing_them():
    items = [(str(q), {"baseline": 40, "skill": None}) for q in range(1, 501)]
    table = format_table(headroom([_run(items)], min_reps=1))
    note = next(line for line in table.splitlines() if line.startswith("note:"))
    assert note.endswith("skill in 500 items") and "(" not in note


def test_floor_is_not_flagged_when_the_no_skill_arm_was_never_scored_on_the_item():
    # Item 1's baseline judge failed; the skill at 0 alone is not a floor. Item 2 is one.
    run = _run([("1", {"baseline": None, "skill": 0}), ("2", {"baseline": 0, "skill": 0})])
    assert _flags(headroom([run], min_reps=1)) == {"1": None, "2": FLOOR}


def test_an_empty_out_is_refused_with_exit_2_through_the_real_parser(tmp_path, capsys):
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 95, "skill": 95}})
    args = build_parser().parse_args(["eval", "headroom", str(path), "--out", ""])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "not a file name" in capsys.readouterr().err


def _arms_file_with_text(path, questions, baseline="baseline"):
    """An arms.v1 file on disk: ``questions`` is ``[(question_id, question_text, baseline score)]``."""
    payload = {"baseline_arm": baseline,
               "answers": [{"question_id": qid, "question_text": text,
                            "arms": {baseline: {"answer": "x"}, "skill": {"answer": "x"}}}
                           for qid, text, _ in questions],
               "evaluations": [{"question_id": qid,
                                "scores": {baseline: {"aggregate": v}, "skill": {"aggregate": v}}}
                               for qid, _, v in questions]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_command_exits_2_when_two_inputs_give_one_id_different_question_text(tmp_path, capsys):
    # The first file's question 1 is a ceiling item, the second's is another question with no headroom:
    # pooled under id "1" they would average to a mid score, so the command refuses the pair.
    first = _arms_file_with_text(tmp_path / "a.json", [("1", "What is X?", 100)])
    second = _arms_file_with_text(tmp_path / "b.json", [("1", "What is Y?", 0)])
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([first, second]))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "question_id '1' has a different question text" in err and str(second) in err


def test_command_pools_partial_runs_that_share_ids_for_the_same_question(tmp_path, capsys):
    first = _arms_file_with_text(tmp_path / "a.json", [("1", "What is X?", 100), ("2", "What is Z?", 50)])
    second = _arms_file_with_text(tmp_path / "b.json", [("1", "What is X?", 100)])
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([first, second], output=out))
    assert "Error" not in capsys.readouterr().err
    items = {i["question_id"]: i["reps"] for i in json.loads(out.read_text(encoding="utf-8"))["items"]}
    assert items == {"1": 2, "2": 1}  # id 1 pooled over both runs, id 2 kept from the partial one


def test_a_copy_with_an_integral_score_written_as_a_float_counts_once(tmp_path, capsys):
    arms = _arms_file(tmp_path / "run1.json", {"1": {"baseline": 95, "skill": 40}})
    copy = tmp_path / "copy.json"
    copy.write_text(arms.read_text(encoding="utf-8").replace('"aggregate": 95', '"aggregate": 95.0'), encoding="utf-8")
    assert copy.read_text(encoding="utf-8") != arms.read_text(encoding="utf-8")
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([arms, copy], output=out))
    assert json.loads(out.read_text(encoding="utf-8"))["runs"] == 1
    assert "counted once" in capsys.readouterr().err


def test_canonical_digests_keep_booleans_and_strings_apart_from_numbers():
    from dokimi.commands.headroom import _canonical

    assert json.dumps(_canonical({"a": 1.0, "b": True, "c": "1.0", "d": [2.0, 2.5]}), sort_keys=True) == \
        '{"a": 1, "b": true, "c": "1.0", "d": [2, 2.5]}'


def _stamped_file(path, *, digest=None, reference=False, grounded=None, ground_truth=None):
    """An arms file with a ``skill`` arm; ``digest`` stamps its profile prompt, ``ground_truth`` its question."""
    skill = {"answer": "x", **({"metadata": {"profile_prompt_sha256": digest}} if digest else {})}
    question = {"question_id": "1", "question_text": "q", "arms": {"baseline": {"answer": "x"}, "skill": skill}}
    if ground_truth:
        question["ground_truth_chunk"] = ground_truth
    payload = {"baseline_arm": "baseline", "answers": [question],
               "evaluations": [{"question_id": "1", "scores": {"baseline": {"aggregate": 50},
                                                               "skill": {"aggregate": 50}}}]}
    if reference:
        payload["reference_scoring"] = {"scorer": "reference"}
    if grounded is not None:
        payload["grounded_questions"] = grounded
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_command_refuses_a_reference_scored_file_pooled_with_a_judged_one(tmp_path, capsys):
    judged = _stamped_file(tmp_path / "judged.json")
    rescored = _stamped_file(tmp_path / "rescored.json", reference=True)
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([judged, rescored]))
    assert exc.value.code == 2
    assert f"{rescored} was scored by the reference and {judged} by the judge" in capsys.readouterr().err


def test_command_refuses_a_profile_prompt_that_changed_under_the_same_arm_name(tmp_path, capsys):
    before = _stamped_file(tmp_path / "before.json", digest="a" * 64)
    after = _stamped_file(tmp_path / "after.json", digest="b" * 64)
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([before, after]))
    assert exc.value.code == 2
    assert "arm 'skill' ran with a different profile prompt" in capsys.readouterr().err


def test_command_pools_runs_whose_profile_prompt_digests_agree(tmp_path):
    one = _stamped_file(tmp_path / "one.json", digest="a" * 64)
    two = tmp_path / "two.json"  # another run: its own score, so the content is not the same run again
    two.write_text(one.read_text(encoding="utf-8").replace('"aggregate": 50', '"aggregate": 40'), encoding="utf-8")
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([one, two], output=out))
    assert json.loads(out.read_text(encoding="utf-8"))["runs"] == 2


def test_command_refuses_a_grounded_question_judged_ungrounded_in_another_run(tmp_path, capsys):
    grounded = _stamped_file(tmp_path / "grounded.json", grounded=1, ground_truth="the chunk")
    ungrounded = _stamped_file(tmp_path / "ungrounded.json", grounded=0)
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([grounded, ungrounded]))
    assert exc.value.code == 2
    assert f"is ungrounded in {ungrounded} but grounded in {grounded}" in capsys.readouterr().err


def test_a_legacy_run_without_a_grounding_count_is_not_compared_for_grounding(tmp_path):
    grounded = _stamped_file(tmp_path / "grounded.json", grounded=1, ground_truth="the chunk")
    legacy = _stamped_file(tmp_path / "legacy.json")
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([grounded, legacy], output=out))
    assert json.loads(out.read_text(encoding="utf-8"))["runs"] == 2


def test_a_mean_just_above_the_floor_is_not_shown_as_the_floor():
    # 30.00004 on 0-100 is 0.3000004: above a floor of 0.3, so unflagged, and shown with the digits that set it apart.
    run = _run([("1", {"baseline": 30.00004, "skill": 30.00004})])
    result = headroom([run], floor=0.3, min_reps=2)
    assert _flags(result) == {"1": None}
    assert "0.3000004" in format_table(result)


def test_the_minimum_positive_threshold_is_accepted_and_just_below_it_refused():
    check_thresholds(1e-6, 0.0, 1)
    check_thresholds(1.0, 1e-6, 1)
    with pytest.raises(ValueError, match="ceiling must be at least 1e-06"):
        check_thresholds(1e-7, 0.0, 1)
    with pytest.raises(ValueError, match="floor must be 0 or at least 1e-06"):
        check_thresholds(1.0, 5e-7, 1)


def test_command_exits_2_on_a_bare_record_list_instead_of_a_traceback(tmp_path, capsys):
    path = tmp_path / "bare.json"
    path.write_text(json.dumps([{"question_id": "1", "scores": {"baseline": {"aggregate": 100},
                                                                "skill": {"aggregate": 100}}}]), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert f"Error: {path}: expected a JSON object" in capsys.readouterr().err


@pytest.mark.parametrize(("metadata", "message"), [
    (["a"], "answer metadata must be a JSON object"),
    ("zz", "answer metadata must be a JSON object"),
    ({"treatments": 5}, "metadata.treatments must be a JSON array"),
])
def test_command_exits_2_when_an_answers_metadata_has_the_wrong_shape(tmp_path, capsys, metadata, message):
    path = _stamped_file(tmp_path / "run.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["answers"][0]["arms"]["skill"]["metadata"] = metadata
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert f"Error: {path}: {message}" in capsys.readouterr().err


def test_command_refuses_a_profile_prompt_that_changes_between_two_questions_of_one_file(tmp_path, capsys):
    path = _arms_file(tmp_path / "run.json", {"1": {"baseline": 50, "skill": 50}, "2": {"baseline": 50, "skill": 50}})
    data = json.loads(path.read_text(encoding="utf-8"))
    for answer, digest in zip(data["answers"], ("a" * 64, "b" * 64), strict=True):
        answer["arms"]["skill"]["metadata"] = {"profile_prompt_sha256": digest}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path]))
    assert exc.value.code == 2
    assert "arm 'skill' ran with a different profile prompt" in capsys.readouterr().err


def test_command_exits_1_when_the_payload_fails_the_artifact_schema(tmp_path, monkeypatch, capsys):
    from dokimi import artifacts

    def refuse(kind, data, path):
        raise artifacts.ArtifactValidationError("headroom.v1: items: refused by the schema")

    monkeypatch.setattr(artifacts, "save_artifact", refuse)
    path = _arms_file(tmp_path / "arms.json", {"1": {"baseline": 95}})
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([path], output=tmp_path / "headroom.json"))
    assert exc.value.code == 1
    assert "Error: headroom.v1: items: refused by the schema" in capsys.readouterr().err


def test_command_refuses_legacy_runs_without_a_top_level_arms_list_whose_arms_differ(tmp_path, capsys):
    one = _arms_file(tmp_path / "one.json", {"1": {"baseline": 100, "skill:a": 100}})
    two = _arms_file(tmp_path / "two.json", {"1": {"baseline": 50, "skill:b": 50}})
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([one, two]))
    assert exc.value.code == 2
    assert f"{two} is not a repetition of {one}" in capsys.readouterr().err


def test_command_pools_legacy_runs_without_a_top_level_arms_list_whose_arms_agree(tmp_path):
    one = _arms_file(tmp_path / "one.json", {"1": {"baseline": 100, "skill": 100}})
    two = _arms_file(tmp_path / "two.json", {"1": {"baseline": 50, "skill": 50}})
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([one, two], output=out))
    assert json.loads(out.read_text(encoding="utf-8"))["runs"] == 2


def _reference_file(path, questions, scores, ungraded=0):
    """An arms file rescored by ``eval reference`` against ``questions``: a summary per run, as the real artifact has."""
    data = json.loads(_arms_file(path, {"1": {"baseline": scores, "skill": scores}}).read_text(encoding="utf-8"))
    data["reference_scoring"] = {"scorer": "reference", "questions": questions,
                                 "summary": {"baseline": {"correct": int(scores >= 50)}},
                                 "ungraded_questions": ungraded}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_command_refuses_reference_scored_runs_graded_against_different_question_files(tmp_path, capsys):
    first = _reference_file(tmp_path / "first.json", "x1.json", 90)
    second = _reference_file(tmp_path / "second.json", "x2.json", 10)
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([first, second]))
    assert exc.value.code == 2
    assert f"{second} was scored by eval reference against different grading than {first}" in capsys.readouterr().err


def test_command_pools_reference_scored_runs_that_share_their_grading_and_differ_in_tallies(tmp_path):
    first = _reference_file(tmp_path / "first.json", "x1.json", 90)
    second = _reference_file(tmp_path / "second.json", "x1.json", 10)
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([first, second], output=out))
    assert json.loads(out.read_text(encoding="utf-8"))["runs"] == 2


def test_command_pools_reference_scored_runs_that_differ_only_in_ungraded_questions(tmp_path):
    first = _reference_file(tmp_path / "first.json", "x1.json", 90)
    second = _reference_file(tmp_path / "second.json", "x1.json", 10, ungraded=3)
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args([first, second], output=out))
    assert json.loads(out.read_text(encoding="utf-8"))["runs"] == 2


def test_command_checks_every_reference_scored_file_not_only_the_second(tmp_path, capsys):
    first = _reference_file(tmp_path / "first.json", "x1.json", 90)
    second = _reference_file(tmp_path / "second.json", "x1.json", 10)
    third = _reference_file(tmp_path / "third.json", "x2.json", 50)
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([first, second, third]))
    assert exc.value.code == 2
    assert f"{third} was scored by eval reference against different grading than {first}" in capsys.readouterr().err


def test_a_baseline_a_hair_below_the_ceiling_at_any_score_precision_is_not_a_ceiling():
    # 89.99999999995 on 0-100 is exactly 5e-13 below 0.9; the reader keeps the score, so it is not a ceiling.
    run = _run([("1", {"baseline": 89.99999999995, "skill": 95})])
    assert _flags(headroom([run], min_reps=1)) == {"1": None}


def test_arms_a_hair_above_zero_at_any_score_precision_are_not_the_floor():
    # 0.00000000005 on 0-100 is 5e-13 above a floor of 0 for every arm, so it is not the floor.
    run = _run([("1", {"baseline": 0.00000000005, "skill": 0.00000000005})])
    assert _flags(headroom([run], min_reps=1)) == {"1": None}


def test_command_refuses_legacy_runs_whose_empty_top_level_arms_list_hides_different_arms(tmp_path, capsys):
    one = _arms_file(tmp_path / "one.json", {"1": {"baseline": 100, "skill:a": 100}})
    two = _arms_file(tmp_path / "two.json", {"1": {"baseline": 50, "skill:b": 50}})
    for path in (one, two):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["arms"] = []  # an empty list is absent to replicate_key, so the arms the records show must count
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_headroom(_args([one, two]))
    assert exc.value.code == 2
    assert f"{two} is not a repetition of {one}" in capsys.readouterr().err

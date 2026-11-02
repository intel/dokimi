# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``dokimi.eval.headroom`` and ``eval headroom``: ceiling, floor and provisional items.

Runs are built as arms.v1 payloads in memory; no model is called.
"""

from __future__ import annotations

import pytest

from dokimi import records
from dokimi.eval.headroom import CEILING, FLOOR, _fmt, format_table, headroom
from tests.headroom_fixtures import _flags, _run


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

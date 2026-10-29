# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``dokimi.eval.headroom`` and ``eval headroom``: ceiling, floor and provisional items.

Runs are built as arms.v1 payloads in memory; no model is called.
"""

from __future__ import annotations

import argparse
import json

import pytest

from dokimi import records
from dokimi.commands.headroom import cmd_eval_headroom
from dokimi.eval.headroom import CEILING, FLOOR, format_table, headroom


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


def test_floor_needs_every_arm_at_zero_not_just_the_baseline():
    run = _run([("1", {"baseline": 0, "skill": 0}), ("2", {"baseline": 0, "skill": 40})])
    assert _flags(headroom([run])) == {"1": FLOOR, "2": None}


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


def test_the_ceiling_reads_the_mean_over_runs():
    # One run at the top does not make a ceiling: the mean (0.85) is below 0.9.
    runs = [_run([("1", {"baseline": 100, "skill": 100})]), _run([("1", {"baseline": 70, "skill": 70})])]
    item = headroom(runs, min_reps=1)["items"][0]
    assert item["no_skill"] == 0.85 and item["flag"] is None
    runs[1] = _run([("1", {"baseline": 90, "skill": 90})])
    item = headroom(runs, min_reps=1)["items"][0]
    assert item["no_skill"] == 0.95 and item["flag"] == CEILING


def test_reps_count_the_arm_scored_least_often():
    runs = [_run([("1", {"baseline": 50, "skill": 50})]) for _ in range(3)]
    runs[2] = _run([("1", {"baseline": 50, "skill": None})])
    item = headroom(runs)["items"][0]
    assert item["reps"] == 2 and item["provisional"] is True


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


def test_a_non_ascii_digit_question_id_is_refused_by_name():
    # '²'.isdigit() is true and int('²') raises: the id must be named, not crash.
    run = _run([("²", {"baseline": 50, "skill": 50})])
    with pytest.raises(ValueError, match="question id '²'"):
        headroom([run])


def test_the_table_and_json_do_not_round_0_89996_up_to_the_ceiling():
    run = _run([("1", {"baseline": 89.996, "skill": 99})])
    result = headroom([run], min_reps=2)
    assert result["items"][0]["no_skill"] == pytest.approx(0.89996) and result["items"][0]["flag"] is None
    table = format_table(result)
    assert "0.89" in table and "0.90" not in table


def test_the_table_lists_the_control_arm_once():
    result = headroom([_run([("1", {"baseline": 40, "skill": 60})])], min_reps=2)
    header = format_table(result).splitlines()[0].split()
    assert header == ["question", "reps", "no-skill", "skill", "flag", "provisional"]


@pytest.mark.parametrize("kwargs", [{"ceiling": 0.0}, {"ceiling": 1.5}, {"floor": 0.9},
                                    {"min_reps": 0}])
def test_unusable_thresholds_are_refused(kwargs):
    run = _run([("1", {"baseline": 50, "skill": 50})])
    with pytest.raises(ValueError):
        headroom([run], **kwargs)


def test_binary_answers_are_refused():
    binary = records.read({"answers": [{"question_id": "1", "question_text": "q",
                                        "with_docs": {"answer": "a"}, "without_docs": {"answer": "b"}}]})
    with pytest.raises(ValueError, match="arms runs only"):
        headroom([binary], no_skill_arm="without_docs")


def test_runs_that_declare_different_baselines_need_an_explicit_arm():
    a = _run([("1", {"baseline": 50, "skill": 50})], baseline="baseline")
    b = _run([("1", {"control": 50, "skill": 50})], baseline="control")
    with pytest.raises(ValueError, match="--no-skill-arm"):
        headroom([a, b])
    assert headroom([a, b], no_skill_arm="baseline")["no_skill_arm"] == "baseline"


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
    assert err.count("counted once") == 3 and f"skipped {copy}: same content as {one}" in err


def test_distinct_input_files_each_count_as_a_run(tmp_path):
    paths = [_arms_file(tmp_path / f"run{i}.json", {"1": {"baseline": 95 - i, "skill": 95}}) for i in range(3)]
    out = tmp_path / "headroom.json"
    cmd_eval_headroom(_args(paths, output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    item = saved["items"][0]
    assert (saved["runs"], item["reps"], item["provisional"], item["flag"]) == (3, 3, False, CEILING)


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

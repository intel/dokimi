# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare`: the start context of each arm, `--task`, and the notes that keep a row from being misread."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.compare import CompareError, compare, format_compare, min_detectable

from test_repo_compare import _cell, _row


def _first(batch: Path, task: str, arm: str, rep: int, tokens: int | None) -> None:
    """A finished gate cell whose ``agent`` records ``first_prompt_tokens`` (left out for ``None``: an older batch)."""
    _cell(batch, task, arm, rep)
    path = batch / f"{task}__{arm.replace(':', '-')}__m-1__r{rep}" / "result.json"
    rec = json.loads(path.read_text())
    if tokens is not None:
        rec["agent"]["first_prompt_tokens"] = tokens
    path.write_text(json.dumps(rec))


def test_first_prompt_tokens_is_compared_per_task_over_the_cells_that_have_it(tmp_path):
    b = tmp_path / "b"
    for task, base, treat in (("t1", (16_310, 16_320), (19_370, 19_390)), ("t2", (16_300, None), (19_400, 19_380))):
        for rep, (x, y) in enumerate(zip(base, treat, strict=True), 1):
            _first(b, task, "guidance:none", rep, x)
            _first(b, task, "guidance:raw", rep, y)
    row = _row(compare([b], "guidance:none"), "first_prompt_tokens")
    by_task = {t["task"]: t for t in row["tasks"]}
    assert by_task["t1"]["baseline"] == 16_315 and by_task["t1"]["treatment"] == 19_380
    assert by_task["t2"]["baseline"] == 16_300 and by_task["t2"]["n_baseline"] == 1  # the cell without it is left out
    assert row["delta"]["mean"] == pytest.approx((3_065 + 3_090) / 2) and row["p_holm"] is None


def test_first_prompt_tokens_of_an_older_batch_is_no_row_not_zero(tmp_path):
    old, mixed, reverse = tmp_path / "old", tmp_path / "mixed", tmp_path / "reverse"
    for rep in (1, 2):
        _first(old, "t1", "guidance:none", rep, None)
        _first(old, "t1", "guidance:raw", rep, None)
        _first(mixed, "t1", "guidance:none", rep, None)
        _first(mixed, "t1", "guidance:raw", rep, 19_380)
        _first(reverse, "t1", "guidance:none", rep, 16_315)
        _first(reverse, "t1", "guidance:raw", rep, None)
    s = compare([old], "guidance:none")
    assert "first_prompt_tokens" not in {r["measure"] for r in s["comparisons"]}
    assert not s["unpaired"] and not s["warnings"]
    for batch in (mixed, reverse):  # the arm without it is the baseline, then the treatment
        s = compare([batch], "guidance:none")
        assert "first_prompt_tokens" not in {r["measure"] for r in s["comparisons"]} and not s["unpaired"]
        assert s["warnings"] == ["guidance:raw m-1: only one arm reports first_prompt_tokens for any shared task; "
                                 "not compared"]


def _three_tasks(b: Path) -> None:
    for task, base_ok in (("t1", False), ("t2", True), ("t3", False)):
        _cell(b, task, "guidance:none", 1, success=base_ok)
        _cell(b, task, "guidance:raw", 1)
    _cell(b, "t4", "guidance:none", 1)  # only one arm ran it: unpaired, unless --task leaves it out
    (b / "t4__guidance-raw__m-1__r1").mkdir()
    (b / "t4__guidance-raw__m-1__r1" / "meta.json").write_text("{}")  # started, never finished: missing


def test_task_compares_only_the_named_tasks_and_says_so(tmp_path):
    b = tmp_path / "b"
    _three_tasks(b)
    whole = compare([b], "guidance:none")
    assert whole["task_filter"] is None and _row(whole, "gate")["n_tasks"] == 3
    assert [u["task"] for u in whole["unpaired"]] == ["t4"] and len(whole["missing"]) == 1
    s = compare([b], "guidance:none", tasks=["t1", "t2"])
    gate = _row(s, "gate")
    assert [t["task"] for t in gate["tasks"]] == ["t1", "t2"] and gate["up"] == 1
    assert s["unpaired"] == [] and s["missing"] == [] and s["cells"] == 4
    assert s["task_filter"] == {"tasks": ["t1", "t2"], "of": 4}
    assert "  tasks: 2 of 4 (--task)" in format_compare(s).splitlines()
    assert "(--task)" not in format_compare(whole)


def test_cli_task_is_repeatable_and_lands_in_the_json(tmp_path, capsys):
    b = tmp_path / "b"
    _three_tasks(b)
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none",
                                      "--task", "t3", "--task", "t4", "--json"])
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["task_filter"] == {"tasks": ["t3", "t4"], "of": 4}
    assert [t["task"] for t in _row(out, "gate")["tasks"]] == ["t3"]
    assert [u["task"] for u in out["unpaired"]] == ["t4"] and len(out["missing"]) == 1


def test_task_matches_a_missing_cell_by_its_task_not_by_a_prefix(tmp_path):
    # a task id may contain "__": the unfinished cell of fix__a is not a cell of fix
    b = tmp_path / "b"
    for task in ("fix", "fix__a"):
        _cell(b, task, "guidance:none", 1)
        _cell(b, task, "guidance:raw", 1)
    (b / "fix__a__guidance-raw__m-1__r2").mkdir()
    (b / "fix__a__guidance-raw__m-1__r2" / "meta.json").write_text("{}")  # started, never finished: missing
    s = compare([b], "guidance:none", tasks=["fix"])
    assert s["missing"] == [] and s["task_filter"] == {"tasks": ["fix"], "of": 2}
    assert "  tasks: 1 of 2 (--task)" in format_compare(s).splitlines()
    assert "cells missing" not in format_compare(s)
    assert compare([b], "guidance:none", tasks=["fix__a"])["missing"] == ["b/fix__a__guidance-raw__m-1__r2"]


def test_an_arm_or_metric_error_under_task_says_the_selection_is_the_reason(tmp_path):
    b = tmp_path / "b"
    _cell(b, "t1", "guidance:none", 1)
    _cell(b, "t1", "guidance:raw", 1)
    for arm in ("guidance:none", "guidance:other"):  # guidance:other ran only t2, with the metric
        _cell(b, "t2", arm, 1, metrics={"tok": 3.0})
    with pytest.raises(CompareError, match=r"no finished cell matches the treatment 'guidance:other' among the "
                                           r"--task selection$"):
        compare([b], "guidance:none", treatments=["guidance:other"], tasks=["t1"])
    with pytest.raises(CompareError, match=r"no finished cell matches the baseline 'guidance:other' among the "
                                           r"--task selection$"):
        compare([b], "guidance:other", tasks=["t1"])
    with pytest.raises(CompareError, match=r"reports a numeric or boolean --metric tok among the --task selection$"):
        compare([b], "guidance:none", metrics=("tok",), tasks=["t1"])
    with pytest.raises(CompareError, match=r"^no finished cell matches the treatment 'guidance:nope'$"):
        compare([b], "guidance:none", treatments=["guidance:nope"])  # without --task, no selection to name


def test_task_refuses_an_id_no_cell_has(tmp_path, capsys):
    b = tmp_path / "b"
    _three_tasks(b)
    with pytest.raises(CompareError, match=r"has --task t9, fix_x$"):
        compare([b], "guidance:none", tasks=["t1", "t9", "fix_x"])
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none", "--task", "t9"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "no finished cell of the compared batches has --task t9" in capsys.readouterr().err


def test_min_detectable_effect_arithmetic():
    # The recall row that read as "no gain": SD 0.222 of the per-task deltas over 26 tasks, t quantiles at df 25
    assert min_detectable(0.222, 26) == pytest.approx(0.126946, abs=1e-6)
    # half the spread or 4x the tasks: about the same figure, the t quantiles shrink a little with the tasks
    assert min_detectable(0.222, 26) == pytest.approx(min_detectable(0.444, 104), rel=0.05)


def test_min_detectable_uses_the_t_quantiles_of_the_interval_at_small_n():
    # four tasks: the interval is a t interval at df 3 (t(0.975, 3) = 3.182, t(0.80, 3) = 0.978), not normal ones
    assert min_detectable(0.456435, 4) == pytest.approx(0.949594, abs=1e-6)


def _score_tasks(b: Path, deltas: list[float], base: float) -> None:
    for i, d in enumerate(deltas):
        _cell(b, f"review_{i}", "guidance:none", 1, score=base)
        _cell(b, f"review_{i}", "guidance:raw", 1, score=base + d)


def test_an_interval_across_zero_is_inconclusive_with_its_mde(tmp_path):
    # six changed tasks: the sign-flip p can go down to 2/64, so 80% power names an effect
    _score_tasks(tmp_path / "b", [0.5, -0.5, 0.25, -0.25, 0.5, -0.5], 0.5)  # sample SD 0.474342
    s = compare([tmp_path / "b"], "guidance:none")
    score = _row(s, "score")
    assert score["mde"] == pytest.approx(min_detectable(0.474342, 6), abs=1e-6) and score["mde"] > 0.6
    assert score["interval_p_disagree"] is False
    text = format_compare(s)
    assert (f"  score inconclusive: the interval includes 0; with this spread the smallest effect 80% power "
            f"detects is about ±{score['mde']:.3f} (6 tasks)") in text.splitlines()
    assert "no effect" not in text
    _score_tasks(tmp_path / "one", [0.5], 0.5)
    one = compare([tmp_path / "one"], "guidance:none")
    assert _row(one, "score")["mde"] is None and "inconclusive" not in format_compare(one)


@pytest.mark.parametrize("deltas,changed,floor", [
    ([0.5, -0.5, 0.25, -0.25], 4, "0.125"),  # four tasks, sample SD 0.456435: a t power figure would say ±0.950
    ([0.5, -0.5, 0.25, -0.25, 0.5, 0.0], 5, "0.0625"),  # six tasks, one tied: five changed
])
def test_no_mde_where_the_sign_flip_p_cannot_reach_005(tmp_path, deltas, changed, floor):
    _score_tasks(tmp_path / "b", deltas, 0.5)
    s = compare([tmp_path / "b"], "guidance:none")
    score = _row(s, "score")
    assert score["mde"] is None and score["delta"]["lo"] < 0 < score["delta"]["hi"]
    assert (f"  score inconclusive: the interval includes 0; with {changed} changed tasks the sign-flip p cannot "
            f"go below {floor}: no effect can reach p < 0.05 here") in format_compare(s).splitlines()
    assert "80% power" not in format_compare(s)


def test_a_gate_row_of_two_changed_tasks_has_no_mde(tmp_path):
    b = tmp_path / "b"
    for task, base_ok in (("t1", False), ("t2", False), ("t3", True)):  # gate deltas [1, 1, 0]
        _cell(b, task, "guidance:none", 1, success=base_ok)
        _cell(b, task, "guidance:raw", 1)
    s = compare([b], "guidance:none")
    gate = _row(s, "gate")
    assert gate["p"] == 0.5 and gate["delta"]["lo"] == 0.0 and gate["mde"] is None
    assert ("  gate inconclusive: the interval includes 0; with 2 changed tasks the sign-flip p cannot go below "
            "0.5: no effect can reach p < 0.05 here") in format_compare(s).splitlines()


def test_a_bootstrap_interval_that_excludes_zero_against_p_025_is_flagged(tmp_path):
    _score_tasks(tmp_path / "b", [0.0, 0.0, 0.333, 0.167, 0.0, 0.333], 0.0)
    s = compare([tmp_path / "b"], "guidance:none")
    score = _row(s, "score")
    assert score["delta"]["lo"] > 0 and score["p"] == 0.25  # three non-zero deltas, all positive: p cannot go lower
    assert score["interval_p_disagree"] is True
    notes = [line for line in format_compare(s).splitlines() if line.startswith("  score")]
    assert notes[-1].startswith(f"  score: the interval [{score['delta']['lo']:+.3f}, +0.250] excludes 0 but p 0.25 "
                                "is ≥ 0.05; they come from two methods, a percentile bootstrap")
    assert notes[-1].endswith("; read p") and not any("inconclusive" in line for line in notes)


def test_a_descriptive_p_below_005_is_named_as_uncorrected(tmp_path):
    b = tmp_path / "b"
    for i, turns in enumerate([11, 11, 11, 11, 11, 30]):  # every task up, one far up: sign-flip p 2/64
        _cell(b, f"fix_{i}", "guidance:none", 1, turns=10)
        _cell(b, f"fix_{i}", "guidance:raw", 1, turns=turns)
    s = compare([b], "guidance:none")
    turns = _row(s, "turns")
    assert turns["p"] == pytest.approx(2 / 64) and turns["delta"]["lo"] < 0 < turns["delta"]["hi"]
    assert turns["interval_p_disagree"] is True and _row(s, "gate")["interval_p_disagree"] is False
    lines = format_compare(s).splitlines()
    disagree = [line for line in lines if line.startswith("  turns: the interval")]
    assert len(disagree) == 1 and "includes 0 but p 0.03125 is < 0.05" in disagree[0]
    assert "a t interval and a sign-flip test" in disagree[0] and "read p" not in disagree[0]
    assert ("  p < 0.05 on turns: descriptive rows, their p uncorrected and outside Holm's family, which covers "
            "gate and score only") in lines

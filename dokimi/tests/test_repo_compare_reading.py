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
    # The recall row that read as "no gain": SD 0.222 of the per-task deltas over 26 tasks
    assert min_detectable(0.222, 26) == pytest.approx(0.122, abs=5e-4)
    assert min_detectable(0.222, 26) == pytest.approx(min_detectable(0.444, 104))  # half the spread or 4x the tasks


def _score_tasks(b: Path, deltas: list[float], base: float) -> None:
    for i, d in enumerate(deltas):
        _cell(b, f"review_{i}", "guidance:none", 1, score=base)
        _cell(b, f"review_{i}", "guidance:raw", 1, score=base + d)


def test_an_interval_across_zero_is_inconclusive_with_its_mde(tmp_path):
    _score_tasks(tmp_path / "b", [0.5, -0.5, 0.25, -0.25], 0.5)  # sample SD 0.456435
    s = compare([tmp_path / "b"], "guidance:none")
    score = _row(s, "score")
    assert score["mde"] == pytest.approx(0.639371, abs=1e-6) and score["interval_p_disagree"] is False
    text = format_compare(s)
    assert ("  score inconclusive: the interval includes 0; with this spread the smallest effect 80% power "
            "detects is about ±0.639 (4 tasks)") in text.splitlines()
    assert "no effect" not in text
    _score_tasks(tmp_path / "one", [0.5], 0.5)
    one = compare([tmp_path / "one"], "guidance:none")
    assert _row(one, "score")["mde"] is None and "inconclusive" not in format_compare(one)


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

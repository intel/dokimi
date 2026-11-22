# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Test-retest noise of the novelty score: the floor, the pairing, and the command that reports it.

The corpus pairs are the ten S2 skills of the 2026-11-09 retest, pinned as ``S2_RETEST``: the
first run used seed 0, the retest seed 2 and a fresh cache. No model is called here.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty_retest as novelty_cmd
from dokimi.novelty.retest import load_runs, pair_runs, render_markdown, retest_summary
from dokimi.utils import InputError
from tests.cli_paths import novelty_argv

# (seed 0, seed 2) headline lenient K0 of the ten S2 skills (the 2026-11-09 retest).
S2_RETEST = [
    (0.33, 0.2),
    (0.16, 0.32),
    (0.16, 0.36),
    (0.68, 0.72),
    (0.28, 0.32),
    (0.08, 0.28),
    (0.28, 0.4),
    (0.16, 0.12),
    (0.48, 0.52),
    (0.6, 0.64),
]


def _pairs(scores):
    return [{"ref": f"skill:/s/{i}", "first": a, "second": b} for i, (a, b) in enumerate(scores)]


def _artifact(ref, lenient, *, seed=0, model="m", partial=False, replayed=0, config=None):
    doc = {
        "artifact": {"ref": ref},
        "k0": {"lenient": lenient, "strict": lenient / 2},
        "model": model,
        "run_config": {"seed": seed, **(config or {})},
        "cost": {"replayed_calls": replayed},
    }
    if partial:
        doc["partial"] = {"reason": "cap"}
    return doc


def _write_runs(root, runs):
    for ref, doc in runs.items():
        folder = root / ref.rsplit("/", 1)[-1]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "novelty.json").write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_the_s2_retest_reproduces_the_reported_noise_numbers():
    summary = retest_summary(_pairs(S2_RETEST), "lenient")
    assert summary["n_items"] == 10
    assert summary["pearson_r"] == pytest.approx(0.86, abs=0.005)
    assert summary["mean_abs_diff"] == pytest.approx(0.10, abs=0.005)
    assert summary["mean_shift"] == pytest.approx(0.067, abs=0.0005)
    # A single-run difference of 0.2 is inside the noise: 1.96 x the spread of the diffs.
    assert summary["floor"] == pytest.approx(0.21, abs=0.01)
    assert summary["n_comparisons"] == 45
    assert summary["n_below_floor"] == 26  # the docs quote 26 of the 45 pairs
    assert summary["small_sample"] is False


def test_a_comparison_below_the_floor_is_flagged_and_one_above_is_not():
    # Diffs of +0.01, -0.01 and -0.01 give a floor near 0.023; items 0.10 and 0.12 are inside
    # it, items 0.10 and 0.50 are not.
    pairs = _pairs([(0.10, 0.11), (0.12, 0.11), (0.50, 0.49)])
    summary = retest_summary(pairs)
    flagged = {(f["a"], f["b"]) for f in summary["below_floor"]}
    assert ("skill:/s/0", "skill:/s/1") in flagged
    assert ("skill:/s/0", "skill:/s/2") not in flagged
    assert ("skill:/s/1", "skill:/s/2") not in flagged


def test_a_zero_k0_is_a_measured_score_that_pairs():
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.0)}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.4, seed=2)}
    assert [p["ref"] for p in pair_runs(first, second, "lenient")["pairs"]] == ["skill:/s/a"]


def test_a_gap_under_the_unrounded_floor_is_below_it_and_one_over_it_is_not():
    # sd of the diffs 0.10203551 gives a floor of 0.1999896: a first-run gap of 0.1999 is
    # below it although the rounded floor reported is 0.2.
    summary = retest_summary(_pairs([(0.0, 0.0), (0.1999, 0.3442)]))
    assert summary["floor"] == pytest.approx(0.2, abs=0.00005)
    assert summary["n_below_floor"] == 1
    assert retest_summary(_pairs([(0.0, 0.0), (0.2, 0.3443)]))["n_below_floor"] == 0


def test_two_items_report_the_correlation_as_not_measured(tmp_path, capsys):
    pairs = _pairs([(0.1, 0.2), (0.5, 0.45)])
    assert "Pearson r between runs: not measured (needs 3 items)" in render_markdown(
        retest_summary(pairs), warnings=[])
    first = {r["ref"]: _artifact(r["ref"], r["first"]) for r in pairs}
    second = {r["ref"]: _artifact(r["ref"], r["second"], seed=2) for r in pairs}
    novelty_cmd.cmd_novelty_retest(argparse.Namespace(
        baseline=str(_write_runs(tmp_path / "a", first)), retest=str(_write_runs(tmp_path / "b", second)),
        scoring="lenient", out_dir=None))
    assert "Pearson r not measured (needs 3 items)" in capsys.readouterr().out


def test_a_constant_run_is_not_reported_as_too_few_items(tmp_path, capsys):
    pairs = [{"ref": f"skill:/s/{i}", "first": 0.5, "second": b} for i, b in enumerate((0.2, 0.4, 0.6))]
    summary = retest_summary(pairs)
    assert summary["pearson_r"] is None
    assert "needs 3 items" not in render_markdown(summary, warnings=[])
    assert "not defined" in render_markdown(summary, warnings=[])
    first = {p["ref"]: _artifact(p["ref"], p["first"]) for p in pairs}
    second = {p["ref"]: _artifact(p["ref"], p["second"], seed=2) for p in pairs}
    novelty_cmd.cmd_novelty_retest(argparse.Namespace(
        baseline=str(_write_runs(tmp_path / "a", first)), retest=str(_write_runs(tmp_path / "b", second)),
        scoring="lenient", out_dir=None))
    out = capsys.readouterr().out
    assert "needs 3 items" not in out
    assert "Pearson r not defined" in out


def test_an_out_dir_that_is_a_file_is_refused_as_bad_input(tmp_path):
    pairs = _pairs([(0.1, 0.2), (0.5, 0.45)])
    first = {r["ref"]: _artifact(r["ref"], r["first"]) for r in pairs}
    second = {r["ref"]: _artifact(r["ref"], r["second"], seed=2) for r in pairs}
    blocker = tmp_path / "out"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(InputError, match="out"):
        novelty_cmd.cmd_novelty_retest(argparse.Namespace(
            baseline=str(_write_runs(tmp_path / "a", first)),
            retest=str(_write_runs(tmp_path / "b", second)), scoring="lenient", out_dir=str(blocker)))


def test_identical_runs_measure_no_noise():
    summary = retest_summary(_pairs([(0.2, 0.2), (0.6, 0.6), (0.9, 0.9)]))
    assert summary["sd_diff"] == 0
    assert summary["floor"] == 0
    assert summary["n_below_floor"] == 0
    assert summary["pearson_r"] == 1


def test_one_item_gives_no_floor_and_no_correlation():
    summary = retest_summary(_pairs([(0.3, 0.4)]))
    assert summary["floor"] is None
    assert summary["pearson_r"] is None
    assert summary["n_comparisons"] == 0


def test_an_unknown_scoring_is_refused():
    with pytest.raises(InputError, match="unknown scoring"):
        retest_summary(_pairs([(0.1, 0.2), (0.3, 0.4)]), "fuzzy")


def test_pairing_reports_seed_model_partial_and_unmatched_items():
    first = {
        "skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0),
        "skill:/s/b": _artifact("skill:/s/b", 0.4, seed=0),
        "skill:/s/c": _artifact("skill:/s/c", 0.3, seed=0, partial=True),
        "skill:/s/only-first": _artifact("skill:/s/only-first", 0.2),
    }
    second = {
        "skill:/s/a": _artifact("skill:/s/a", 0.55, seed=0, model="other"),
        "skill:/s/b": _artifact("skill:/s/b", 0.42, seed=2),
        "skill:/s/c": _artifact("skill:/s/c", 0.31, seed=2),
    }
    paired = pair_runs(first, second, "lenient")
    assert [p["ref"] for p in paired["pairs"]] == ["skill:/s/a", "skill:/s/b"]
    assert paired["unmatched"] == ["skill:/s/only-first"]
    assert paired["unscored"] == ["skill:/s/c"]
    text = " ".join(paired["warnings"])
    assert "seed 0" in text  # item a: the same seed in both runs
    assert "different models" in text
    assert "one run only" in text
    assert "skill:/s/only-first" in text  # the unmatched ref is named, not only counted
    assert "skill:/s/c" in text  # the unscored ref is named too
    assert "partial" in text


def test_a_retest_that_replayed_cached_calls_warns_and_names_the_item():
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0)}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=2, replayed=400)}
    text = " ".join(pair_runs(first, second, "lenient")["warnings"])
    assert "skill:/s/a" in text
    assert "replayed 400" in text


def test_a_replay_in_the_baseline_warns_too():
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0, replayed=3)}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=2)}
    text = " ".join(pair_runs(first, second, "lenient")["warnings"])
    assert "replayed 3" in text


def test_a_run_config_difference_other_than_seed_warns_and_names_the_keys():
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0, config={"max_atoms": 12, "arms": ["full"]})}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.6, seed=2, config={"max_atoms": 20, "arms": ["full"]})}
    text = " ".join(pair_runs(first, second, "lenient")["warnings"])
    assert "max_atoms" in text
    assert "arms" not in text  # the keys that match are not named


def test_a_seed_only_change_gives_no_warning():
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0, config={"max_atoms": 12})}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.6, seed=2, config={"max_atoms": 12})}
    assert pair_runs(first, second, "lenient")["warnings"] == []


def _judged(doc, judge="j1", judges=None, rule=None):
    doc["judge_model"] = judge
    if judges is not None:
        doc["judge_panel"] = {"judges": judges, "rule": rule, "added_share_range": [0.1, 0.2]}
    return doc


def test_another_judge_model_is_not_read_as_noise():
    first = {"skill:/s/a": _judged(_artifact("skill:/s/a", 0.5, seed=0), judge="j1")}
    second = {"skill:/s/a": _judged(_artifact("skill:/s/a", 0.6, seed=2), judge="j2")}
    text = " ".join(pair_runs(first, second, "lenient")["warnings"])
    assert "graded differently (judge_model)" in text


def test_another_judge_panel_or_rule_with_the_same_first_judge_is_not_read_as_noise():
    first = {"skill:/s/a": _judged(_artifact("skill:/s/a", 0.5, seed=0), judges=["j1", "j2"], rule="median")}
    second = {"skill:/s/a": _judged(_artifact("skill:/s/a", 0.6, seed=2), judges=["j1", "j3"], rule="majority")}
    text = " ".join(pair_runs(first, second, "lenient")["warnings"])
    assert "graded differently (judges, rule)" in text


def test_the_same_panel_with_measured_values_that_vary_gives_no_warning():
    first = {"skill:/s/a": _judged(_artifact("skill:/s/a", 0.5, seed=0), judges=["j1", "j2"], rule="median")}
    second = {"skill:/s/a": _judged(_artifact("skill:/s/a", 0.6, seed=2), judges=["j1", "j2"], rule="median")}
    second["skill:/s/a"]["judge_panel"]["added_share_range"] = [0.3, 0.5]
    assert pair_runs(first, second, "lenient")["warnings"] == []


def test_a_non_finite_score_is_unscored_not_a_crash():
    first = {"skill:/s/a": _artifact("skill:/s/a", float("nan"))}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=2)}
    paired = pair_runs(first, second, "lenient")
    assert paired["unscored"] == ["skill:/s/a"]
    assert paired["pairs"] == []


def test_a_run_with_a_repeated_ref_is_refused(tmp_path):
    doc = _artifact("skill:/s/a", 0.5)
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (tmp_path / "x" / "novelty.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "y" / "novelty.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(InputError, match="appears twice"):
        load_runs(tmp_path)


def test_a_directory_without_novelty_output_is_refused(tmp_path):
    with pytest.raises(InputError, match="no novelty.json"):
        load_runs(tmp_path)


def test_a_run_saved_under_another_name_is_not_read(tmp_path):
    (tmp_path / "foo.json").write_text(json.dumps(_artifact("skill:/s/foo", 0.5)), encoding="utf-8")
    with pytest.raises(InputError, match="no novelty.json"):
        load_runs(tmp_path)


@pytest.mark.parametrize("body", ["[]", '"text"', '{"artifact": "x"}'])
def test_a_novelty_file_that_is_not_an_object_with_a_ref_is_refused(tmp_path, body):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "novelty.json").write_text(body, encoding="utf-8")
    with pytest.raises(InputError, match="not a novelty artifact"):
        load_runs(tmp_path)


def test_the_retest_command_is_registered_and_writes_the_report(tmp_path):
    first = _write_runs(
        tmp_path / "run1",
        {
            f"skill:/s/{i}": _artifact(f"skill:/s/{i}", a, seed=0)
            for i, (a, _) in enumerate(S2_RETEST)
        },
    )
    second = _write_runs(
        tmp_path / "run2",
        {
            f"skill:/s/{i}": _artifact(f"skill:/s/{i}", b, seed=2)
            for i, (_, b) in enumerate(S2_RETEST)
        },
    )
    args = build_parser().parse_args(
        [
            *novelty_argv("retest"),
            "--baseline",
            str(first),
            "--retest",
            str(second),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert args.func is novelty_cmd.cmd_novelty_retest

    args.func(args)

    saved = json.loads((tmp_path / "out" / "retest.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == "novelty_retest.v1"
    assert saved["n_items"] == 10
    assert saved["pearson_r"] == pytest.approx(0.86, abs=0.005)
    report = (tmp_path / "out" / "retest.md").read_text(encoding="utf-8")
    assert "Noise floor on `lenient`" in report
    assert "Comparisons below the floor" in report


def test_the_report_says_when_the_floor_is_not_measured():
    summary = retest_summary(_pairs([(0.3, 0.4)]))
    assert "Noise floor not measured" in render_markdown(summary, warnings=[])


def test_a_floor_from_few_items_carries_a_small_sample_caveat():
    summary = retest_summary(_pairs([(0.3, 0.4), (0.5, 0.45)]))
    assert summary["small_sample"] is True
    assert "fewer than 10 items" in render_markdown(summary, warnings=[])


def _run_retest(tmp_path, baseline, retest, *extra):
    args = build_parser().parse_args(
        [*novelty_argv("retest"), "--baseline", str(baseline), "--retest", str(retest), *extra]
    )
    args.func(args)


def test_the_command_says_when_one_item_gives_no_floor_and_names_the_warnings(tmp_path, capsys):
    first = _write_runs(tmp_path / "a", {"skill:/s/one": _artifact("skill:/s/one", 0.5, seed=0)})
    second = _write_runs(tmp_path / "b", {"skill:/s/one": _artifact("skill:/s/one", 0.6, seed=0)})
    _run_retest(tmp_path, first, second)
    captured = capsys.readouterr()
    assert "Noise floor not measured: 1 item" in captured.out
    assert "both runs used seed 0" in captured.err  # the same seed warns, not a silent pass


def test_the_command_prints_the_small_sample_caveat_with_the_floor(tmp_path, capsys):
    first = _write_runs(tmp_path / "a", {f"skill:/s/{i}": _artifact(f"skill:/s/{i}", a) for i, a in enumerate([0.3, 0.5])})
    second = _write_runs(tmp_path / "b", {f"skill:/s/{i}": _artifact(f"skill:/s/{i}", b, seed=2) for i, b in enumerate([0.4, 0.45])})
    _run_retest(tmp_path, first, second)
    assert "fewer than 10 items" in capsys.readouterr().out


def test_the_command_refuses_runs_with_no_item_in_common(tmp_path):
    first = _write_runs(tmp_path / "a", {"skill:/s/one": _artifact("skill:/s/one", 0.5)})
    second = _write_runs(tmp_path / "b", {"skill:/s/two": _artifact("skill:/s/two", 0.5)})
    with pytest.raises(InputError, match="no item scored in both runs"):
        _run_retest(tmp_path, first, second)


def test_the_zero_pair_refusal_names_the_excluded_items_on_stderr(tmp_path, capsys):
    first = _write_runs(tmp_path / "a", {"skill:/s/one": _artifact("skill:/s/one", 0.5)})
    second = _write_runs(tmp_path / "b", {"skill:/s/two": _artifact("skill:/s/two", 0.5)})
    with pytest.raises(InputError, match="no item scored in both runs"):
        _run_retest(tmp_path, first, second)
    err = capsys.readouterr().err
    assert "skill:/s/one" in err
    assert "skill:/s/two" in err


def test_the_zero_pair_refusal_names_unscored_items_on_stderr(tmp_path, capsys):
    first = _write_runs(tmp_path / "a", {"skill:/s/one": _artifact("skill:/s/one", 0.5, partial=True)})
    second = _write_runs(tmp_path / "b", {"skill:/s/one": _artifact("skill:/s/one", 0.5, seed=2)})
    with pytest.raises(InputError, match="no item scored in both runs"):
        _run_retest(tmp_path, first, second)
    assert "skill:/s/one" in capsys.readouterr().err


def test_a_ref_that_is_not_a_string_is_refused_as_bad_input(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "novelty.json").write_text('{"artifact": {"ref": ["skill:/s/a"]}}', encoding="utf-8")
    with pytest.raises(InputError, match="not a novelty artifact"):
        load_runs(tmp_path)


def test_changed_content_between_the_runs_is_not_read_as_noise():
    first = _artifact("skill:/s/a", 0.5, seed=0)
    second = _artifact("skill:/s/a", 0.6, seed=2)
    first["artifact"]["ref_digest"] = "aaa"
    second["artifact"]["ref_digest"] = "bbb"
    text = " ".join(pair_runs({"skill:/s/a": first}, {"skill:/s/a": second}, "lenient")["warnings"])
    assert "content changed" in text


def test_the_same_content_digest_gives_no_warning():
    first = _artifact("skill:/s/a", 0.5, seed=0)
    second = _artifact("skill:/s/a", 0.6, seed=2)
    first["artifact"]["ref_digest"] = second["artifact"]["ref_digest"] = "aaa"
    assert pair_runs({"skill:/s/a": first}, {"skill:/s/a": second}, "lenient")["warnings"] == []


def test_a_run_with_no_probed_atom_is_unscored_not_a_zero():
    first = _artifact("skill:/s/a", 0.0, seed=0)
    first["atoms"] = {"n": 0, "n_probed": 0}
    second = _artifact("skill:/s/a", 0.4, seed=2)
    paired = pair_runs({"skill:/s/a": first}, {"skill:/s/a": second}, "lenient")
    assert paired["pairs"] == []
    assert paired["unscored"] == ["skill:/s/a"]


def test_a_run_whose_baseline_grades_all_errored_is_unscored_not_a_zero():
    first = _artifact("skill:/s/a", 0.0, seed=0)
    first["atoms"] = {"n": 3, "n_probed": 3}
    first["arms"] = {"lenient": {"full": 0.5}, "strict": {"full": 0.4}}
    second = _artifact("skill:/s/a", 0.4, seed=2)
    second["arms"] = {"lenient": {"no_artifact": 0.4, "full": 0.5}}
    paired = pair_runs({"skill:/s/a": first}, {"skill:/s/a": second}, "lenient")
    assert paired["pairs"] == []
    assert paired["unscored"] == ["skill:/s/a"]


def test_summary_refuses_scores_outside_the_k0_domain():
    pairs = [{"ref": "skill:/s/a", "first": 1.7e308, "second": -1.7e308},
             {"ref": "skill:/s/b", "first": 0.1, "second": 0.2}]
    with pytest.raises(InputError, match="between 0 and 1"):
        retest_summary(pairs)


def test_a_novelty_file_that_is_not_utf8_is_refused_as_bad_input(tmp_path):
    (tmp_path / "novelty.json").write_bytes(b'{"artifact": {"ref": "skill:/s/\xff"}}')
    with pytest.raises(InputError, match="novelty.json"):
        load_runs(tmp_path)


def test_tier_forfeits_that_follow_the_atom_count_are_not_a_settings_change():
    base = {"name": "triage", "overrides": {}}
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0, config={"tier": {**base, "forfeits": []}})}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.6, seed=2, config={"tier": {**base, "forfeits": ["sampled"]}})}
    assert pair_runs(first, second, "lenient")["warnings"] == []


def test_another_tier_name_still_warns():
    first = {"skill:/s/a": _artifact("skill:/s/a", 0.5, seed=0, config={"tier": {"name": "triage", "overrides": {}}})}
    second = {"skill:/s/a": _artifact("skill:/s/a", 0.6, seed=2, config={"tier": {"name": "full", "overrides": {}}})}
    assert "tier" in " ".join(pair_runs(first, second, "lenient")["warnings"])


def test_a_constant_side_has_no_correlation_even_when_its_mean_does_not_round():
    from dokimi.eval.correlation import pearson

    assert pearson([0.1] * 3, [0.2, 0.5, 0.9]) is None
    assert pearson([0.2, 0.5, 0.9], [0.1] * 3) is None


def test_confidence_correlation_is_the_pearson_r_of_confidence_and_correctness():
    from types import SimpleNamespace

    from dokimi.novelty.measure import MeasureResult

    rows: Any = [SimpleNamespace(confidence=c, correct=lambda _mode, ok=ok: ok)
            for c, ok in ((0.1, False), (0.5, True), (0.9, True))]
    result = MeasureResult.__new__(MeasureResult)
    result.runs = rows
    assert result.confidence_correlation() == pytest.approx(0.866, abs=0.001)


@pytest.mark.parametrize("marker", [{"status": "partial"}, {"partial": {}}])
@pytest.mark.parametrize("side", [0, 1])
def test_partial_marker_excludes_either_member_of_a_pair(marker, side):
    ref = "skill:/s/a"
    runs = [{ref: _artifact(ref, 0.5, seed=seed)} for seed in (1, 2)]
    runs[side][ref].update(marker)
    result = pair_runs(runs[0], runs[1], "lenient")
    assert result["pairs"] == []
    assert result["unscored"] == [ref]
    assert any("partial" in warning for warning in result["warnings"])


@pytest.mark.parametrize("score", [1.7e308, -(10**400), float("inf")])
def test_unusable_k0_is_excluded_from_the_persisted_summary(tmp_path, score):
    first = {ref: _artifact(ref, 0.1) for ref in ("valid", "invalid")}
    first["invalid"]["k0"]["lenient"] = score
    second = {ref: _artifact(ref, 0.2, seed=1) for ref in first}
    _run_retest(tmp_path, _write_runs(tmp_path / "a", first), _write_runs(tmp_path / "b", second),
                "--out-dir", str(tmp_path / "out"))
    report = json.loads((tmp_path / "out" / "retest.json").read_text())
    json.dumps(report, allow_nan=False)
    assert report["unscored"] == ["invalid"]
    assert [row["ref"] for row in report["items"]] == ["valid"]


def test_a_strict_retest_pairs_and_summarises_the_strict_scores():
    first = {ref: _artifact(ref, 0.8) for ref in ("skill:/s/a", "skill:/s/b")}
    second = {ref: _artifact(ref, 0.8, seed=2) for ref in first}
    first["skill:/s/a"]["k0"]["strict"] = 0.1
    second["skill:/s/a"]["k0"]["strict"] = 0.2
    first["skill:/s/b"]["k0"]["strict"] = 0.2
    second["skill:/s/b"]["k0"]["strict"] = 0.5
    paired = pair_runs(first, second, "strict")
    assert retest_summary(paired["pairs"], "strict")["mean_shift"] == pytest.approx(0.2)

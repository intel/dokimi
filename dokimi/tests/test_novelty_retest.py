# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Test-retest noise of the novelty score: the floor, the pairing, and the command that reports it.

The corpus pairs are the ten S2 skills of the 11-09 retest (REPORT.md, S3m section): the first
run used seed 0, the retest seed 2 and a fresh cache. No model is called here.
"""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd
from dokimi.novelty.retest import load_runs, pair_runs, render_markdown, retest_summary
from dokimi.utils import InputError
from tests.cli_paths import novelty_argv

# (seed 0, seed 2) headline lenient K0 of the ten S2 skills, from out/s3m_summary.txt.
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


def _artifact(ref, lenient, *, seed=0, model="m", partial=False):
    doc = {
        "artifact": {"ref": ref},
        "k0": {"lenient": lenient, "strict": lenient / 2},
        "model": model,
        "run_config": {"seed": seed},
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
    assert summary["n_below_floor"] == len(summary["below_floor"]) > 0


def test_a_comparison_below_the_floor_is_flagged_and_one_above_is_not():
    # Diffs of +0.01, -0.01 and -0.01 give a floor near 0.023; items 0.10 and 0.12 are inside
    # it, items 0.10 and 0.50 are not.
    pairs = _pairs([(0.10, 0.11), (0.12, 0.11), (0.50, 0.49)])
    summary = retest_summary(pairs)
    flagged = {(f["a"], f["b"]) for f in summary["below_floor"]}
    assert ("skill:/s/0", "skill:/s/1") in flagged
    assert ("skill:/s/0", "skill:/s/2") not in flagged
    assert ("skill:/s/1", "skill:/s/2") not in flagged


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
    assert "partial" in text


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
    assert saved["n_items"] == 10
    assert saved["pearson_r"] == pytest.approx(0.86, abs=0.005)
    report = (tmp_path / "out" / "retest.md").read_text(encoding="utf-8")
    assert "Noise floor on `lenient`" in report
    assert "Comparisons below the floor" in report


def test_the_report_says_when_the_floor_is_not_measured():
    summary = retest_summary(_pairs([(0.3, 0.4)]))
    assert "Noise floor not measured" in render_markdown(summary, warnings=[])


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


def test_the_command_refuses_runs_with_no_item_in_common(tmp_path):
    first = _write_runs(tmp_path / "a", {"skill:/s/one": _artifact("skill:/s/one", 0.5)})
    second = _write_runs(tmp_path / "b", {"skill:/s/two": _artifact("skill:/s/two", 0.5)})
    with pytest.raises(InputError, match="no item scored in both runs"):
        _run_retest(tmp_path, first, second)

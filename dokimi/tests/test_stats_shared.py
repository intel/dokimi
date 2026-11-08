# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One bootstrap and one paired delta for the whole package (eval/stats.py).

The tool-preference cluster CI used to resample with its own RNG stream. It now calls ``eval.stats.bootstrap_ci``;
these tests pin the wiring (resample counts, seed, output shape) and the golden values so a second resampler cannot
come back unnoticed. Novelty's CI is out of scope here: main's #418 clusters its paraphrases by atom.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import dokimi
from dokimi.eval import arm_runner
from dokimi.eval import stats
from dokimi.records import BASELINE, CONTEXT
from dokimi.tool_preference import _boot as group_boot

PKG = Path(dokimi.__file__).parent


def test_group_boot_is_the_shared_cluster_bootstrap_at_2000_resamples():
    groups = {f"g{i}": (i * 0.37) % 1 for i in range(30)}
    shared = stats.bootstrap_ci(list(groups.values()), n_resamples=2000, seed=0)
    out = group_boot(groups)
    assert out == {"mean": shared["mean"], "ci95": [shared["lo"], shared["hi"]], "n_groups": 30}
    # Golden value for the integer seed 0 stream.
    assert out["ci95"] == [0.3893, 0.599]


def test_group_boot_of_nothing_and_of_one_group():
    assert group_boot({}) == {"mean": None, "ci95": None, "n_groups": 0}
    assert group_boot({"a": 0.5}) == {"mean": 0.5, "ci95": [0.5, 0.5], "n_groups": 1}


def test_paired_delta_is_the_rounded_mean_of_the_complete_pairs_only():
    evals = [
        {
            "question_id": "q1",
            "question_text": "Q1?",
            BASELINE: {"aggregate": 40},
            CONTEXT: {"aggregate": 70},
        },
        {
            "question_id": "q2",
            "question_text": "Q2?",
            BASELINE: {"aggregate": 50},
            CONTEXT: {"aggregate": 55},
        },
        # Scored by the baseline only: must not enter the delta.
        {"question_id": "q3", "question_text": "Q3?", BASELINE: {"aggregate": 90}, CONTEXT: None},
    ]
    assert stats.paired_delta(evals, BASELINE, CONTEXT) == 17.5
    assert stats.paired_delta(evals, BASELINE, BASELINE) == 0.0
    assert stats.paired_delta([], BASELINE, CONTEXT) is None


def test_paired_delta_agrees_with_paired_summary_before_rounding():
    evals = [
        {
            "question_id": f"q{i}",
            "question_text": "Q?",
            BASELINE: {"aggregate": 10 * i},
            CONTEXT: {"aggregate": 10 * i + (i % 3)},
        }
        for i in range(1, 8)
    ]
    summary = stats.paired_summary(evals)
    assert stats.paired_delta(evals, BASELINE, CONTEXT) == round(summary.delta_mean, 1)


def test_the_arm_runner_reads_the_delta_from_the_stats_module():
    assert arm_runner.arm_stats.paired_delta is stats.paired_delta


def _bootstrap_definitions() -> set[tuple[str, str]]:
    """Every function in the package whose name says it resamples."""
    found: set[tuple[str, str]] = set()
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                "bootstrap" in node.name or node.name.lstrip("_") == "boot"
            ):
                found.add((path.relative_to(PKG).as_posix(), node.name))
    return found


def test_the_package_has_no_second_bootstrap_resampler():
    # A new resampler must be added here on purpose, after checking it cannot be a call to eval.stats.bootstrap_ci.
    assert _bootstrap_definitions() == {
        ("eval/stats.py", "bootstrap_ci"),
        ("tool_preference.py", "_boot"),
    }


@pytest.mark.parametrize("module", ["eval/arm_summary.py", "repos/compare.py"])
def test_no_module_redefines_paired_delta_or_paired_summary(module):
    tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "paired_delta" not in names
    assert "paired_summary" not in names

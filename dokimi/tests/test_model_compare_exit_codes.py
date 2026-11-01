# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Runs that cannot be compared are refused with exit 2, as a missing --treatment-arm is.

The refusals are input errors: the command was given runs it cannot pool. Each raises InputError,
which cli.main reports with exit 2; a message match alone would not pin the status.
"""

from __future__ import annotations

import json
import sys

import pytest

from dokimi import cli
from dokimi.report.model_compare import check_run_consistency
from dokimi.utils import InputError
from tests.test_model_compare import _V1, _run_pair, _stamped

_NULL_ARM = {"baseline_arm": "baseline", "answers": [
    {"question_id": "q1", "question_text": "Q?", "arms": {"baseline": {"answer": "b"}, "docs": None}}]}
_OTHER_ARM = {"baseline_arm": "baseline", "answers": [
    {"question_id": "q1", "question_text": "Q?", "arms": {"baseline": {"answer": "b"}, "skill": {"answer": "s"}}}]}

REFUSED_RUNS = {
    "baseline-arm": ([_run_pair("a", baseline="no-context"), _run_pair("b", baseline="baseline")], "different baseline_arm"),
    "same-model": ([_run_pair("a", model="same"), _run_pair("b", model="same")], "--pool-replicates"),
    "treatment-arms": ([("a", _NULL_ARM), ("b", _OTHER_ARM)], "different treatment arms"),
    "judge-context": ([_stamped("a", None), _stamped("b", _V1)], "judge_context"),
    "mixed-run": ([_stamped("a", _V1 + "+snippet_fallback"), _stamped("b", _V1)], "pools with nothing"),
}


@pytest.mark.parametrize("name", REFUSED_RUNS)
def test_runs_that_cannot_be_compared_raise_input_error(name):
    runs, message = REFUSED_RUNS[name]
    with pytest.raises(InputError, match=message):
        check_run_consistency(runs, "regular")


@pytest.mark.parametrize("name", ["judge-context", "mixed-run"])
def test_inconsistent_run_files_exit_2_through_cli_main(name, tmp_path, monkeypatch, capsys):
    runs, message = REFUSED_RUNS[name]
    paths = []
    for run_id, data in runs:
        path = tmp_path / f"{run_id}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        paths.append(str(path))
    out = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["dokimi", "report", "model-compare", "--regular-runs", *paths,
                                      "--run-ids", ",".join(run_id for run_id, _ in runs), "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


def _three_arm_run(path):
    """Two non-baseline arms (docs, skill) on every question; baseline is declared."""
    evaluations = [{"question_id": f"q{i}", "question_text": "q", "category": "", "difficulty": "easy",
                    "persona": "", "scores": {"baseline": {"aggregate": 50.0}, "docs": {"aggregate": 60.0},
                                              "skill": {"aggregate": 55.0}}} for i in range(3)]
    path.write_text(json.dumps({"baseline_arm": "baseline", "evaluations": evaluations}), encoding="utf-8")
    return path


def _three_arm_legacy_run(path):
    """The same arms in the legacy per-answer format: no top-level ``evaluations`` key."""
    answers = [{"question_id": f"q{i}", "question_text": "q", "arms": {
        "baseline": {"answer": "b"}, "docs": {"answer": "d"}, "skill": {"answer": "s"}}} for i in range(3)]
    path.write_text(json.dumps({"baseline_arm": "baseline", "answers": answers}), encoding="utf-8")
    return path


@pytest.mark.parametrize("writer", [_three_arm_run, _three_arm_legacy_run])
@pytest.mark.parametrize("extra, message", [
    ([], "multiple non-baseline arms"),
    (["--treatment-arm", "missing"], "'missing' not found"),
])
def test_treatment_arm_refusals_exit_2_through_cli_main(writer, extra, message, tmp_path, monkeypatch, capsys):
    run = writer(tmp_path / "run.json")
    out = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["dokimi", "report", "model-compare", "--regular-runs", str(run),
                                      "--run-ids", "a", "--out", str(out), *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not out.exists()

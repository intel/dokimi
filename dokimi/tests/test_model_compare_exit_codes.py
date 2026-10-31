# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Runs that cannot be compared are refused with exit 2, as a missing --treatment-arm is.

The refusals are input errors: the command was given runs it cannot pool. A message match alone
would pass if a refusal went back to the default exit status 1, so each case also pins the code.
"""

from __future__ import annotations

import pytest

from dokimi.report.model_compare import check_run_consistency
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
def test_runs_that_cannot_be_compared_exit_2(name):
    runs, message = REFUSED_RUNS[name]
    with pytest.raises(SystemExit, match=message) as exc:
        check_run_consistency(runs, "regular")
    assert exc.value.code == 2

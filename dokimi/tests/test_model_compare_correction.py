# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The run family correction of the model comparison keeps the exact p-values."""

from __future__ import annotations

from dokimi.eval import stats
from dokimi.report.model_compare import _adjusted_significance


def test_adjusted_significance_corrects_exact_p_only_across_testable_runs():
    def run(deltas):
        return [{"with_docs": 1.0 + d, "without_docs": 1.0} for d in deltas]

    scores = {
        "a": run([1.0, 2.5, 3.1, 4.7, 5.2, 6.0, 7.1]),
        "b": run([0.3, 0.5]),  # untestable: fewer than MIN_PAIRS_FOR_TEST pairs
        "c": run([2.0, 1.5, 2.2, 1.9, 2.4, 1.1, 2.8]),
    }
    sigs, n_tested = _adjusted_significance(["a", "b", "c"], scores, "holm")

    assert n_tested == 2
    assert sigs["b"] is None  # significance_test is None below MIN_PAIRS_FOR_TEST
    # sigs are rounded for display (p_ttest included), so take the raw p-values from paired_test.
    raw = [stats.paired_test([1.0 + d for d in ds], [1.0] * len(ds), rounded=False)["p_ttest"]
           for ds in ([1.0, 2.5, 3.1, 4.7, 5.2, 6.0, 7.1], [2.0, 1.5, 2.2, 1.9, 2.4, 1.1, 2.8])]
    assert None not in raw
    exact = stats.adjust_pvalues(raw, method="holm", rounded=False)
    assert sigs["a"]["exact"]["p_adjusted"] == exact[0]
    assert sigs["c"]["exact"]["p_adjusted"] == exact[1]

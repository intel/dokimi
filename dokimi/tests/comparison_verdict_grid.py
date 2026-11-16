# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The input grid the comparison-verdict golden is captured over (``test_comparison_verdict_golden.py``).

Pure data: the golden file holds what the callers returned for each case on the base tree, and the test
replays the same cases through the same callers. Both sides enumerate the grid from this module.
"""

from __future__ import annotations

import numpy as np

from dokimi.eval.sign_flip import ALPHA, EPS

NAN = float("nan")
INF = float("inf")

# subjects.evidence._verdict(delta, sig): a delta, and the scorecard's sig dict.
EVIDENCE_DELTAS = [None, -INF, -1.0, -0.0001, -1e-12, -0.0, 0.0, 1e-12, 0.0001, 0.5, INF, NAN]
EVIDENCE_SIGS = [{}, {"significant_adjusted": True}, {"significant_adjusted": False},
                 {"significant_adjusted": None}, {"significant_adjusted": "true"},
                 {"significant_adjusted": 1}, {"significant_adjusted": "false"}, {"significant_adjusted": 0}]


def evidence_cases() -> list[tuple[float | None, dict]]:
    return [(delta, dict(sig)) for delta in EVIDENCE_DELTAS for sig in EVIDENCE_SIGS]


# repos.ledger._verdict(entry, alpha): the entry fields the verdict reads, and the ledger's alpha.
LEDGER_MEANS = [-0.5, -2 * EPS, -EPS, -EPS / 2, 0.0, EPS / 2, EPS, 2 * EPS, 0.5, NAN]
LEDGER_P = [None, 0.0001, 0.2, np.float64(0.2)]  # a NumPy scalar is a p a stats helper may return
LEDGER_P_HOLM = [None, 0.0499, 0.05, 0.06, 0.5, np.float64(0.0499)]
LEDGER_ALPHAS = [ALPHA, 0.01]


def ledger_cases() -> list[tuple[dict, float]]:
    cases = []
    for error in (None, "grader crashed"):
        for paired in (True, False):
            for unmeasured in (0, 2):
                for mixed in (False, True):
                    for p in LEDGER_P:
                        for p_holm in LEDGER_P_HOLM:
                            for mean in LEDGER_MEANS:
                                for alpha in LEDGER_ALPHAS:
                                    entry = {"error": error, "paired": paired, "unmeasured": unmeasured,
                                             "mixed": mixed, "p": p, "p_holm": p_holm, "baseline": "base",
                                             "changed": 3, "delta": {"mean": mean}}
                                    cases.append((entry, alpha))
    return cases

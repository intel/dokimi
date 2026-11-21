# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Golden pins of the two callers of :func:`dokimi.eval.comparison_verdict.decide`: the verdict a skill
evidence row carries, and the verdict and reason a rule ledger entry carries. The golden was captured from
the tree before the seam existed, over the grid in ``comparison_verdict_grid``. A refactor of the seam must
leave every value unchanged: each one is written to a JSON document a reader or a script takes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from comparison_verdict_grid import evidence_cases, ledger_cases
from dokimi.eval.comparison_verdict import ComparisonVerdict, decide
from dokimi.repos.ledger import _verdict as ledger_verdict
from dokimi.subjects.evidence import _verdict as evidence_verdict

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "comparison_verdict_golden.json").read_text(encoding="utf-8"))
CODE = {"K": "keep", "R": "review", "I": "insufficient-evidence"}


def test_evidence_verdicts_match_the_golden():
    got = [evidence_verdict(delta, sig) for delta, sig in evidence_cases()]
    assert got == GOLDEN["evidence"]


def test_ledger_verdicts_and_reasons_match_the_golden():
    cases = ledger_cases()
    reasons = GOLDEN["ledger_reasons"]
    assert len(GOLDEN["ledger_reason_index"]) == len(cases) == len(GOLDEN["ledger_verdicts"])
    for i, ((entry, alpha), idx, code) in enumerate(
            zip(cases, GOLDEN["ledger_reason_index"], GOLDEN["ledger_verdicts"], strict=True)):
        verdict, reason = ledger_verdict(dict(entry), alpha)
        assert (verdict, reason) == (CODE[code], reasons[idx]), f"case {i}: {entry} alpha={alpha}"


def test_decide_reads_only_a_significant_nonzero_delta_by_its_sign():
    assert decide(0.2, True) is ComparisonVerdict.IMPROVED
    assert decide(-0.2, True) is ComparisonVerdict.REGRESSED
    assert decide(0.2, False) is ComparisonVerdict.INCONCLUSIVE
    assert decide(None, True) is ComparisonVerdict.INCONCLUSIVE
    assert decide(0.0, True) is ComparisonVerdict.INCONCLUSIVE


def test_decide_reads_a_numpy_bool_flag_by_its_truth_value():
    assert decide(0.2, np.bool_(True)) is ComparisonVerdict.IMPROVED
    assert decide(0.2, np.bool_(False)) is ComparisonVerdict.INCONCLUSIVE


def test_decide_eps_is_an_open_bound_on_the_magnitude():
    assert decide(0.1, True, eps=0.1) is ComparisonVerdict.INCONCLUSIVE
    assert decide(-0.1, True, eps=0.1) is ComparisonVerdict.INCONCLUSIVE
    assert decide(0.11, True, eps=0.1) is ComparisonVerdict.IMPROVED
    assert decide(-0.11, True, eps=0.1) is ComparisonVerdict.REGRESSED


def test_verdict_values_are_the_evidence_vocabulary():
    assert [v.value for v in ComparisonVerdict] == ["improved", "regressed", "inconclusive"]

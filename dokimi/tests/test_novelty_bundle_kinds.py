"""A stored bundle row whose ``kind`` is not a string must not take down its readers."""
from __future__ import annotations

import pytest

from dokimi.gate.novelty_gate import evaluate_novelty
from dokimi.novelty import report
from dokimi.novelty.bundle import Coverage, excluded_of


def _coverage_check(record):
    verdict = evaluate_novelty({"bundle": record})
    return next(c for c in verdict.checks if c.name == "artifact_coverage")


COUNTS = {"entry_point_words": 100, "resource_words_on_disk": 100, "resource_words_measured": 0}


@pytest.mark.parametrize("kind", [["budget"], {"a": 1}])
def test_a_schema_valid_row_with_an_odd_kind_does_not_take_down_the_reader(kind):
    # excluded items are `{"type": "object"}` in novelty.v1, so any `kind` validates.
    record = dict(COUNTS, include_resources=True, n_files=1,
                  excluded=[{"path": "a.md", "reason": "over budget", "kind": kind}])
    check = _coverage_check(record)                      # falls back to the reason
    assert check.measured is True
    assert report._render_bundle({"bundle": record})
    assert Coverage.from_record(record) is not None
    assert excluded_of(record["excluded"], "gap") == record["excluded"]



@pytest.mark.parametrize("words", ["n/a", [1], {"a": 1}])
def test_non_numeric_words_in_decision_row_does_not_crash(words):
    # Regression: THREAD 0 -- non-numeric words should be treated as 0
    record = dict(COUNTS, include_resources=True, n_files=1,
                  excluded=[{"path": "evals/a.md", "reason": "role policy", "kind": "policy", "words": words}])
    check = _coverage_check(record)
    assert check.value is not None  # Should not crash
    cov = Coverage.from_record(record)
    assert cov is not None
    assert cov.policy_excluded_words == 0  # Non-numeric falls back to 0

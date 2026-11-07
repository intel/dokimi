# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A judged reply with no row this stage can use is split and not cached, and is reported.

intel/dokimi#219. A value-judge reply whose rows are all malformed used to be cached and
counted as skipped; a conflict reply whose rows could not be used read as "checked, no
conflicts", and its dropped rows were not counted; and a retry for unusable rows got the
``--max-tokens`` hint that only a cut-off reply needs.
"""

from __future__ import annotations

import json
import re

import pytest

from dokimi.novelty.atoms import Atom
from dokimi.novelty.classify import ClassifyReport, detect_conflicts, judge_value
from dokimi.novelty.client import LLMClient


def _atoms(n: int) -> list[Atom]:
    return [Atom(statement=f"Setting {i} defaults to {i * 10}.", question="q", answer="a",
                 span_ids=(f"s{i:04d}",)) for i in range(1, n + 1)]


def _numbers(prompt: str) -> list[int]:
    return [int(m) for m in re.findall(r"^(\d+)\. ", prompt, flags=re.M)]


def _cache_verdicts(monkeypatch, reply_for) -> list[bool]:
    """Each call's reply, and whether the response cache would store it (its accept test)."""
    seen: list[bool] = []

    def text(self, prompt, *, system=None, accept=None, draw=0):
        reply = reply_for(prompt)
        seen.append(True if accept is None else bool(accept(reply)))
        return reply

    monkeypatch.setattr(LLMClient, "text", text)
    return seen


MALFORMED_VERDICT_ROWS = [
    lambda n: {"n": n, "needed": "false"},
    lambda n: {"n": n, "verdict": "no"},
    lambda n: {"needed": True},
]


@pytest.mark.parametrize("row", MALFORMED_VERDICT_ROWS, ids=["needed-string", "needed-missing", "n-missing"])
def test_a_judge_reply_with_no_usable_row_is_split_and_never_cached(monkeypatch, row):
    atoms = _atoms(10)
    seen = _cache_verdicts(monkeypatch, lambda p: json.dumps([row(n) for n in _numbers(p)]))
    report = ClassifyReport()
    verdicts = judge_value(atoms, client=LLMClient(model="x"), task="t", batch=5, report=report)
    assert verdicts == {}
    assert seen and not any(seen)  # no reply was offered to the cache
    assert report.retried_batches > 0  # split and re-asked, not taken as the answer
    assert sorted(report.unjudged) == sorted(a.id for a in atoms)
    assert report.unjudged_skipped == 0 and report.unjudged_malformed == 0
    assert report.unjudged_unusable == len(atoms)
    assert any(w.endswith("(10 whose reply had no usable verdict row)")
               for w in report.warnings(len(atoms)))
    assert report.value_error == ""  # an unusable reply has its own cause, not "calls that failed"


def test_an_empty_judge_reply_for_a_batch_is_split_and_never_cached(monkeypatch):
    atoms = _atoms(4)
    seen = _cache_verdicts(monkeypatch, lambda p: "[]")
    report = ClassifyReport()
    assert judge_value(atoms, client=LLMClient(model="x"), task="t", batch=4, report=report) == {}
    assert seen and not any(seen)
    assert report.unjudged_unusable == len(atoms) and report.unjudged_skipped == 0


def test_a_transport_error_is_not_overwritten_by_a_later_unusable_reply(monkeypatch):
    atoms = _atoms(2)

    def reply_for(prompt):
        # the first atom's call of one fails in transport; the second's reply is unusable
        if _numbers(prompt) == [1] and "Setting 1 " in prompt and "Setting 2 " not in prompt:
            raise RuntimeError("503 service unavailable")
        return json.dumps([{"n": n, "needed": "no"} for n in _numbers(prompt)])

    _cache_verdicts(monkeypatch, reply_for)
    report = ClassifyReport()
    judge_value(atoms, client=LLMClient(model="x"), task="t", batch=2, report=report)
    assert "503" in report.value_error


def test_a_judge_reply_with_one_usable_row_is_still_cached(monkeypatch):
    atoms = _atoms(3)
    seen = _cache_verdicts(monkeypatch, lambda p: json.dumps(
        [{"n": 1, "needed": True}, {"n": 2, "needed": "false"}, {"n": 3, "needed": False}]))
    report = ClassifyReport()
    verdicts = judge_value(atoms, client=LLMClient(model="x"), task="t", report=report)
    assert sorted(verdicts) == sorted([atoms[0].id, atoms[2].id])
    assert seen == [True] and report.calls == 1


def test_a_malformed_judge_row_is_counted_apart_from_a_row_the_judge_skipped():
    atoms = _atoms(3)
    reply = json.dumps([{"n": 1, "needed": "false"}, {"n": 2, "needed": True}])
    report = ClassifyReport()
    verdicts = judge_value(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (reply, None)),
        task="t", report=report,
    )
    assert set(verdicts) == {atoms[1].id}
    assert (report.unjudged_skipped, report.unjudged_malformed) == (1, 1)
    [warning] = report.warnings(len(atoms))
    assert warning.endswith("(1 skipped by the judge's reply; 1 with a malformed verdict row)")


@pytest.mark.parametrize("rows", [
    [{"a": 1, "b": 1, "why": "w"}],
    [{"x": 1}],
    ["junk", 3],
    [{"a": 1, "b": 99}],
    [{"a": float("inf"), "b": 2}],
    [{"a": 1.5, "b": 2}],
    [{"a": True, "b": 2}],
], ids=["self-pair", "no-a-b", "non-dict", "out-of-range", "infinity", "fraction", "bool"])
def test_a_conflict_reply_with_no_usable_row_is_not_cached_and_atoms_are_unchecked(monkeypatch, rows):
    atoms = _atoms(4)
    seen = _cache_verdicts(monkeypatch, lambda p: json.dumps({"conflicts": rows}))
    report = ClassifyReport()
    assert detect_conflicts(atoms, client=LLMClient(model="x"), report=report) == []
    assert seen and not any(seen)
    assert report.unchecked == [a.id for a in atoms]
    assert report.unchecked_unusable == len(atoms) and report.pairs_compared == 0
    assert any(w.startswith("4 of 4 atoms were not checked for conflicts")
               for w in report.warnings(len(atoms)))


def test_conflict_rows_that_cannot_be_used_are_counted_and_warned():
    atoms = _atoms(4)
    reply = json.dumps({"conflicts": [
        {"a": 1, "b": 2, "why": "x"}, {"a": 3, "b": 3, "why": "self"}, {"x": 1},
    ]})
    report = ClassifyReport()
    conflicts = detect_conflicts(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (reply, None)), report=report,
    )
    assert conflicts == [{"a": atoms[0].id, "b": atoms[1].id, "why": "x"}]
    assert report.conflict_rows_dropped == 2
    [warning] = report.warnings(len(atoms))
    assert warning.startswith("2 conflict rows could not be used")


def test_a_judge_retry_for_unusable_rows_does_not_blame_max_tokens():
    atoms = _atoms(4)
    report = ClassifyReport()
    judge_value(
        atoms,
        client=LLMClient(model="x", transport=lambda p, s: (
            json.dumps([{"foo": 1} for _ in _numbers(p)]), None)),
        task="t", batch=4, report=report,
    )
    assert report.retried_batches > 0
    [retry] = [w for w in report.warnings(len(atoms)) if "re-asked" in w]
    assert "re-asked 3 unusable batches as halves" in retry
    assert "--max-tokens" not in retry


def test_a_judge_retry_for_a_cut_off_reply_still_advises_max_tokens():
    def cut_above_one(prompt, system):
        numbers = _numbers(prompt)
        if len(numbers) > 1:
            return '[{"n": 1, "needed": tr', None
        return json.dumps([{"n": 1, "needed": True}]), None

    atoms = _atoms(4)
    report = ClassifyReport()
    judge_value(atoms, client=LLMClient(model="x", transport=cut_above_one),
                task="t", batch=4, report=report)
    assert report.unjudged == [] and report.retried_batches == 3
    [retry] = [w for w in report.warnings(len(atoms)) if "re-asked" in w]
    assert "re-asked 3 unparseable batches as halves" in retry
    assert retry.endswith("a higher --max-tokens avoids the extra calls")


def test_a_judge_row_with_an_overlong_number_does_not_discard_the_usable_rows(monkeypatch):
    atoms = _atoms(2)
    seen = _cache_verdicts(monkeypatch, lambda p: json.dumps(
        [{"n": "9" * 5000, "needed": True}, {"n": 1, "needed": True}]))
    report = ClassifyReport()
    verdicts = judge_value(atoms, client=LLMClient(model="x"), task="t", report=report)
    assert set(verdicts) == {atoms[0].id}
    assert seen == [True] and report.calls == 1


def test_a_conflict_with_a_whole_number_written_as_float_or_padded_string_is_kept():
    atoms = _atoms(3)
    reply = json.dumps({"conflicts": [{"a": 1.0, "b": " 2", "why": "x"}]})
    report = ClassifyReport()
    conflicts = detect_conflicts(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (reply, None)), report=report,
    )
    assert conflicts == [{"a": atoms[0].id, "b": atoms[1].id, "why": "x"}]
    assert report.conflict_rows_dropped == 0


def test_an_atom_alone_in_a_conflict_batch_is_warned_apart_from_failed_calls(monkeypatch):
    atoms = _atoms(3)
    _cache_verdicts(monkeypatch, lambda p: json.dumps({"conflicts": []}))
    report = ClassifyReport()
    assert detect_conflicts(atoms, client=LLMClient(model="x"), batch=2, report=report) == []
    assert report.unchecked_alone == 1 and report.unchecked_unusable == 0
    assert any(w.startswith("1 of 3 atoms were not checked for conflicts")
               and "1 alone in a batch, with no pair to check" in w
               for w in report.warnings(len(atoms)))

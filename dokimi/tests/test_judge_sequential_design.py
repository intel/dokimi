# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sequential gate's hypotheses, its settled FAIL, and the intact-score retry."""

import hashlib
import json
import random
import threading
import time

import pytest

from dokimi.cli import build_parser
from dokimi.eval.degrade import build_pairs
from dokimi.eval.judge_sequential import SPRT_DELTA, run_sequential, sprt, sprt_bounds
from dokimi.eval.validity_scoring import INTACT_ATTEMPTS, _score_pairs
from tests.test_judge_reliability import _many_answers


def _decide(gate, accuracy, rng, cap=120, batch=10):
    outcomes = []
    while len(outcomes) < cap:
        outcomes += [1.0 if rng.random() < accuracy else 0.0 for _ in range(batch)]
        decision, _ = sprt(outcomes, gate)
        if decision != "continue":
            return decision
    return "undecided"


def test_the_default_gate_keeps_its_hypotheses_exactly():
    assert sprt_bounds(0.8) == (max(1e-6, 0.8 - SPRT_DELTA), 0.8 + SPRT_DELTA) == pytest.approx((0.72, 0.88))


@pytest.mark.parametrize("gate", [0.03, 0.1, 0.5, 0.8, 0.9, 0.91, 0.92, 0.95, 0.96, 0.97])
def test_the_hypotheses_are_symmetric_about_the_gate_and_inside_the_ceiling(gate):
    p0, p1 = sprt_bounds(gate)
    assert gate - p0 == pytest.approx(p1 - gate) and 0.01 - 1e-9 <= p0 < gate < p1 <= 0.99 + 1e-9
    if 0.1 <= gate <= 0.91:  # the old construction was already symmetric and in range here
        assert (p0, p1) == (gate - SPRT_DELTA, gate + SPRT_DELTA)


@pytest.mark.parametrize("gate", [0.0, 0.029, 0.971, 0.98, 0.99, 1.0, 1.2, -0.5])
def test_a_gate_the_test_cannot_decide_is_refused(gate):
    with pytest.raises(ValueError, match="--min-accuracy"):
        sprt_bounds(gate)
    called = []
    with pytest.raises(ValueError):
        run_sequential(_many_answers(20), lambda *a: called.append(a) or 1.0, min_accuracy=gate)
    assert not called


def test_the_command_exits_2_before_any_judge_call_for_an_undecidable_gate(tmp_path, capsys):
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")
    args = build_parser().parse_args(
        ["eval", "judge", "validate", "--answers", str(answers), "--sequential", "--min-accuracy", "0.99"]
    )
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "--min-accuracy 0.99" in capsys.readouterr().err


def test_error_rates_hold_at_a_high_gate_where_the_upper_clamp_used_to_bite():
    """Gate 0.95: a 0.99 judge failed 26% of runs when H1 was clamped to 0.999999."""
    rng = random.Random(7)
    p0, p1 = sprt_bounds(0.95)
    good = [_decide(0.95, p1, rng) for _ in range(600)]
    bad = [_decide(0.95, p0, rng) for _ in range(600)]
    assert good.count("FAIL") / 600 < 0.05 and bad.count("PASS") / 600 < 0.05
    assert good.count("PASS") / 600 > 0.9 and bad.count("FAIL") / 600 > 0.9


def test_the_report_prints_the_hypotheses_actually_tested():
    answers = _many_answers(30)
    intact = {a["arms"]["baseline"]["answer"] for a in answers}

    def scorer(lib, q, ans):
        return 90.0 if ans in intact else 10.0

    for gate, text in ((0.8, "SPRT 0.72 vs 0.88, alpha=beta=0.05"), (0.95, "SPRT 0.91 vs 0.99, alpha=beta=0.05")):
        report = run_sequential(answers, scorer, max_bases=30, variants=["truncate_20"], min_accuracy=gate)
        sprt_info = report.summary()["sprt"]
        assert sprt_info["p0"] == pytest.approx(gate - (0.08 if gate == 0.8 else 0.04))
        assert sprt_info["p1"] == pytest.approx(gate + (0.08 if gate == 0.8 else 0.04))
        assert text in report.format_summary()


def test_a_sequential_fail_settles_the_run_beside_a_variant_still_open():
    answers = _many_answers(60)
    variants = ["truncate_20", "strip_code"]
    pairs = build_pairs(answers, noop={}, n_bases=60, seed=0, variants=variants, library="L")
    variant_of = {p["degraded"]: p["variant"] for p in pairs}
    intact = {p["intact"] for p in pairs}
    seen = {v: 0 for v in variants}

    def scorer(lib, q, ans):
        if ans in intact:
            return 90.0
        v = variant_of[ans]
        i, seen[v] = seen[v], seen[v] + 1
        wrong = (i % 10 < 3) if v == "strip_code" else (i % 5 == 0)  # accuracy 0.70 and 0.80
        return 95.0 if wrong else 10.0

    report = run_sequential(answers, scorer, max_bases=60, variants=variants)
    s = report.summary()
    assert s["per_variant"]["strip_code"]["sequential"]["decision"] == "FAIL"
    assert s["per_variant"]["truncate_20"]["sequential"]["decision"] == "continue"
    assert s["passed"] is False and s["conclusive"] is True
    assert s["inconclusive_variants"] == ["truncate_20"]
    assert report.format_summary().rstrip().endswith("RESULT: FAIL")


def test_an_open_variant_with_no_decision_anywhere_is_still_inconclusive():
    """Only a decided FAIL settles the run: an undecided variant below the gate does not."""
    answers = _many_answers(20)
    intact = {a["arms"]["baseline"]["answer"] for a in answers}
    toggle = {"n": 0}

    def scorer(lib, q, ans):
        if ans in intact:
            return 90.0
        toggle["n"] += 1
        return 95.0 if toggle["n"] % 5 == 0 else 10.0  # about 0.80: inside the band

    s = run_sequential(answers, scorer, max_bases=20, variants=["truncate_20"]).summary()
    assert s["per_variant"]["truncate_20"]["sequential"]["decision"] == "continue"
    assert s["conclusive"] is False


def _flaky(rate):
    """Fails the n-th call on a (question, answer) with probability ``rate``, whatever the thread order.

    Keyed on the question too: one answer text can appear in several pairs
    (``mismatched_answer`` reuses another base's intact answer, ``truncate_20``
    of short answers collides), and a count shared across pairs would make
    which pair draws the failing call depend on thread order. The jitter
    shuffles that order so the concurrency test can see it.
    """
    counts, lock = {}, threading.Lock()

    def scorer(lib, q, ans):
        time.sleep(random.random() * 0.001)
        with lock:
            n = counts[(q, ans)] = counts.get((q, ans), 0) + 1
        if int(hashlib.md5(f"{q}|{ans}|{n}".encode(), usedforsecurity=False).hexdigest(), 16) % 1000 < rate * 1000:
            raise RuntimeError("throttle")
        return float(len(ans) % 97)

    return scorer


def _errors(concurrency, rate=0.2):
    pairs = build_pairs(_many_answers(40), noop={}, n_bases=40, seed=0, variants=None, library="L")
    rows = _score_pairs(_flaky(rate), pairs, {}, concurrency=concurrency)
    return frozenset((r.base_id, r.variant) for r in rows if r.error)


def test_a_failed_intact_score_is_retried_before_its_variants_are_scored():
    answers = _many_answers(10)
    variants = ["truncate_20", "strip_code", "shuffle_sentences"]
    pairs = build_pairs(answers, noop={}, n_bases=1, seed=0, variants=variants, library="L")
    intact_answer, calls = pairs[0]["intact"], []

    def scorer(lib, q, ans):
        calls.append(ans)
        if ans == intact_answer and calls.count(ans) == 1:
            raise RuntimeError("throttle")
        return 90.0 if ans == intact_answer else 10.0

    rows = _score_pairs(scorer, pairs, {}, concurrency=1)
    assert [r.error for r in rows] == [None] * len(pairs)
    assert calls.count(intact_answer) == 2


def test_an_intact_score_that_keeps_failing_errors_its_variants_after_a_bounded_number_of_tries():
    answers = _many_answers(10)
    pairs = build_pairs(answers, noop={}, n_bases=1, seed=0, variants=None, library="L")
    intact_answer, calls = pairs[0]["intact"], []

    def scorer(lib, q, ans):
        calls.append(ans)
        if ans == intact_answer:
            raise RuntimeError("down")
        return 10.0

    rows = _score_pairs(scorer, pairs, {}, concurrency=3)
    assert all(r.error and "down" in r.error for r in rows)
    assert calls.count(intact_answer) == INTACT_ATTEMPTS and len(calls) == INTACT_ATTEMPTS


def test_which_pairs_error_does_not_depend_on_concurrency_or_thread_order():
    serial = _errors(1)
    assert serial and all(_errors(8) == serial for _ in range(4))

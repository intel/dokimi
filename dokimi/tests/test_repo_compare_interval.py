# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare` intervals: every shift the sign-flip test does not reject (dokimi.repos.compare_interval)."""

from __future__ import annotations

import json
import random

from dokimi.cli import build_parser
from dokimi.eval.stats import mean_ci
from dokimi.repos import compare_interval
from dokimi.repos.compare import paired_summary, sign_flip_p
from dokimi.repos.compare_interval import ShiftTest, format_interval, sign_flip_interval

from test_repo_compare import _cell

# The metric:n_findings row of a 26-task review_replay of a dpnp PR (issue #292); its own deltas are not
# recoverable, so these reproduce it: mean +0.885, a t interval across 0, a sign-flip p just under 0.05.
N_FINDINGS_26 = [-1.0, 0.0, 1.0, 1.0, -1.0, 10.0, 1.0, -1.0, 0.0, -1.0, 3.0, 1.0, 3.0, 2.0, 1.0, 0.0, 0.0, 1.0, 2.0,
                 2.0, -3.0, 0.0, 0.0, 1.0, 2.0, -1.0]


def _pairs(deltas: list[float]) -> list[dict]:
    return [{"task": f"t{i}", "baseline": 0.0, "treatment": d, "delta": d} for i, d in enumerate(deltas)]


def _deltas(rng: random.Random, n: int, changed: int) -> list[float]:
    """``changed`` rates-like deltas of either sign (a few may cancel to a tie) and ties for the rest, shuffled."""
    d = [rng.choice((-1, 1)) * rng.choice((1 / 3, 0.5, 2 / 3, 1.0, 2.0)) + rng.choice((0.0, 0.25))
         for _ in range(changed)]
    d += [0.0] * (n - changed)
    rng.shuffle(d)
    return d


def _includes_zero(delta: dict) -> bool:
    return (delta["lo"] is None or delta["lo"] <= 0) and (delta["hi"] is None or delta["hi"] >= 0)


def test_the_issue_n_findings_row_now_excludes_zero_as_its_p_says():
    assert mean_ci(N_FINDINGS_26)["lo"] < 0 < mean_ci(N_FINDINGS_26)["hi"]  # the t interval it had: across 0
    row = paired_summary(_pairs(N_FINDINGS_26), "metric:n_findings")
    assert row["p"] == 0.049798 and row["interval_p_disagree"] is False
    assert row["delta"] == {"mean": 0.8846, "lo": 0.0588, "hi": 1.8182, "method": "sign_flip_inversion"}


def test_an_end_is_open_at_five_or_fewer_tasks_and_bounded_from_six():
    # every shifted delta of one sign gives p 2/2**n, which rejects only from n = 6 on, however large the effect
    for n in range(1, 6):
        assert sign_flip_interval([5.0] * n, sign_flip_p([5.0] * n)) == (None, None)
    assert sign_flip_interval([5.0] * 6, sign_flip_p([5.0] * 6)) == (5.0, 5.0)


def test_an_end_is_bounded_at_deltas_too_large_to_move_by_one():
    # 1e16 + 1.0 == 1e16: a far bound 1 past the extreme tied every shifted delta and left the end open
    for v in (1e16, 1e150):  # finite measures go up to SCORE_BOUND, 1e150
        assert sign_flip_interval([v] * 6, sign_flip_p([v] * 6)) == (v, v)
        assert sign_flip_interval([-v] * 6, sign_flip_p([-v] * 6)) == (-v, -v)
    assert sign_flip_interval([1e16] * 5 + [2e16], sign_flip_p([1e16] * 5 + [2e16])) == (1e16, 2e16)


def test_large_deltas_keep_the_ends_and_the_p_of_the_shifted_deltas():
    # at 1e7 the product's rounding passed EPS: the observed pattern stopped counting and both ends moved
    d = [0.62, 0.18, 0.94, -0.37, 0.71, 0.29, 0.83, 0.56, -0.05]
    big = [1e7 * x for x in d]
    lo, hi = sign_flip_interval(d, sign_flip_p(d), exact=True)
    big_lo, big_hi = sign_flip_interval(big, sign_flip_p(big), exact=True)
    assert abs(big_lo - 1e7 * lo) < 10 and abs(big_hi - 1e7 * hi) < 10, (big_lo, big_hi)  # 1e7 * TOL is 1
    test = ShiftTest(big)
    for theta in (1e5 * i + 0.37 for i in range(-200, 201)):  # off the deltas' grid, where sums tie exactly
        assert test.p(theta) == sign_flip_p([x - theta for x in big], exact=True), theta


def test_tied_tasks_count_once_shifted_off_zero():
    # one changed task of six: p 1 at 0, yet off 0 the five ties count and bound the interval on both sides
    assert sign_flip_p([1.0, 0, 0, 0, 0, 0]) == 1.0
    assert sign_flip_interval([1.0, 0, 0, 0, 0, 0], 1.0) == (0.0, 1.0)


def test_twenty_tasks_with_few_changed_agree_with_the_exact_p():
    # p is exact over the changed tasks, the interval Monte Carlo over all 20: 0 still falls where p says
    six, five = paired_summary(_pairs([1.0] * 6 + [0.0] * 14)), paired_summary(_pairs([1.0] * 5 + [0.0] * 15))
    assert six["p"] == 0.03125 and (six["delta"]["lo"], six["delta"]["hi"]) == (0.1, 0.5)
    assert five["p"] == 0.0625 and (five["delta"]["lo"], five["delta"]["hi"]) == (0.0, 0.4545)


def test_p_at_zero_is_the_sign_flip_p_exact_and_monte_carlo():
    rng = random.Random(1)
    for n, changed in [(1, 1), (4, 2), (6, 3), (9, 9), (16, 16), (20, 16), (20, 17), (26, 20), (30, 24)]:
        for seed in (0, 3):
            d = _deltas(rng, n, changed)
            assert ShiftTest(d, seed).p(0.0) == sign_flip_p(d, seed=seed, exact=True), (d, seed)
    near_tie = [1.0] * 16 + [1e-12]  # within EPS of 0: a tie, so 16 changed and still exact, as sign_flip_p has it
    assert ShiftTest(near_tie).p(0.0) == sign_flip_p(near_tie, exact=True) == 2 / 2 ** 16
    tied = [0.0, 1e-12, -1e-12]  # every delta tied: no p at 0, nor where a shift ties every delta again
    assert ShiftTest(tied).p(0.0) is sign_flip_p(tied) is None and ShiftTest([2.0] * 3).p(2.0) is None


def test_the_interval_excludes_zero_exactly_when_p_is_below_005():
    rng = random.Random(2)
    shapes = [(n, rng.randint(0, n)) for n in rng.choices(range(1, 17), k=150)]
    shapes += [(n, rng.randint(6, n)) for n in rng.choices(range(17, 31), k=20)]  # incl. > 16 tasks, <= 16 changed
    seen = set()
    for n, changed in shapes:
        d = _deltas(rng, n, changed)
        for exact in (False, True) if n % 4 == 0 else (False,):
            row = paired_summary(_pairs(d), exact=exact)
            if row["p"] is not None:
                assert _includes_zero(row["delta"]) == (row["p"] >= 0.05), (d, row["p"], row["delta"])
                seen.add((n > 16, row["p"] < 0.05))
            assert row["interval_p_disagree"] is False
    assert seen == {(False, False), (False, True), (True, False), (True, True)}


def test_the_ends_are_step_points_of_p():
    for d in ([10.0, 12.0, 11.0, 9.0, 13.0, 10.0, -2.0], [1.0] * 6 + [0.0], [0.5, -0.5, 0.25, -0.25, 0.5, -0.5],
              N_FINDINGS_26):
        test = ShiftTest(d)
        lo, hi = sign_flip_interval(d, sign_flip_p(d), exact=True)
        assert test.p(lo) >= 0.05 > test.p(lo - 1e-6) and test.p(hi) >= 0.05 > test.p(hi + 1e-6), d
    assert sign_flip_interval([10.0, 12.0, 11.0, 9.0, 13.0, 10.0, -2.0], 0.03125) == (4.0, 12.0)


def test_monte_carlo_ends_are_the_same_on_every_call_and_follow_the_seed():
    first = sign_flip_interval(N_FINDINGS_26, sign_flip_p(N_FINDINGS_26))
    compare_interval._draws.cache_clear()  # drawn afresh from the seed, not read back
    assert sign_flip_interval(N_FINDINGS_26, sign_flip_p(N_FINDINGS_26)) == first == (0.0588, 1.8182)
    # seed 1 draws a p of 0.05 or more: the interval then reaches 0, and the row still agrees with its p
    assert sign_flip_p(N_FINDINGS_26, seed=1) >= 0.05
    assert sign_flip_interval(N_FINDINGS_26, sign_flip_p(N_FINDINGS_26, seed=1), seed=1) == (0.0, 1.8)


def test_rounding_keeps_an_end_on_its_side_of_zero():
    # which side of 0 an end is on is the row's verdict: 4 places must not move it onto 0
    assert compare_interval._rounded(-3e-5) == -1e-4 and compare_interval._rounded(3e-5) == 1e-4
    assert str(compare_interval._rounded(-0.0)) == "0.0" and compare_interval._rounded(0.123449) == 0.1234


def test_a_mean_rounded_to_zero_is_zero_not_negative_zero():
    mean = paired_summary(_pairs([-1e-5, 0.0, 0.0]), "turns")["delta"]["mean"]  # -3.3e-6 at 4 places
    assert json.dumps(mean) == "0.0" and f"{mean:+.3f}" == "+0.000"


def test_an_open_end_prints_as_inf():
    assert format_interval(None, 0.5) == "[-inf, +0.500]" and format_interval(-0.25, None) == "[-0.250, +inf]"


def test_an_end_three_decimals_would_print_as_zero_keeps_its_sign_and_value():
    assert format_interval(1e-4, 4.9e-4) == "[+0.0001, +0.00049]" and format_interval(-3e-4, 0.0) == "[-0.0003, +0.000]"
    assert format_interval(1e-8, 0.0005) == "[+1e-08, +0.001]"  # an exact (ledger) end is not rounded to 4 places


def test_json_rows_name_the_interval_method_and_open_ends_are_null(tmp_path, capsys):
    b = tmp_path / "b"
    for task in ("fix_a", "fix_b", "fix_c"):
        _cell(b, task, "guidance:none", 1, success=False, turns=10)
        _cell(b, task, "guidance:raw", 1, turns=20)
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none", "--json"])
    args.func(args)
    rows = json.loads(capsys.readouterr().out)["comparisons"]
    assert {r["measure"] for r in rows} == {"gate", "cost_usd", "turns"}
    assert all(r["delta"]["method"] == "sign_flip_inversion" and r["delta"]["lo"] is None
               and r["delta"]["hi"] is None for r in rows)  # three tasks: no shift is rejected

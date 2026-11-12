# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arms significance and its interval from one sign-flip test (issue #421).

Every paired arms result (``paired_test``, ``arm_significance``, the interaction contrasts) takes ``p`` from the
sign-flip test on its deltas and ``ci_lo``/``ci_hi`` from inverting that test, so the interval excludes 0 exactly
when ``p < 0.05``. ``significant`` is ``None`` where the floor ``2 / 2**k`` over the k changed pairs is not below
0.05. The readers show the new fields and still render an ``arms.json`` written before them.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.eval import stats
from dokimi.eval.interactions import interaction_significance
from dokimi.report.arms_report import _fmt_ci, _fmt_estimate, render_arms_report
from dokimi.report.eval_report import generate_report
from dokimi.report.model_compare import _fmt_delta, generate_section_report

OLD_ARMS = Path(__file__).parent / "fixtures" / "artifacts" / "arms.v1.json"


# deltas, p, ci_lo, ci_hi, n_changed, significant: the repo track's own functions give these on main.
ROWS = [
    ([0, 0, 0.2, 0.3, 0.25, 0.3], 0.125, 0.0, 0.3, 4, None),
    ([0, 0, 0, 0.2, 0.3, 0.25, 0.3, 0.2], 0.0625, 0.0, 0.275, 5, None),
    ([-0.05, 0.2, 0.3, 0.25, 0.3, 0.2], 0.0625, -0.05, 0.3, 6, False),
    ([0.2, 0.3, 0.25, 0.3, 0.2], 0.0625, None, None, 5, None),
    ([20.0] * 8, 0.0078, 20.0, 20.0, 8, True),
]


@pytest.mark.parametrize(("deltas", "p", "lo", "hi", "changed", "significant"), ROWS)
def test_the_issue_rows_get_one_test_and_its_interval(deltas, p, lo, hi, changed, significant):
    res = stats.paired_test(deltas, [0] * len(deltas))
    assert (res["p"], res["ci_lo"], res["ci_hi"]) == (p, lo, hi)
    assert (res["n_changed"], res["significant"], res["ci_method"]) == (changed, significant, "sign_flip_inversion")
    assert res["ci_confidence"] == 0.95 and not {"p_ttest", "p_wilcoxon", "n_wilcoxon"} & set(res)


def test_a_constant_shift_gets_its_p_and_verdict_and_no_d_z():
    res = stats.paired_test([20.0] * 8, [0.0] * 8, rounded=False)
    assert (res["p"], res["significant"], res["cohens_d"], res["effect"]) == (2 / 2 ** 8, True, None, "unknown")
    assert stats.paired_test([0.0] * 8, [0.0] * 8)["cohens_d"] == 0.0


def test_every_pair_tied_gets_no_p_and_the_interval_0_0_from_six_pairs():
    six, five = stats.paired_test([0.0] * 6, [0.0] * 6), stats.paired_test([0.0] * 5, [0.0] * 5)
    assert (six["p"], six["ci_lo"], six["ci_hi"], six["significant"]) == (None, 0.0, 0.0, None)
    assert (five["p"], five["ci_lo"], five["ci_hi"], five["significant"]) == (None, None, None, None)


def test_a_nan_delta_gets_no_p_and_no_verdict():
    res = stats.paired_test([float("nan"), 0.2, 0.3, 0.25, 0.3, 0.2, 0.4], [0.0] * 7)
    assert (res["p"], res["significant"], res["ci_lo"], res["ci_hi"]) == (None, None, None, None)


def test_a_mean_under_a_hundredth_keeps_two_significant_digits():
    # the mean 0.0035 is 0.0 at 2 places; it prints as 0.0035, not +0.0, beside [-0.002, 0.01]
    res = stats.paired_test([0.01, -0.002, 0.006, 0.0, 0.004, 0.003], [0.0] * 6)
    assert res["delta_mean"] == pytest.approx(0.0035)
    assert res["ci_lo"] <= res["delta_mean"] <= res["ci_hi"]


def test_the_displayed_mean_stays_inside_a_narrow_interval():
    # 20.004 at 2 places is 20.0, outside the interval [20.004, 20.004] of a constant shift
    res = stats.paired_test([20.004] * 8, [0.0] * 8)
    assert (res["ci_lo"], res["delta_mean"], res["ci_hi"]) == (20.004, 20.004, 20.004)
    assert stats.paired_test([20.0, 21.0] * 4, [0.0] * 8)["delta_mean"] == 20.5  # inside: rounded as before


def _includes_zero(lo, hi) -> bool:
    return (lo is None or lo <= 0) and (hi is None or hi >= 0)


def _agrees(res: dict) -> bool:
    """The interval, displayed and exact, excludes 0 exactly when ``significant``; no verdict, and it includes 0."""
    shown = _includes_zero(res["ci_lo"], res["ci_hi"])
    assert shown == _includes_zero(res["exact"]["ci_lo"], res["exact"]["ci_hi"]), res
    if res["significant"] is None:
        assert shown, res  # no p, or a floor at 0.05 or more: p cannot be below 0.05 either
    else:
        assert res["significant"] is (not shown), res
    return not shown


def _deltas(rng: random.Random, n: int, changed: int, shift: float) -> list[float]:
    """``changed`` judge-like deltas, N(shift, 12) at one decimal (one may round to a tie), and ties for the rest."""
    d = [round(rng.gauss(shift, 12), 1) for _ in range(changed)] + [0.0] * (n - changed)
    rng.shuffle(d)
    return d


def _shapes(rng: random.Random, count: int) -> list[tuple[int, int, float]]:
    """(pairs, changed, shift): 2 to 40 pairs, exact and Monte Carlo, more than 16 pairs with 16 or fewer changed."""
    fixed = [(2, 2, 8.0), (5, 5, 8.0), (6, 6, 0.0), (30, 12, 8.0), (30, 16, 0.0), (26, 22, 8.0), (40, 30, 0.0)]
    out = list(fixed)
    while len(out) < count:
        n = rng.randint(2, 16)
        out.append((n, rng.randint(1, n), rng.choice((0.0, 4.0, 8.0))))
    return out


def test_paired_test_interval_excludes_zero_exactly_when_p_is_below_alpha():
    rng = random.Random(421)
    seen = set()
    for n, changed, shift in _shapes(rng, 60):
        d = _deltas(rng, n, changed, shift)
        res = stats.paired_test(d, [0.0] * n, rounded=False)
        if res["p"] is not None:
            assert _includes_zero(res["ci_lo"], res["ci_hi"]) == (res["p"] >= stats.ALPHA), (d, res["p"])
            seen.add((res["n_changed"] > 16, res["p"] < stats.ALPHA))
        _agrees(res)
    assert seen == {(False, False), (False, True), (True, False), (True, True)}


def _arm_evaluations(arms: dict[str, list[float]]) -> list[dict]:
    n = len(next(iter(arms.values())))
    return [{"question_id": f"q{i}", "scores": {"baseline": {"aggregate": 50.0},
                                                 **{arm: {"aggregate": 50.0 + d[i]} for arm, d in arms.items()}}}
            for i in range(n)]


def test_arm_significance_interval_agrees_with_its_verdict():
    rng = random.Random(7)
    verdicts = set()
    for n, changed, shift in _shapes(rng, 14):
        arms = {f"arm{k}": _deltas(rng, n, changed, s) for k, s in enumerate((shift, 0.0))}
        for res in stats.arm_significance(_arm_evaluations(arms), "baseline").values():
            verdicts.add(_agrees(res) if res["significant"] is not None else None)
    assert verdicts == {True, False, None}


def test_interaction_contrasts_interval_agrees_with_its_verdict():
    rng = random.Random(11)
    verdicts = set()
    for n, changed, shift in _shapes(rng, 14):
        contrast = _deltas(rng, n, changed, shift)
        a, b = [rng.randint(-5, 5) for _ in range(n)], [rng.randint(-5, 5) for _ in range(n)]
        both = [x + y + c for x, y, c in zip(a, b, contrast, strict=True)]  # the contrast AB - A - B + base is c
        ev = _arm_evaluations({"docs": a, "skill:x": b, "docs+skill:x": both})
        for res in interaction_significance(ev, ["baseline", "docs", "skill:x", "docs+skill:x"], "baseline"):
            assert res["ci_method"] == "sign_flip_inversion" and "p_ttest" not in res
            verdicts.add(_agrees(res) if res["significant"] is not None else None)
    assert verdicts == {True, False, None}


# a: 8 positive pairs (p 2/256); b: one negative pair (p 6/256); c: 3 changed pairs, p 0.25 and under the floor.
HOLM_ARMS = {"a": [5, 6, 7, 8, 9, 10, 11, 12], "b": [5, -1, 4, 6, 3, 2, 7, 1], "c": [3, 0, 0, 0, 2, 1, 0, 0]}


def test_holm_corrects_the_sign_flip_p_across_three_arms_one_under_the_floor():
    res = stats.arm_significance(_arm_evaluations(HOLM_ARMS), "baseline", ["baseline", "a", "b", "c"])
    # Holm: 2 x 2/256, then 1 x 6/256; c's 0.25 is under the floor, out of the multiplier, with no adjusted p
    assert [res[a]["p"] for a in "abc"] == [0.0078, 0.0234, 0.25]
    assert [res[a]["p_adjusted"] for a in "abc"] == [0.0156, 0.0234, None]
    assert [res[a]["exact"]["p_adjusted"] for a in "abc"] == [2 * 2 / 256, 6 / 256, None]
    assert [res[a]["significant_adjusted"] for a in "abc"] == [True, True, None]
    assert [(res[a]["family_size"], res[a]["family_tested"]) for a in "abc"] == [(3, 2)] * 3
    assert (res["c"]["n_changed"], res["c"]["significant"]) == (3, None)


def test_holm_across_two_arms():
    res = stats.arm_significance(_arm_evaluations(HOLM_ARMS), "baseline", ["baseline", "a", "b"])
    assert [(res[a]["p_adjusted"], res[a]["exact"]["p_adjusted"]) for a in "ab"] == [(0.0156, 2 * 2 / 256),
                                                                                       (0.0234, 6 / 256)]
    assert [res[a]["significant_adjusted"] for a in "ab"] == [True, True]


def _arms_data(sig: dict) -> dict:
    block = {"n": 6, "delta_mean": 5.0, "cohens_d": 0.8, "effect": "large", "ci_lo": 1.0, "ci_hi": 9.0,
             "p_adjusted": 0.0625, "significant_adjusted": False, "correction": "holm", **sig}
    return {"library_name": "dpnp", "model": "m", "provider": "bedrock", "arms": ["baseline", "docs"],
            "baseline_arm": "baseline", "total_questions": 6, "answers": [],
            "summary": {"per_arm": {"baseline": {"avg_aggregate": 50.0, "n": 6},
                                    "docs": {"avg_aggregate": 55.0, "n": 6, "significance": block}},
                        "interactions": [{"arm_a": "docs", "arm_b": "skill:x", "interaction": -8.0, "n": 6, **sig}]}}


def _rows(md: str) -> list[str]:
    """The arm's row of the significance table, then the interaction row."""
    tables = [md.split(heading)[1].split("\n## ")[0] for heading in ("## Paired significance", "## Factorial")]
    return [next(ln for ln in table.splitlines() if ln.startswith("| `docs` |")) for table in tables]


def test_arms_report_prints_p_from_a_new_record_and_p_ttest_from_an_old_one():
    new_md, old_md = render_arms_report(_arms_data({"p": 0.0312})), render_arms_report(_arms_data({"p_ttest": 0.0339}))
    new, old = _rows(new_md), _rows(old_md)
    assert new[0].endswith("| [+1.0, +9.0] | +0.80 | large | 0.0312 | 0.0625 | no | 6 |")
    assert old[0].endswith("| [+1.0, +9.0] | +0.80 | large | 0.0339† | 0.0625 | no | 6 |")
    assert new[1].endswith("| 0.0312 | — | — | 6 |") and old[1].endswith("| 0.0339† | — | — | 6 |")  # interaction
    # the old record's t-test p is named under each table; a new record's p gets no note
    note = "† t-test p, and p (adj) its correction: the record predates the sign-flip test."
    assert old_md.count(note) == 2 and note not in new_md
    # a new record whose every pair tied has no p, whatever an old field might say
    tied = render_arms_report(_arms_data({"p": None, "p_ttest": 0.01}))
    assert "| — | 0.0625 |" in _rows(tied)[0] and note not in tied


def test_arms_report_prints_an_open_end_as_open():
    rows = _rows(render_arms_report(_arms_data({"p": 0.0625, "ci_lo": None, "ci_hi": None})))
    assert "| [-inf, +inf] |" in rows[0] and "| [-inf, +inf] |" in rows[1]
    assert _fmt_ci(None, 0.25) == "[-inf, +0.2]" and _fmt_ci(-0.25, None) == "[-0.2, +inf]"
    assert _fmt_ci(1e-4, 0.3) == "[+0.0001, +0.3]"  # an end off 0 keeps its side of 0 at one decimal


def test_arms_report_prints_a_small_estimate_inside_its_interval():
    # eight pairs of constant shifts under 0.05, in the arm table and in every interaction cell: one decimal prints 0.0
    ev = _arm_evaluations({"docs": [0.04] * 8, "skill:x": [-0.03] * 8, "docs+skill:x": [0.02] * 8})
    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    per_arm: dict = {arm: {"avg_aggregate": 50.0, "n": 8} for arm in arms}
    for arm, sig in stats.arm_significance(ev, "baseline", arms).items():
        per_arm[arm]["significance"] = sig
    summary = {"per_arm": per_arm, "interactions": interaction_significance(ev, arms, "baseline")}
    arm, interaction = _rows(render_arms_report({"arms": arms, "baseline_arm": "baseline", "answers": [],
                                                 "summary": summary}))
    assert arm.startswith("| `docs` | +0.04 | [+0.04, +0.04] |") and arm.endswith("| yes | 8 |")
    assert interaction.startswith("| `docs` | `skill:x` | +0.04 | -0.03 | +0.01 | +0.02 | **+0.01** | [+0.01, +0.01] | "
                                  "0.0078 |")


def test_a_sub_hundredth_delta_prints_in_fixed_point_never_scientific():
    assert _fmt_ci(1e-5, -2.5e-5) == "[+0.00001, -0.000025]" and _fmt_estimate(0.0035) == "+0.0035"
    assert [_fmt_delta(d) for d in (1e-5, -3.5e-3, 0.04, 0.0, 0.5)] == ["+0.00001", "-0.0035", "+0.04", "0.0", "+0.5"]


def test_an_arm_delta_reads_the_same_in_the_arm_and_the_interaction_table():
    # eight pairs of constant shifts under 0.005: two places is 0.0 for each, two significant digits is not
    ev = _arm_evaluations({"docs": [0.0035] * 8, "skill:x": [0.0012] * 8, "docs+skill:x": [0.0049] * 8})
    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    arm_row = stats.arm_significance(ev, "baseline", arms)["docs"]
    cell = interaction_significance(ev, arms, "baseline")[0]
    assert cell["delta_a"] == arm_row["delta_mean"] == pytest.approx(0.0035)
    assert cell["delta_b"] == pytest.approx(0.0012) and cell["delta_combined"] == pytest.approx(0.0049)
    assert cell["additive_expectation"] == pytest.approx(0.0047)
    assert _fmt_estimate(cell["delta_a"]) == _fmt_estimate(arm_row["delta_mean"]) == "+0.0035"


@pytest.mark.parametrize("docs, skill, both", [(0.049, 0.0, 0.049), (0.014, 0.014, 0.014), (-0.014, 0.014, 0.0)])
def test_an_estimate_at_a_rounding_boundary_prints_alike_in_both_tables_and_the_row_adds_up(docs, skill, both):
    # eight identical pairs: 0.049 rounds to 0.05 and would print +0.1; the arm table prints the estimate held inside its interval
    ev = _arm_evaluations({"docs": [docs] * 8, "skill:x": [skill] * 8, "docs+skill:x": [both] * 8})
    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    table = stats.arm_significance(ev, "baseline", arms)
    cell = interaction_significance(ev, arms, "baseline")[0]
    assert cell["delta_a"] == table["docs"]["delta_mean"] and cell["delta_b"] == table["skill:x"]["delta_mean"]
    assert cell["delta_combined"] == table["docs+skill:x"]["delta_mean"]
    assert cell["additive_expectation"] == pytest.approx(cell["delta_a"] + cell["delta_b"], abs=1e-12)
    assert _fmt_estimate(cell["delta_a"]) == _fmt_estimate(table["docs"]["delta_mean"])
    if docs == 0.049:
        assert _fmt_estimate(cell["delta_a"]) == "+0.049" and _fmt_estimate(cell["additive_expectation"]) == "+0.049"


def test_eval_report_prints_the_sign_flip_p_and_no_t_or_wilcoxon_row(tmp_path):
    def report(deltas: list[float]) -> str:
        evaluations = [{"question_id": f"q{i}", "with_docs": {"aggregate": 50.0 + d},
                        "without_docs": {"aggregate": 50.0}} for i, d in enumerate(deltas)]
        path, out = tmp_path / "lib.json", tmp_path / "lib.md"
        path.write_text(json.dumps({"judge_model": "m", "evaluations": evaluations}), encoding="utf-8")
        generate_report(str(path), str(out), str(tmp_path / "lib.qa.json"))
        return out.read_text(encoding="utf-8").split("## Statistical Significance")[1].split("\n## ")[0]

    six = report([10.0] * 6)
    assert "| Sign-flip p (two-sided) | 0.0312 |" in six and "| Changed pairs (non-zero delta) | 6 of 6 |" in six
    assert "| 95% CI on the delta | [10.0, 10.0] (sign_flip_inversion) |" in six and "Statistically significant" in six
    assert "t-test" not in six and "Wilcoxon" not in six
    five = report([10.0] * 5)  # under the floor: the test runs, p is printed, no verdict, open ends
    assert "| Sign-flip p (two-sided) | 0.0625 |" in five and "Cannot determine" in five
    assert "| 95% CI on the delta | [-inf, +inf] (sign_flip_inversion) |" in five


def test_model_compare_prints_one_sign_flip_p_column():
    def run(deltas: list[float]) -> dict:
        return {"arms": ["baseline", "docs"], "baseline_arm": "baseline", "evaluations": [
            {"question_id": f"q{i}", "question_text": f"Q{i}",
             "scores": {"baseline": {"aggregate": 50.0}, "docs": {"aggregate": 50.0 + d}}}
            for i, d in enumerate(deltas)]}

    text = "\n".join(generate_section_report([("six", run([10.0] * 6)), ("five", run([10.0] * 5 + [0.0]))],
                                             ["six", "five"], "Regular"))
    section = text.split("## Statistical Significance")[1].split("\n## ")[0]
    assert "| Run | Delta | p (sign-flip) | p (adjusted) | Changed pairs |" in section
    assert "t-test" not in section and "Wilcoxon" not in section and "t_stat" not in section
    # The run of five changed pairs is under the floor: out of the family, no adjusted p, no verdict
    assert "| **six** | +10.0 | 0.0312 | 0.0312 | 6 of 6 | N/A | unknown | OK Yes |" in section
    assert "| **five** | +8.3 | 0.0625 | N/A | 5 of 6 | +2.0 | large | N/A |" in section


def test_model_compare_prints_a_small_significant_delta_with_its_sign():
    def run(delta: float) -> dict:
        return {"arms": ["baseline", "docs"], "baseline_arm": "baseline", "evaluations": [
            {"question_id": f"q{i}", "question_text": f"Q{i}",
             "scores": {"baseline": {"aggregate": 50.0}, "docs": {"aggregate": 50.0 + delta}}} for i in range(8)]}

    text = "\n".join(generate_section_report([("up", run(0.04)), ("down", run(-0.04)), ("tied", run(0.0))],
                                             ["up", "down", "tied"], "Regular"))
    section = text.split("## Statistical Significance")[1].split("\n## ")[0]
    # eight constant ±0.04 shifts: p 2/2^8, Holm over the two runs with a p; one decimal printed 0.0 and -0.0
    assert "| **up** | +0.04 | 0.0078 | 0.0156 | 8 of 8 | N/A | unknown | OK Yes |" in section
    assert "| **down** | -0.04 | 0.0078 | 0.0156 | 8 of 8 | N/A | unknown | OK Yes |" in section
    assert "| **tied** | 0.0 | N/A | N/A | 0 of 8 | 0.0 | negligible | N/A |" in section


# CI's static job runs config_check and `cli.py repo scan` without numpy. This imports dokimi.cli, not the cli.py shim
# (not on sys.path in the wheel jobs): a numpy import in the shim itself fails that job's scan step, not this test.
NO_NUMPY = (
    "import sys\n"
    "sys.modules['numpy'] = sys.modules['scipy'] = None\n"  # an import fails, find_spec("scipy") is None: as if uninstalled
    "import dokimi.cli, dokimi.config_check\n"
    "from dokimi.eval import stats\n"
    "from dokimi.eval.sign_flip import sign_flip_p\n"
    "print(sign_flip_p([1.0] * 6))\n"
)


def test_the_package_imports_without_numpy_as_the_static_job_runs_it():
    done = subprocess.run([sys.executable, "-c", NO_NUMPY], capture_output=True, text=True,
                          cwd=Path(__file__).parents[1], check=False)
    assert (done.returncode, done.stdout) == (0, "0.03125\n"), done.stderr


def test_a_new_arms_json_and_one_written_before_both_validate():
    ev = _arm_evaluations({"docs": [3, 1, 4, 0, 3, 1], "skill:x": [11, 13, 10, 14, 11, 13],
                           "docs+skill:x": [7, 5, 8, 4, 7, 5]})
    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    per_arm: dict = {arm: {"avg_aggregate": 50.0, "n": 6} for arm in arms}
    for arm, sig in stats.arm_significance(ev, "baseline", arms).items():
        per_arm[arm]["significance"] = sig
    new = {"arms": arms, "baseline_arm": "baseline", "answers": [],
           "summary": {"per_arm": per_arm, "interactions": interaction_significance(ev, arms, "baseline")}}
    assert new["summary"]["per_arm"]["docs"]["significance"]["ci_method"] == "sign_flip_inversion"
    validate_artifact("arms", json.loads(json.dumps(new)))
    old = json.loads(OLD_ARMS.read_text(encoding="utf-8"))
    assert old["summary"]["per_arm"]["docs"]["significance"]["ci_method"] == "student_t"
    validate_artifact("arms", old)
    new["summary"]["per_arm"]["docs"]["significance"]["n_changed"] = "five"
    with pytest.raises(ArtifactValidationError, match="n_changed"):
        validate_artifact("arms", new)

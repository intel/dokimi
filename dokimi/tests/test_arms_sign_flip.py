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
from dokimi.report.arms_report import _fmt_ci, render_arms_report
from dokimi.report.eval_report import generate_report
from dokimi.report.model_compare import generate_section_report

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
    # Holm: 3 x 2/256, then 2 x 6/256; c's 0.25 stays in the family and counts in the multiplier
    assert [res[a]["p"] for a in "abc"] == [0.0078, 0.0234, 0.25]
    assert [res[a]["p_adjusted"] for a in "abc"] == [0.0234, 0.0469, 0.25]
    assert [res[a]["exact"]["p_adjusted"] for a in "abc"] == [3 * 2 / 256, 2 * 6 / 256, 0.25]
    assert [res[a]["significant_adjusted"] for a in "abc"] == [True, True, None]
    assert [(res[a]["family_size"], res[a]["family_tested"]) for a in "abc"] == [(3, 3)] * 3
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
    new, old = _rows(render_arms_report(_arms_data({"p": 0.0312}))), _rows(render_arms_report(_arms_data({"p_ttest": 0.0339})))
    assert new[0].endswith("| [+1.0, +9.0] | +0.80 | large | 0.0312 | 0.0625 | no | 6 |")
    assert old[0].endswith("| [+1.0, +9.0] | +0.80 | large | 0.0339 | 0.0625 | no | 6 |")
    assert new[1].endswith("| 0.0312 | — | — | 6 |") and old[1].endswith("| 0.0339 | — | — | 6 |")  # interaction
    # a new record whose every pair tied has no p, whatever an old field might say
    assert "| — | 0.0625 |" in _rows(render_arms_report(_arms_data({"p": None, "p_ttest": 0.01})))[0]


def test_arms_report_prints_an_open_end_as_open():
    rows = _rows(render_arms_report(_arms_data({"p": 0.0625, "ci_lo": None, "ci_hi": None})))
    assert "| [-inf, +inf] |" in rows[0] and "| [-inf, +inf] |" in rows[1]
    assert _fmt_ci(None, 0.25) == "[-inf, +0.2]" and _fmt_ci(-0.25, None) == "[-0.2, +inf]"
    assert _fmt_ci(1e-4, 0.3) == "[+0.0001, +0.3]"  # an end off 0 keeps its side of 0 at one decimal


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
    # Holm over both runs: 2 x 2/64 and 1 x 2/32; the run of five changed pairs is in the family, with no verdict
    assert "| **six** | +10.0 | 0.0312 | 0.0625 | 6 of 6 | N/A | unknown | No |" in section
    assert "| **five** | +8.3 | 0.0625 | 0.0625 | 5 of 6 | +2.0 | large | N/A |" in section


# CI's static job imports the package with its tools only: config_check and `cli.py repo scan` run without numpy.
NO_NUMPY = (
    "import importlib.abc, sys\n"
    "class Block(importlib.abc.MetaPathFinder):\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] in ('numpy', 'scipy'):\n"
    "            raise ModuleNotFoundError(name)\n"
    "sys.meta_path.insert(0, Block())\n"
    "import cli, dokimi.config_check\n"
    "from dokimi.eval import stats\n"
    "print(stats.sign_flip_p([1.0] * 6))\n"
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

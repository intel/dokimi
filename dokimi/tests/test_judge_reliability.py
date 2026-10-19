# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Offline tests for the judge-reliability checks: sequential gate, wording
sensitivity, required-point coverage, pairwise judging and the cross-judge verdict."""

from unittest.mock import MagicMock

import pytest

from dokimi.eval import checklist as ck
from dokimi.eval.judge import JUDGE_PROMPT, Judge
from dokimi.eval.judge_sequential import SPRT_BATCH, run_sequential, sprt
from dokimi.eval.judge_validity import evaluate_pairs
from dokimi.eval.judge_wording import WORDINGS, WordingReport, reword

from tests.test_judge_validity import ANSWER, OTHER_ANSWER


def _many_answers(n=40):
    return [
        {
            "question_id": f"Q{i:03d}",
            "question_text": f"Question {i}?",
            "arms": {"baseline": {"answer": (ANSWER if i % 2 else OTHER_ANSWER) + f" Case {i}."}},
        }
        for i in range(n)
    ]


def _length_judge(library, question, answer):
    return float(len(answer) % 1000)


def _perfect(intact_set):
    return lambda library, question, answer: 90.0 if answer in intact_set else 10.0


# --- sequential -------------------------------------------------------------


def test_sprt_decides_on_clear_evidence_only():
    assert sprt([1.0] * 15, 0.8)[0] == "PASS"
    assert sprt([1.0] * 5, 0.8)[0] == "continue"
    assert sprt([0.0] * 5, 0.8)[0] == "FAIL"
    # Exactly at the gate the evidence never accumulates toward either side.
    assert sprt([1.0, 1.0, 1.0, 1.0, 0.0] * 4, 0.8)[0] == "continue"


def test_sequential_stops_a_perfect_judge_after_the_minimum_rounds():
    answers = _many_answers()
    intact = {a["arms"]["baseline"]["answer"] for a in answers}
    report = run_sequential(answers, _perfect(intact), max_bases=40, variants=["truncate_20", "strip_code"])
    s = report.summary()
    assert s["mode"] == "sequential"
    assert s["passed"] and s["conclusive"]
    for v in ("truncate_20", "strip_code"):
        seq = s["per_variant"][v]["sequential"]
        assert seq["decision"] == "PASS"
        assert seq["n"] == 2 * SPRT_BATCH  # 15 straight wins are needed; batches are 10


def test_sequential_fails_an_inverted_judge_early():
    answers = _many_answers()
    intact = {a["arms"]["baseline"]["answer"] for a in answers}
    good = _perfect(intact)
    report = run_sequential(
        answers, lambda lib, q, a: 100.0 - good(lib, q, a), max_bases=40, variants=["truncate_20"]
    )
    s = report.summary()
    assert not s["passed"]
    assert s["per_variant"]["truncate_20"]["sequential"]["decision"] == "FAIL"
    assert s["per_variant"]["truncate_20"]["n"] == SPRT_BATCH


def test_sequential_reports_the_cap_as_inconclusive():
    answers = _many_answers()
    report = run_sequential(answers, lambda *_: 50.0, max_bases=20, variants=["truncate_20"])
    s = report.summary()
    # Ties are half a success: 0.5 sits below the gate, but 20 pairs at the
    # FAIL boundary is not reached... and the pass rule must not flip it to PASS.
    assert s["per_variant"]["truncate_20"]["sequential"]["decision"] in ("FAIL", "continue")
    assert not s["passed"]
    assert "RESULT: FAIL" in report.format_summary()


def test_sequential_says_when_the_pool_ran_out_before_the_cap():
    answers = _many_answers(12)
    # A judge sitting exactly on the gate: right on 4 pairs of 5, so SPRT never decides.
    calls = []

    def at_the_gate(pair):
        calls.append(pair)
        return (60.0, 50.0) if len(calls) % 5 else (50.0, 60.0)

    report = run_sequential(answers, lambda *_: 0.0, max_bases=120, variants=["truncate_20"],
                            pair_scorer=at_the_gate)
    seq = report.summary()["per_variant"]["truncate_20"]["sequential"]
    assert seq["decision"] == "continue" and seq["stopped_by"] == "pool"
    assert "answer pool exhausted" in report.format_summary()



def _undecided_by_variant():
    """A pair scorer right on 4 pairs of 5 for each variant, so SPRT never decides any."""
    calls: dict = {}

    def at_the_gate(pair):
        n = calls[pair["variant"]] = calls.get(pair["variant"], 0) + 1
        return (60.0, 50.0) if n % 5 else (50.0, 60.0)

    return at_the_gate


def test_sequential_stopped_by_is_judged_per_variant():
    fence = "```\nperf stat -e cycles,instructions ./app\n```\n\n"
    answers = _many_answers(40)
    for i, a in enumerate(answers):
        if i >= 5:  # a code block in only 5 answers: strip_code applies to 5 bases
            a["arms"]["baseline"]["answer"] = ANSWER.replace(fence, "") + f" Case {i}."
    report = run_sequential(answers, lambda *_: 0.0, max_bases=40, variants=["strip_code", "truncate_20"],
                            pair_scorer=_undecided_by_variant())
    pv = report.summary()["per_variant"]
    strip, trunc = pv["strip_code"]["sequential"], pv["truncate_20"]["sequential"]
    assert (strip["n"], strip["decision"], trunc["decision"]) == (5, "continue", "continue")
    assert (strip["stopped_by"], trunc["stopped_by"]) == ("pool", "cap")


def test_sequential_few_pairs_with_more_answers_than_the_cap_is_the_cap():
    """strip_code has 5 pairs in the 20 bases sampled, but 40 answers exist: a higher
    --max-bases would add pairs, so "answer pool exhausted" would send the user the wrong way."""
    fence = "```\nperf stat -e cycles,instructions ./app\n```\n\n"
    answers = _many_answers(40)
    for i, a in enumerate(answers):
        if i % 8:
            a["arms"]["baseline"]["answer"] = ANSWER.replace(fence, "") + f" Case {i}."
    report = run_sequential(answers, lambda *_: 0.0, max_bases=20, variants=["strip_code"],
                            pair_scorer=_undecided_by_variant())
    seq = report.summary()["per_variant"]["strip_code"]["sequential"]
    assert seq["decision"] == "continue" and seq["n"] < 20
    assert seq["stopped_by"] == "cap"


def test_neutral_variant_stops_taking_pairs_at_30_bases():
    report = run_sequential(_many_answers(60), lambda *_: 0.0, max_bases=60, variants=["truncate_20", "padding"],
                            pair_scorer=_undecided_by_variant())
    counts = {v: sum(1 for r in report.rows if r.variant == v) for v in ("truncate_20", "padding")}
    assert counts == {"truncate_20": 60, "padding": 30}

def test_concurrent_scoring_matches_serial():
    answers = _many_answers(12)
    from dokimi.eval.degrade import build_pairs

    pairs = build_pairs(answers, n_bases=12)
    a = evaluate_pairs(_length_judge, pairs).to_dict()["rows"]
    b = evaluate_pairs(_length_judge, pairs, concurrency=6).to_dict()["rows"]
    assert a == b


# --- wording ----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WORDINGS))
def test_every_wording_applies_to_the_shipped_prompt(name):
    out = reword(JUDGE_PROMPT, name)
    assert out != JUDGE_PROMPT
    assert out.split("**Output format", 1)[1] == JUDGE_PROMPT.split("**Output format", 1)[1]


def test_a_wording_whose_anchor_is_gone_raises_instead_of_measuring_the_original():
    with pytest.raises(ValueError):
        reword("no criteria here\n**Output format** {}", "reorder_dimensions")


def _report_with(correct_by_cond, variant="wrong_numbers"):
    """A WordingReport whose conditions ordered pair i right when ``correct_by_cond[cond][i]``."""
    from dokimi.eval.judge_validity import ValidityReport
    from dokimi.eval.validity_scoring import ValidityRow

    report = WordingReport(judge="fake")
    for cond, correct in correct_by_cond.items():
        report.reports[cond] = ValidityReport(judge="fake", rows=[
            ValidityRow(base_id=f"Q{i}", arm="baseline", variant=variant,
                        intact_score=90.0 if ok else 10.0, degraded_score=50.0)
            for i, ok in enumerate(correct)
        ])
    return report


def test_wording_flags_a_rewrite_that_moves_the_same_pairs():
    # 16 of 20 under the original and a re-draw; the persona rewrite loses 9 of those pairs.
    base = [True] * 16 + [False] * 4
    s = _report_with({"original": base, "redraw-1": base, "persona": [False] * 9 + base[9:]}).summary()
    assert s["fragile_variants"] == ["wrong_numbers"]
    assert s["per_variant"]["wrong_numbers"]["worst_wording"] == "persona"
    assert s["per_variant"]["wrong_numbers"]["p_holm"] < 0.05


def test_wording_does_not_flag_identical_prompts():
    """Identical prompts drawn independently and given the wording and noise roles:
    the old range-versus-range rule flagged about half of such runs at the defaults."""
    import random

    flagged = 0
    for seed in range(40):
        rng = random.Random(seed)
        conds = ["original", "redraw-1", *sorted(WORDINGS)]
        draws = {c: [rng.random() < 0.7 for _ in range(20)] for c in conds}
        flagged += bool(_report_with(draws).summary()["fragile_variants"])
    assert flagged <= 3


# --- checklist --------------------------------------------------------------


def test_parse_and_points():
    assert ck.parse_yes_no("Yes.") is True
    assert ck.parse_yes_no("**NO**") is False
    assert ck.parse_yes_no("maybe") is None
    assert ck.parse_yes_no("no") is False
    # "no" inside prose is not the verdict.
    assert ck.parse_yes_no("There is no explicit mention, but it is covered: YES") is True
    assert ck.points_of({"expected_topics": "['a', 'b']"}) == ["a", "b"]
    assert ck.points_of({"expected_topics": ["a", " "]}) == ["a"]


def test_disagreeing_polarities_count_half():
    assert ck.PointVerdict("p", covered=True, absent=False).score == 1.0
    assert ck.PointVerdict("p", covered=False, absent=True).score == 0.0
    assert ck.PointVerdict("p", covered=True, absent=True).score == 0.5  # yes-bias
    assert ck.PointVerdict("p", covered=False, absent=False).score == 0.5  # no-bias
    assert ck.PointVerdict("p", covered=None, absent=False).score is None


def _keyword_ask(prompt, **_):
    """Covers a point iff the point's text appears in the answer."""
    answer = prompt.split("ANSWER:\n", 1)[1].split("\n\nRequired point:", 1)[0]
    point = prompt.split("Required point:\n", 1)[1].split("\n\n", 1)[0]
    present = point in answer
    asks_absent = ck.ASK_ABSENT in prompt
    return "YES" if present != asks_absent else "NO"


def test_run_checklist_compares_arms_to_baseline():
    records = [
        {"question_id": f"Q{i}", "question_text": "q", "arms": {
            "baseline": {"answer": "alpha"},
            "skill": {"answer": "alpha beta"},
        }}
        for i in range(6)
    ] + [{"question_id": "QX", "question_text": "q", "arms": {"baseline": {"answer": "x"}}}]
    points = {f"Q{i}": ["alpha", "beta"] for i in range(6)}
    report = ck.run_checklist(records, points, _keyword_ask, concurrency=3)
    s = report.summary()
    assert s["n_without_points"] == 1
    assert s["arms"]["baseline"]["coverage"]["mean"] == 0.5
    assert s["arms"]["skill"]["coverage"]["mean"] == 1.0
    vb = s["arms"]["skill"]["vs_baseline"]
    assert (vb["wins"], vb["losses"], vb["ties"]) == (6, 0, 0)
    assert s["arms"]["skill"]["consistency"] == 1.0
    md = report.format_markdown()
    assert "| `skill` | 1.000" in md and "**+** `skill`" in md


def test_a_failed_checklist_call_is_unparsed_not_a_lost_run():
    def flaky_ask(prompt, **_):
        if "beta" in prompt.split("Required point:\n", 1)[1]:
            raise RuntimeError("ValidationException: input too long")
        return _keyword_ask(prompt)

    records = [{"question_id": "Q1", "question_text": "q", "arms": {
        "baseline": {"answer": "alpha"}, "skill": {"answer": "alpha beta"}}}]
    report = ck.run_checklist(records, {"Q1": ["alpha", "beta"]}, flaky_ask, concurrency=2)
    s = report.summary()
    assert s["arms"]["skill"]["unparsed"] == 1
    assert s["arms"]["skill"]["coverage"]["mean"] == 1.0


# --- per-draw caching -------------------------------------------------------


def test_prompt_template_override_reaches_the_model():
    judge = Judge.__new__(Judge)
    judge.temperature = judge.seed = None
    judge.prompt_template = "CUSTOM __QUESTION__ __ANSWER__ __CONTEXT__ __LIBRARY_NAME__"
    judge._accumulate_usage = lambda usage: None
    judge.llm = MagicMock()
    judge.llm.invoke.return_value = MagicMock(content=(
        '{"correctness": 1, "completeness": 1, "specificity": 1, "code_quality": 1, "actionability": 1, "aggregate": 0}'
    ))
    judge.score_answer(library_name="L", question="Q?", answer="A!")
    assert judge.llm.invoke.call_args.args[0].startswith("CUSTOM Q? A!")


# --- cross-judge ------------------------------------------------------------

from dokimi.eval import cross_judge as cj  # noqa: E402


def _row(sign, significant):
    return {"sign": sign, "p_sign": 0.001 if significant else 0.5}


def test_an_escalated_pairwise_measure_is_not_a_second_judge():
    rows = [{"sign": 1, "p_sign": 0.001, "measure": "p/oss pairwise"},
            {"sign": 1, "p_sign": 0.001, "measure": "p/oss pairwise → p/opus"}]
    assert cj.label(rows) == "CONSISTENT + (one judge)"
    rows.append({"sign": 1, "p_sign": 0.001, "measure": "p/haiku"})
    assert cj.label(rows) == "CONSISTENT +"


def test_one_marginal_measure_out_of_many_is_not_a_verdict():
    rows = [{"sign": 1, "p_sign": 0.03}] + [{"sign": 1, "p_sign": 0.4} for _ in range(5)]
    assert cj.label(rows) == "NO DETECTABLE EFFECT"  # Holm: 0.03 x 6 = 0.18
    assert rows[0]["p_holm"] > 0.05 and rows[0]["votes"]


def test_a_failed_gate_also_silences_that_judges_grounded_measure():
    absolute = cj.Measure(name="p/j", kind="aggregate")
    grounded = cj.Measure(name="p/j", kind="grounded")
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[absolute, grounded])
    report.apply_validity([{"judge": "p/j", "passed": False}])
    assert (absolute.gate, grounded.gate) == ("FAIL", "FAIL")
    report.apply_validity([{"judge": "p/j", "passed": True}])
    assert (absolute.gate, grounded.gate) == ("FAIL", "FAIL")  # a later PASS does not lift a FAIL
    fresh = cj.Measure(name="p/j", kind="grounded")
    cj.CrossJudgeReport(baseline_arm="baseline", measures=[fresh]).apply_validity([{"judge": "p/j", "passed": True}])
    assert fresh.gate != "PASS"  # a PASS does not validate grounded scoring


@pytest.mark.parametrize("rows,expected", [
    ([_row(1, True), _row(1, False)], "CONSISTENT + (one judge)"),
    ([_row(1, True), _row(1, True)], "CONSISTENT +"),
    ([_row(-1, True), _row(-1, True)], "CONSISTENT −"),
    ([_row(1, True), _row(-1, False)], "LEANING + (one judge)"),
    ([_row(1, True), _row(-1, True)], "JUDGE-DEPENDENT"),
    ([_row(1, False), _row(-1, False)], "NO DETECTABLE EFFECT"),
])
def test_verdict_labels(rows, expected):
    assert cj.label(rows) == expected


def test_measures_pair_by_question_and_skip_unscored():
    data = {"evaluations": [
        {"question_id": f"Q{i}", "scores": {"baseline": {"aggregate": 80}, "skill": {"aggregate": 90}}}
        for i in range(8)
    ] + [{"question_id": "QN", "scores": {"baseline": {"aggregate": 80}, "skill": {}}}]}
    stored = cj.stored_measure(data)
    row = stored.versus("skill", "baseline")
    assert (row["n"], row["wins"], row["losses"]) == (8, 8, 0)
    assert row["significant"] and row["sign"] == 1

    checklist = {"questions": [
        {"question_id": f"Q{i}", "arms": {"baseline": {"coverage": 1.0}, "skill": {"coverage": 0.0}}}
        for i in range(8)
    ]}
    cov = cj.coverage_measure(checklist, "haiku")
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[stored, cov])
    assert report.summary()["arms"]["skill"]["label"] == "JUDGE-DEPENDENT"
    assert "**JUDGE-DEPENDENT**" in report.format_markdown()


def test_score_measure_drops_failed_answers():
    records = [{"question_id": "Q1", "question_text": "q", "arms": {
        "baseline": {"answer": "ok"}, "skill": {"answer": "boom"}}}]

    def scorer(lib, q, a):
        if a == "boom":
            raise RuntimeError("judge down")
        return 70.0

    m = cj.score_measure(records, lambda l, q, a, g, c: scorer(l, q, a), name="j", library="L", concurrency=2)
    assert m.scores == {("Q1", "baseline"): 70.0, ("Q1", "skill"): None}
    assert m.versus("skill", "baseline")["n"] == 0



def test_cross_judge_rescoring_shows_the_judge_each_arms_context(tmp_path, monkeypatch):
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    seen = {}

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, *, answer, context="", **_):
            seen[answer] = context
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps({"library_name": "L", "answers": [{
        "question_id": "Q1", "question_text": "q", "arms": {
            "baseline": {"answer": "base answer"},
            "skill": {"answer": "skill answer",
                      "context_chunks": [{"snippet": "first"}, {"snippet": "second"}]},
        }}]}), encoding="utf-8")
    args = build_parser().parse_args(["eval", "cross-judge", "--answers", str(answers), "--no-stored",
                                      "--judges", "amazon-bedrock/m", "--output", str(tmp_path / "cj.json")])
    args.func(args)
    assert seen == {"skill answer": "first\nsecond", "base answer": "(No documentation retrieved)"}


def _stamped_run(source="stored"):
    """An arms file judged under judge_context: every score records its context source."""
    def score(agg):
        return {"aggregate": agg, "judge_context_source": source}
    return {
        "library_name": "L", "judge_provider": "amazon-bedrock", "judge_model": "haiku",
        "judge_context": "treatment_material.v1",
        "answers": [{"question_id": f"Q{i}", "question_text": "q", "arms": {
            "baseline": {"answer": f"b{i}", "metadata": {"arm_kind": "baseline"}},
            "skill": {"answer": f"s{i}", "metadata": {"arm_kind": "skill"}},
        }} for i in range(8)],
        "evaluations": [{"question_id": f"Q{i}", "scores": {"baseline": score(70), "skill": score(90)}}
                        for i in range(8)],
    }


BODY = "full skill body"


def _material_run(body=BODY, n=8):
    """An arms file whose skill records and scores all cite ``body``, kept in judge_materials."""
    from dokimi.utils import digest_text

    data = _stamped_run()
    data["answers"], data["evaluations"] = data["answers"][:n], data["evaluations"][:n]
    digest = digest_text(body)
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    data["judge_materials"] = {digest: body}
    return data


def _cross_judge(tmp_path, monkeypatch, data, *extra, judges=True):
    """Run ``eval cross-judge`` on ``data`` with a recording fake Judge.

    Returns ``(exit_code, contexts_seen, args)``; the code is 0 when it did not exit.
    """
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    seen = []

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **kw):
            seen.append(kw.get("context"))
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    path = tmp_path / "run.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(path), "--output", str(tmp_path / "cj.json")]
    if judges:
        argv += ["--judges", "amazon-bedrock/m"]
    args = build_parser().parse_args(argv + list(extra))
    try:
        args.func(args)
    except SystemExit as e:
        return e.code, seen
    return 0, seen


def test_stored_measure_is_named_for_its_judge():
    data = _stamped_run()
    stored = cj.stored_measure(data)
    assert stored.name == "amazon-bedrock/haiku (stored)"
    rescored = cj.Measure(name="amazon-bedrock/haiku", kind="aggregate", scores=dict(stored.scores))
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[stored, rescored])
    # Both measures are Haiku: agreement between them is not agreement across judges.
    assert report.summary()["arms"]["skill"]["label"] == "CONSISTENT + (one judge)"
    assert stored.context == "stored" and rescored.context == "none"
    # A PASS gates only the measure of its own context.
    assert report.apply_validity([{"judge": "amazon-bedrock/haiku", "context": "stored", "passed": True}]) == []
    assert (stored.gate, rescored.gate) == ("PASS", None)
    stored.gate = None
    assert report.apply_validity([{"judge": "amazon-bedrock/haiku", "passed": True}]) == []  # no key: none
    assert (stored.gate, rescored.gate) == (None, "PASS")
    # A FAIL in either context reaches every measure of the judge.
    for ctx in ("stored", "none"):
        stored.gate = rescored.gate = None
        assert report.apply_validity([{"judge": "amazon-bedrock/haiku", "context": ctx, "passed": False}]) == []
        assert (stored.gate, rescored.gate) == ("FAIL", "FAIL")
    assert cj.stored_measure({"evaluations": data["evaluations"]}).name == "stored judge"


def test_unreproducible_arms_are_those_judged_on_material_the_file_lacks():
    assert cj.unreproducible_arms(_stamped_run()) == ["skill"]
    # Before judge_context the stored judge saw the snippets the file keeps.
    unstamped = _stamped_run()
    for ev in unstamped["evaluations"]:
        for s in ev["scores"].values():
            del s["judge_context_source"]
    assert cj.unreproducible_arms(unstamped) == []
    assert cj.unreproducible_arms(_stamped_run("snippet_fallback")) == []


def test_an_arm_whose_material_the_file_keeps_is_still_unreproducible_for_pairwise():
    data = _stamped_run()
    digest = "sha256:" + "0" * 64
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    data["judge_materials"] = {digest: "skill body"}
    # Pairwise and checklist judges see only the answers: unreproducible either way.
    assert cj.unreproducible_arms(data) == ["skill"]


@pytest.mark.parametrize("source", ["snippet_fallback", None])
def test_a_stored_measure_judged_on_snippets_is_none_context_and_gated_by_a_none_report(source):
    data = _stamped_run(source) if source else _stamped_run()
    if source is None:  # a file from before judge_context: no source at all
        for ev in data["evaluations"]:
            for s in ev["scores"].values():
                s.pop("judge_context_source", None)
    stored = cj.stored_measure(data)
    assert stored.context == "none"
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[stored])
    assert report.apply_validity([{"judge": "amazon-bedrock/haiku", "passed": True}]) == []
    assert stored.gate == "PASS"


def test_a_stored_fail_also_silences_the_judges_coverage_and_pairwise_measures():
    measures = [cj.Measure(name="p/j", kind=k, context=c) for k, c in
                (("coverage", "none"), ("pairwise", "none"), ("grounded", "stored"), ("aggregate", "stored"))]
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=measures)
    assert report.apply_validity([{"judge": "p/j", "context": "stored", "passed": False}]) == []
    assert [m.gate for m in measures] == ["FAIL"] * 4


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_a_fail_wins_over_a_pass_in_either_report_order(order):
    reports = [{"judge": "p/j", "context": "stored", "passed": False},
               {"judge": "p/j", "context": "none", "passed": True}]
    measures = [cj.Measure(name="p/j", kind="aggregate", context=c) for c in ("none", "stored")]
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=measures)
    assert report.apply_validity([reports[i] for i in order]) == []
    assert [m.gate for m in measures] == ["FAIL", "FAIL"]


def test_score_measure_hands_the_stored_material_to_the_rescored_judge():
    from dokimi.eval.judge_context import JudgeContextStore

    seen = {}

    def scorer(library, question, answer, ground_truth, context):
        seen[answer] = context
        return 50.0

    records = [{"question_id": "q1", "question_text": "q?",
                "arms": {"skill": {"answer": "a1", "context_chunks": [{"snippet": "snip"}]}}}]
    store = JudgeContextStore()
    store.put("q1", "skill", "full skill body")
    cj.score_measure(records, scorer, name="p/m", library="lib", store=store)
    assert seen["a1"] == "full skill body"
    cj.score_measure(records, scorer, name="p/m", library="lib")
    assert seen["a1"] == "snip"


def test_cross_judge_refuses_to_rescore_against_unreproducible_stored_scores(tmp_path, monkeypatch, capsys):
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(_stamped_run()), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err
    # Without the stored scores every measure sees the same snippets, and it runs.
    args = build_parser().parse_args(argv + ["--no-stored"])
    args.func(args)
    assert (tmp_path / "cj.json").exists()


def test_cross_judge_judges_with_intact_materials_runs_end_to_end(tmp_path, monkeypatch, capsys):
    """--judges with intact judge_materials runs and gate passes."""
    import json
    from dokimi.utils import digest_text

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    contexts_seen = []

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **kw):
            contexts_seen.append(kw.get("context"))
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    skill_body = "full skill body"
    digest = digest_text(skill_body)
    # Stamp the arm records with the digest so from_artifact can rebuild the store.
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    data["judge_materials"] = {digest: skill_body}
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    # Should not raise SystemExit; gate should pass because materials are intact.
    args.func(args)
    assert (tmp_path / "cj.json").exists()
    # Judge was called for each question/arm pair; verify at least some contexts got the stored material
    assert len(contexts_seen) > 0, "Judge should have been called"
    # At least one context should have the stored material (not fallback snippets)
    from dokimi.eval.judge_context import NO_CONTEXT
    assert all("full skill body" in str(c) for c in contexts_seen if c != NO_CONTEXT), contexts_seen
    assert contexts_seen.count("full skill body") == 8, contexts_seen
    out = json.loads((tmp_path / "cj.json").read_text(encoding="utf-8"))
    # Verify the measure was added (stored judge + re-scored judge).
    measures = out.get("summary", {}).get("measures", [])
    assert len(measures) >= 1


def test_cross_judge_judges_refuses_file_without_materials(tmp_path, monkeypatch, capsys):
    """--judges on a file without judge_materials exits 2."""
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    # No judge_materials, but scores claim to be stored (no file before judge_context exists).
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err


def test_cross_judge_judges_with_int_question_ids(tmp_path, monkeypatch, capsys):
    """--judges with int question ids (including 0) works end to end."""
    import json
    from dokimi.utils import digest_text

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    contexts_seen = []

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **kw):
            contexts_seen.append(kw.get("context"))
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    skill_body = "full skill body for int ids"
    digest = digest_text(skill_body)
    # Use int question_ids including 0
    for i, ans in enumerate(data["answers"]):
        ans["question_id"] = i
        ans["arms"]["skill"]["judge_context_sha256"] = digest
    for i, ev in enumerate(data["evaluations"]):
        ev["question_id"] = i
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    data["judge_materials"] = {digest: skill_body}
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    args.func(args)
    assert (tmp_path / "cj.json").exists()
    # Judge was called; at least some contexts should have the stored material
    from dokimi.eval.judge_context import NO_CONTEXT
    skill_ctx = [c for c in contexts_seen if c != NO_CONTEXT]
    assert len(skill_ctx) == 8 and all(c == "full skill body for int ids" for c in skill_ctx), contexts_seen


def test_cross_judge_judges_and_pairwise_refuse_together(tmp_path, monkeypatch, capsys):
    """--judges + --pairwise refuses even with intact materials."""
    import json
    from dokimi.utils import digest_text

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    skill_body = "full skill body"
    digest = digest_text(skill_body)
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    data["judge_materials"] = {digest: skill_body}
    pairwise = tmp_path / "pw.json"
    pairwise.write_text(json.dumps({"verdicts": {"baseline": {}, "skill": {}}, "summary": {"judge": "p/m"}}), encoding="utf-8")
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--pairwise", str(pairwise), "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err


def test_cross_judge_judges_refuses_altered_material_text(tmp_path, monkeypatch, capsys):
    """--judges refuses when judge_materials text doesn't match recorded digest."""
    import json
    from dokimi.utils import digest_text

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    skill_body = "full skill body"
    digest = digest_text(skill_body)
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    # Store altered text with the correct digest key
    data["judge_materials"] = {digest: "ALTERED TEXT"}
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err


def test_cross_judge_judges_refuses_records_without_digest(tmp_path, monkeypatch, capsys):
    """--judges refuses when arm records are missing the digest."""
    import json
    from dokimi.utils import digest_text

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    skill_body = "full skill body"
    digest = digest_text(skill_body)
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest
    data["judge_materials"] = {digest: skill_body}
    # Strip the digest from arm records only
    for ans in data["answers"]:
        if "judge_context_sha256" in ans["arms"]["skill"]:
            del ans["arms"]["skill"]["judge_context_sha256"]
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err


def test_cross_judge_judges_refuses_mismatched_digest_between_arms_and_scores(tmp_path, monkeypatch, capsys):
    """--judges refuses when arm records cite different digest than scores."""
    import json
    from dokimi.utils import digest_text

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _stamped_run()
    skill_body = "full skill body"
    digest1 = digest_text(skill_body)
    digest2 = digest_text("different body")
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest1
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = digest2
    # Only include digest1 in materials (what the arm records cite)
    data["judge_materials"] = {digest1: skill_body}
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--judges", "amazon-bedrock/m",
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err


def test_cross_judge_pairwise_with_materials_refuses_comparison(tmp_path, monkeypatch):
    """--pairwise with stored materials refuses to mix judges."""
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _material_run()
    # Pairwise judges see only the answers, so they still refuse stored material.
    pairwise = tmp_path / "pw.json"
    pairwise.write_text(json.dumps({"verdicts": {"baseline": {}, "skill": {}}, "summary": {"judge": "p/m"}}), encoding="utf-8")
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--pairwise", str(pairwise),
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2


def test_cross_judge_checklist_with_materials_refuses_comparison(tmp_path, monkeypatch):
    """--checklist with stored materials refuses to mix judges."""
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)
    data = _material_run()
    # Checklist judges see only the answers, so they still refuse stored material.
    checklist = tmp_path / "ck.json"
    checklist.write_text(json.dumps({"questions": [], "summary": {"judge": "p/m"}}), encoding="utf-8")
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), "--checklist", str(checklist),
            "--output", str(tmp_path / "cj.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2


def test_a_failed_gate_also_silences_that_judges_coverage_measure():
    absolute = cj.Measure(name="p/j", kind="aggregate")
    coverage = cj.Measure(name="p/j", kind="coverage")
    other = cj.Measure(name="p/k", kind="aggregate")
    for i in range(10):
        for m in (absolute, coverage):
            m.scores.update({(f"Q{i}", "baseline"): 0.2, (f"Q{i}", "skill"): 0.8})
        other.scores.update({(f"Q{i}", "baseline"): 70.0, (f"Q{i}", "skill"): 70.0 + (1 if i % 2 else -1)})
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[absolute, coverage, other])
    report.apply_validity([{"judge": "p/j", "passed": False}])
    assert coverage.gate == "FAIL"
    rows = report.summary()["arms"]["skill"]
    assert [r["measure"] for r in rows["rows"] if r["votes"]] == ["p/k"]
    assert rows["label"] == "NO DETECTABLE EFFECT"
    report.apply_validity([{"judge": "p/j", "passed": True}])
    assert coverage.gate == "FAIL"  # a PASS does not validate checklist coverage


@pytest.mark.parametrize("measure_flag", ["--pairwise", "--checklist"])
def test_cross_judge_refuses_stored_scores_against_pairwise_and_checklist_too(tmp_path, capsys, measure_flag):
    import json

    from dokimi.cli import build_parser

    run = _stamped_run()
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(run), encoding="utf-8")
    other = tmp_path / "other.json"
    qids = [f"Q{i}" for i in range(8)]
    if measure_flag == "--pairwise":
        other.write_text(json.dumps({"summary": {"judge": "p/j pairwise"}, "verdicts": {
            "skill": {q: {"score": -1.0} for q in qids}}}), encoding="utf-8")
    else:
        other.write_text(json.dumps({"summary": {"judge": "p/j"}, "questions": [
            {"question_id": q, "arms": {"baseline": {"coverage": 0.8}, "skill": {"coverage": 0.2}}}
            for q in qids]}), encoding="utf-8")
    argv = ["eval", "cross-judge", "--answers", str(answers), measure_flag, str(other),
            "--output", str(tmp_path / "cj.json")]
    with pytest.raises(SystemExit) as exc:
        args = build_parser().parse_args(argv)
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err
    assert not (tmp_path / "cj.json").exists()
    # The judges that never saw the material can be compared with each other.
    args = build_parser().parse_args(argv + ["--no-stored"])
    args.func(args)
    assert (tmp_path / "cj.json").exists()
    # Stored scores that the file does reproduce (judged on the snippets) are not refused.
    (tmp_path / "cj.json").unlink()
    answers.write_text(json.dumps(_stamped_run("snippet_fallback")), encoding="utf-8")
    args = build_parser().parse_args(argv)
    args.func(args)
    assert (tmp_path / "cj.json").exists()


def test_cross_judge_judges_refuses_a_score_citing_other_material_than_the_records_serve(
    tmp_path, monkeypatch, capsys
):
    """Both digests are in judge_materials, so only comparing them catches it."""
    from dokimi.utils import digest_text

    data = _material_run()
    other = "OTHER SKILL BODY"
    data["judge_materials"][digest_text(other)] = other
    for ans in data["answers"]:
        ans["arms"]["skill"]["judge_context_sha256"] = digest_text(other)
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    err = capsys.readouterr().err
    assert code == 2 and not seen
    assert "different material" in err and "'Q0'" in err and "'Q7'" in err
    assert "written before them" not in err


@pytest.mark.parametrize("damage", ["score_digest", "record_digest", "record_material", "null_qid"])
def test_cross_judge_judges_refuses_one_damaged_question_among_eight(
    tmp_path, monkeypatch, capsys, damage
):
    """The gate must look past the first evaluation: question 5 alone is damaged."""
    from dokimi.utils import digest_text

    data = _material_run()
    other = "OTHER SKILL BODY"
    if damage == "score_digest":
        data["judge_materials"][digest_text(other)] = other
        data["evaluations"][5]["scores"]["skill"]["judge_context_sha256"] = digest_text(other)
    elif damage == "record_digest":
        data["judge_materials"][digest_text(other)] = other
        data["answers"][5]["arms"]["skill"]["judge_context_sha256"] = digest_text(other)
    elif damage == "record_material":
        # The record's text fails its digest, so the reader skips just that question.
        bad = digest_text("X")
        data["judge_materials"][bad] = "not X"
        data["answers"][5]["arms"]["skill"]["judge_context_sha256"] = bad
    else:
        data["evaluations"][5]["question_id"] = None
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    err = capsys.readouterr().err
    assert code == 2 and not seen
    assert "skill" in err and ("None" in err if damage == "null_qid" else "'Q5'" in err)
    assert "'Q4'" not in err and "'Q6'" not in err


@pytest.mark.parametrize("damage", ["score_digest", "record_material"])
def test_cross_judge_judges_gates_an_arm_that_read_material_when_it_is_the_baseline_arm(
    tmp_path, monkeypatch, capsys, damage
):
    """--baseline-arm skill picks the reference, it does not make the skill arm read nothing."""
    from dokimi.utils import digest_text

    data = _material_run()
    # As the arms run writes it: the baseline arm cites the empty text.
    data["judge_materials"][digest_text("")] = ""
    for ans, ev in zip(data["answers"], data["evaluations"], strict=True):
        ans["arms"]["baseline"]["judge_context_sha256"] = digest_text("")
        ev["scores"]["baseline"]["judge_context_sha256"] = digest_text("")
    if damage == "score_digest":
        data["judge_materials"][digest_text("OTHER SKILL BODY")] = "OTHER SKILL BODY"
        data["evaluations"][5]["scores"]["skill"]["judge_context_sha256"] = digest_text("OTHER SKILL BODY")
    else:
        data["judge_materials"][digest_text("X")] = "not X"
        data["answers"][5]["arms"]["skill"]["judge_context_sha256"] = digest_text("X")
    assert _cross_judge(tmp_path, monkeypatch, data)[0] == 2
    capsys.readouterr()
    code, seen = _cross_judge(tmp_path, monkeypatch, data, "--baseline-arm", "skill")
    err = capsys.readouterr().err
    assert code == 2 and not seen
    assert "for skill" in err and "'Q5'" in err and "for baseline" not in err


def test_cross_judge_judges_skips_an_evaluation_with_no_answer_record(tmp_path, monkeypatch, capsys):
    """score_measure never scores it, so it is no reason to refuse."""
    data = _material_run()
    del data["answers"][7]
    # Its score may cite any digest, held or not: it is never re-scored.
    data["evaluations"][7]["scores"]["skill"]["judge_context_sha256"] = "sha256:" + "e" * 64
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    assert code == 0, capsys.readouterr().err
    assert seen.count(BODY) == 7


def test_cross_judge_judges_refuses_one_damaged_int_question_among_eight(tmp_path, monkeypatch, capsys):
    from dokimi.utils import digest_text

    data = _material_run()
    for i, (ans, ev) in enumerate(zip(data["answers"], data["evaluations"], strict=True)):
        ans["question_id"] = ev["question_id"] = i
    other = "OTHER SKILL BODY"
    data["judge_materials"][digest_text(other)] = other
    data["evaluations"][5]["scores"]["skill"]["judge_context_sha256"] = digest_text(other)
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    err = capsys.readouterr().err
    assert code == 2 and not seen
    assert "question(s) 5:" in err


def test_cross_judge_judges_accepts_a_snippet_fallback_score(tmp_path, monkeypatch, capsys):
    """A score judged on snippets (no digest) is not gated on stored material."""
    data = _material_run()
    s = data["evaluations"][4]["scores"]["skill"]
    s["judge_context_source"] = "snippet_fallback"
    s.pop("judge_context_sha256", None)
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    assert code == 0, capsys.readouterr().err
    assert seen.count(BODY) == 8


def test_cross_judge_judges_exits_2_for_an_unhashable_question_id(tmp_path, monkeypatch, capsys):
    """origin/main exits 2 here; the gate loop must not raise TypeError."""
    data = _material_run()
    data["evaluations"][0] = {"question_id": ["a"],
                              "scores": {"skill": {"error": "x", "judge_context_source": "stored"}}}
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    assert code == 2 and not seen
    assert "['a']" in capsys.readouterr().err


def test_cross_judge_judges_on_an_answers_only_file_hands_the_judge_the_material(tmp_path, monkeypatch):
    """No stored scores: the gate does not run, and the judge must still get the text."""
    data = _material_run()
    del data["evaluations"]
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    assert code == 0
    assert seen.count(BODY) == 8


def test_cross_judge_judges_with_no_stored_hands_the_judge_the_material(tmp_path, monkeypatch):
    from dokimi.utils import digest_text

    data = _material_run()
    # A stored score that would fail the gate: --no-stored skips it, not the reader.
    data["evaluations"][2]["scores"]["skill"]["judge_context_sha256"] = digest_text("elsewhere")
    code, seen = _cross_judge(tmp_path, monkeypatch, data, "--no-stored")
    assert code == 0
    assert seen.count(BODY) == 8


def test_cross_judge_builds_the_material_store_once_and_only_for_judges(tmp_path, monkeypatch, caplog):
    data = _material_run()
    data["answers"][3]["arms"]["skill"]["judge_context_sha256"] = "sha256:" + "f" * 64
    with caplog.at_level("WARNING"):
        _cross_judge(tmp_path, monkeypatch, data, "--no-stored")
    assert sum("cite judge material" in r.getMessage() for r in caplog.records) == 1
    caplog.clear()
    # With the gate running (no --no-stored), the store is still built once.
    with caplog.at_level("WARNING"):
        _cross_judge(tmp_path, monkeypatch, data)
    assert sum("cite judge material" in r.getMessage() for r in caplog.records) == 1
    caplog.clear()
    checklist = tmp_path / "ck.json"
    checklist.write_text('{"questions": [], "summary": {"judge": "p/m"}}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        _cross_judge(tmp_path, monkeypatch, data, "--checklist", str(checklist), "--no-stored", judges=False)
    assert not any("cite judge material" in r.getMessage() for r in caplog.records)


def test_cross_judge_judges_handles_falsy_question_ids(tmp_path, monkeypatch):
    """Int ids, including 0, resolve in the gate and the reader alike (distinct ids)."""
    data = _material_run()
    for i, (ans, ev) in enumerate(zip(data["answers"], data["evaluations"], strict=True)):
        ans["question_id"] = ev["question_id"] = i
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    assert code == 0
    assert seen.count(BODY) == 8


def test_cross_judge_judges_refuses_one_damaged_question_among_eight_int_ids(
    tmp_path, monkeypatch, capsys
):
    """The gate reads int ids too: question 5 alone is damaged, and its id is the int 5."""
    from dokimi.utils import digest_text

    data = _material_run()
    for i, (ans, ev) in enumerate(zip(data["answers"], data["evaluations"], strict=True)):
        ans["question_id"] = ev["question_id"] = i
    other = "OTHER SKILL BODY"
    data["judge_materials"][digest_text(other)] = other
    data["evaluations"][5]["scores"]["skill"]["judge_context_sha256"] = digest_text(other)
    code, seen = _cross_judge(tmp_path, monkeypatch, data)
    assert code == 2 and not seen
    assert "different material" in capsys.readouterr().err


def test_from_artifact_skips_a_malformed_answer_list_and_non_scalar_ids():
    """No exception; a record with a list id is skipped and counted, the rest are served."""
    from dokimi.eval.judge_context import JudgeContextStore

    data = _material_run(n=2)
    data["answers"][0]["question_id"] = ["a"]  # from_artifact reads the answer records
    store = JudgeContextStore.from_artifact(data)
    assert store.get("Q1", "skill") == BODY and list(store._ctx) == [("Q1", "skill")]
    data["answers"] = 5
    assert JudgeContextStore.from_artifact(data).materials() == {}


def test_digest_text_tells_a_lone_surrogate_from_its_removal():
    """errors="ignore" would hash a lone-surrogate text and its stripped form alike."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.utils import digest_text

    assert digest_text("a\ud83db") != digest_text("ab")
    data = _material_run(body="a\ud83db", n=1)
    assert JudgeContextStore.from_artifact(data).get("Q0", "skill") == "a\ud83db"
    (digest,) = data["judge_materials"]
    data["judge_materials"][digest] = "ab"
    assert JudgeContextStore.from_artifact(data).get("Q0", "skill") is None


@pytest.mark.parametrize("kind", ["grounded", "coverage"])
def test_a_failed_gate_reaches_a_judge_whose_only_measure_is_grounded_or_coverage(kind):
    # --grounded without --both, or --checklist alone, leaves no aggregate measure for the judge.
    only = cj.Measure(name="p/j", kind=kind)
    other = cj.Measure(name="p/k", kind="aggregate")
    for i in range(10):
        only.scores.update({(f"Q{i}", "baseline"): 0.8, (f"Q{i}", "skill"): 0.2})
        other.scores.update({(f"Q{i}", "baseline"): 70.0, (f"Q{i}", "skill"): 70.0 + (1 if i % 2 else -1)})
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[only, other])
    assert report.apply_validity([{"judge": "p/j", "passed": False}]) == []
    assert only.gate == "FAIL"
    assert report.summary()["arms"]["skill"]["label"] == "NO DETECTABLE EFFECT"
    # A PASS validates neither, and says so: it matched nothing.
    only.gate = None
    assert report.apply_validity([{"judge": "p/j", "passed": True}]) == ["p/j"]
    assert only.gate is None


def test_significance_uses_the_unrounded_holm_p():
    rows = [{"sign": 1, "p_sign": 0.0166662, "measure": f"p/j{i}"} for i in range(3)]
    # Holm: 3 x 0.0166662 = 0.0499986, significant; displayed to 4 decimals it is 0.05.
    assert cj.label(rows) == "CONSISTENT +"
    assert all(r["significant"] for r in rows)
    assert {r["p_holm"] for r in rows} == {0.05}


def test_digest_text_surrogatepass_handles_lone_surrogates():
    """digest_text uses surrogatepass to handle lone surrogates without raising."""
    from dokimi.utils import digest_text

    # A lone surrogate pair should not raise but produce a digest
    result = digest_text("a\ud83db")
    assert result.startswith("sha256:") and len(result) == 71


def test_from_artifact_guards_against_malformed_data():
    """from_artifact checks digest, record, and arms types without raising."""
    from dokimi.eval.judge_context import JudgeContextStore

    # List digest on arm record
    store = JudgeContextStore.from_artifact({
        "judge_materials": {},
        "answers": [{
            "question_id": "q1",
            "arms": {"a": {"judge_context_sha256": ["x"]}},
        }],
    })
    assert store.materials() == {}

    # Non-dict record
    store = JudgeContextStore.from_artifact({
        "judge_materials": {},
        "answers": [1],
    })
    assert store.materials() == {}

    # Arms as a list
    store = JudgeContextStore.from_artifact({
        "judge_materials": {},
        "answers": [{"question_id": "q1", "arms": [1]}],
    })
    assert store.materials() == {}

    # An arm entry that is not a dict, next to one that is served
    data = _material_run(n=1)
    data["answers"][0]["arms"]["broken"] = 1
    store = JudgeContextStore.from_artifact(data)
    assert store.get("Q0", "skill") == BODY and store.get("Q0", "broken") is None


def test_unreproducible_arms_skips_malformed_records_and_scores():
    """Records, arms, evaluations and scores of the wrong type are skipped, not raised on."""
    from dokimi.eval import cross_judge as cj

    data = _stamped_run()
    data["answers"] += [5, {"question_id": "Qa", "arms": [1]},
                        {"question_id": "Qb", "arms": {"skill": 3}}]
    data["evaluations"] += [7, {"question_id": "Qc", "scores": 3},
                            {"question_id": "Qd", "scores": {"skill": "x"}}]
    assert cj.unreproducible_arms(data) == ["skill"]


def test_unreproducible_arms_with_non_string_score_digest():
    """unreproducible_arms counts non-string digests as unreproducible."""
    from dokimi.eval import cross_judge as cj

    data = _stamped_run()
    # Replace score digest with a list
    for ev in data["evaluations"]:
        ev["scores"]["skill"]["judge_context_sha256"] = ["x"]
    result = cj.unreproducible_arms(data)
    assert "skill" in result

# --- pairwise ---------------------------------------------------------------

from dokimi.eval import pairwise as pw  # noqa: E402


def _ab_ask(better, *, consistent=True):
    """A judge that prefers the answer containing ``better``; optionally position-biased."""
    def ask(prompt, **_):
        a = prompt.split("**Answer A:**\n", 1)[1].split("\n\n**Answer B:**", 1)[0]
        if not consistent:
            return "A"
        return "A" if better in a else "B"
    return ask


def test_parse_choice():
    assert pw.parse_choice("**B**") == "B"
    assert pw.parse_choice("tie") == "TIE"
    assert pw.parse_choice("Answer: TIE") == "TIE"
    # The article is not verdict A.
    assert pw.parse_choice("Both are fine, so it is a TIE.") == "TIE"
    assert pw.parse_choice("Answer A is better: A") == "A"
    assert pw.parse_choice("neither") is None


def test_pairwise_needs_both_orders_to_agree():
    good = pw.PairwiseJudge(ask=_ab_ask("GOOD"))
    assert good.compare("L", "q", "GOOD x", "bad y").score == 1
    assert good.compare("L", "q", "bad y", "GOOD x").score == -1
    biased = pw.PairwiseJudge(ask=_ab_ask("GOOD", consistent=False))
    assert biased.compare("L", "q", "GOOD x", "bad y").score == 0  # always "A" is position, not preference


def test_pairwise_escalates_only_undecided_pairs():
    calls = []

    def expensive(prompt, **_):
        calls.append(prompt)
        return _ab_ask("GOOD")(prompt)

    judge = pw.PairwiseJudge(ask=_ab_ask("GOOD", consistent=False), escalate=expensive)
    v = judge.compare("L", "q", "GOOD x", "bad y")
    assert (v.score, v.escalated, len(calls)) == (1, True, 2)
    decided = pw.PairwiseJudge(ask=_ab_ask("GOOD"), escalate=expensive)
    assert decided.compare("L", "q", "GOOD x", "bad y").escalated is False
    assert len(calls) == 2


def test_unparseable_reply_is_an_error_not_a_tie():
    judge = pw.PairwiseJudge(ask=lambda p, **_: "", escalate=lambda p, **_: "A")
    with pytest.raises(pw.UnparseableVerdict):
        judge.compare("L", "q", "x", "y")
    records = [{"question_id": "Q1", "question_text": "q", "arms": {"baseline": {"answer": "b"}, "s": {"answer": "a"}}}]
    report = pw.run_arm_pairwise(records, pw.PairwiseJudge(ask=lambda p, **_: "I cannot decide"))
    assert report.verdicts == {} and len(report.errors) == 1


def test_eval_pairwise_writes_the_errors_when_every_comparison_fails(tmp_path, monkeypatch):
    import json

    from dokimi.cli import build_parser
    from dokimi.commands import judge_reliability as jr

    monkeypatch.setattr(jr, "pairwise_judge",
                        lambda args: (pw.PairwiseJudge(ask=lambda p, **_: "no idea"), "fake"))
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps({"answers": [{"question_id": "Q1", "question_text": "q", "arms": {
        "baseline": {"answer": "b"}, "skill": {"answer": "s"}}}]}), encoding="utf-8")
    out = tmp_path / "pw.json"
    args = build_parser().parse_args(["eval", "pairwise", "--answers", str(answers), "--output", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    errors = json.loads(out.read_text(encoding="utf-8"))["errors"]
    assert [e["question_id"] for e in errors] == ["Q1"] and "UnparseableVerdict" in errors[0]["error"]


def test_consistent_ties_are_kept_unless_asked():
    calls = []

    def expensive(prompt, **_):
        calls.append(prompt)
        return "A"

    tie = pw.PairwiseJudge(ask=lambda p, **_: "TIE", escalate=expensive)
    assert (tie.compare("L", "q", "x", "y").escalated, len(calls)) == (False, 0)
    tie.escalate_ties = True
    assert tie.compare("L", "q", "x", "y").escalated is True


def test_pairwise_drives_the_validity_gate():
    answers = _many_answers()
    intact = {a["arms"]["baseline"]["answer"] for a in answers}

    def ask(prompt, **_):
        a = prompt.split("**Answer A:**\n", 1)[1].split("\n\n**Answer B:**", 1)[0]
        return "A" if a in intact else "B"

    scorer = pw.PairwiseJudge(ask=ask).pair_scorer()
    report = run_sequential(answers, lambda *_: 0.0, max_bases=40, variants=["truncate_20", "padding"],
                            pair_scorer=scorer)
    s = report.summary()
    assert s["passed"] and s["per_variant"]["truncate_20"]["sequential"]["decision"] == "PASS"


def test_arm_pairwise_report_and_cross_judge_measure():
    records = [{"question_id": f"Q{i}", "question_text": "q", "arms": {
        "baseline": {"answer": "plain"}, "skill": {"answer": "GOOD"}}} for i in range(7)]
    report = pw.run_arm_pairwise(records, pw.PairwiseJudge(ask=_ab_ask("GOOD")), label="fake", concurrency=3)
    r = report.summary()["arms"]["skill"]
    assert (r["wins"], r["losses"], r["ties"]) == (7, 0, 0) and r["p_sign"] < 0.05
    assert "| `skill` | 7 | 0 | 0 |" in report.format_markdown()
    m = cj.pairwise_measure(report.to_dict(), "fake", "baseline")
    assert m.versus("skill", "baseline")["wins"] == 7


def test_a_judge_that_failed_its_gate_does_not_vote():
    good = cj.Measure(name="p/strong", kind="aggregate")
    bad = cj.Measure(name="p/weak", kind="aggregate")
    for i in range(8):
        good.scores.update({(f"Q{i}", "baseline"): 70.0, (f"Q{i}", "skill"): 80.0})
        bad.scores.update({(f"Q{i}", "baseline"): 80.0, (f"Q{i}", "skill"): 70.0})
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[good, bad])
    assert report.summary()["arms"]["skill"]["label"] == "JUDGE-DEPENDENT"
    unmatched = report.apply_validity([
        {"judge": "p/weak", "passed": False}, {"judge": "p/strong", "passed": True}, {"judge": "p/other"},
    ])
    assert unmatched == ["p/other"]
    assert report.summary()["arms"]["skill"]["label"] == "CONSISTENT + (one judge)"
    md = report.format_markdown()
    assert "| ✗ |" in md and "| ✓ |" in md
    unvalidated = cj.Measure(name="p/unknown", kind="aggregate", scores=dict(bad.scores))
    report.measures.append(unvalidated)
    assert report.summary()["arms"]["skill"]["label"] == "JUDGE-DEPENDENT"
    report.require_validated = True
    assert report.summary()["arms"]["skill"]["label"] == "CONSISTENT + (one judge)"


def test_escalation_without_pairwise_mode_is_refused(tmp_path):
    from dokimi.cli import build_parser

    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(tmp_path / "a.json"),
                                      "--escalate-model", "m"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2


# --- verdict parsing inside prose, and checklist rendering without a parsed point ---------


@pytest.mark.parametrize("reply, expected", [
    ("Neither A nor B is wrong, so TIE", None),
    ("B is better than A.", None),
    ("Answer A omits the unit. Answer B is complete. Verdict: B", None),
    ("A, B, or TIE", None),
    ("Reply with exactly one token: A, B, or TIE.", None),
    ("<think>Compare A and B</think>\nB", "B"),
    ("<think>A says 8, B says 8.</think>**TIE**", "TIE"),
    ("<think>Answer A cites 8 threads and", None),
    ("A tie", "TIE"),
    ("A TIE.", "TIE"),
    ("Both are fine, so it is a TIE.", "TIE"),
    ("The better answer is B.", "B"),
    ("Answer A is better: A", "A"),
    ("**b**", "B"),
    ("B\n\nAnswer B is more accurate than A.", "B"),
    ("A\nreason: B lacks the number", "A"),
    ("B is better than A.\nA lacks the unit.", None),
    ("", None),
])
def test_parse_choice_needs_one_verdict(reply, expected):
    assert pw.parse_choice(reply) == expected


@pytest.mark.parametrize("reply, expected", [
    ("There is no explicit mention, but it is covered: YES", True),
    ("NO. The answer never states it, and YES would be wrong.", None),
    ("Reply with exactly one word: YES or NO.", None),
    ("<think>maybe NO</think>YES", True),
    ("<think>maybe NO", None),
    ("**No**", False),
    ("NO - the answer never mentions it", False),
    ("YES\nIt says NO threads are needed, so the point is covered.", True),
    ("NO.\nThe answer never states it, and YES would be wrong.", False),
])
def test_parse_yes_no_needs_one_verdict(reply, expected):
    assert ck.parse_yes_no(reply) is expected


def test_a_reply_naming_two_verdicts_is_redrawn_not_read_as_the_first():
    replies = iter(["Neither A nor B is wrong, so TIE", "TIE"])
    verdict, raw = pw.ask_parsed(lambda p, **_: next(replies), "p", pw.parse_choice)
    assert (verdict, raw) == ("TIE", "TIE")
    judge = pw.PairwiseJudge(ask=lambda p, **_: "A or B?")
    with pytest.raises(pw.UnparseableVerdict):
        judge.compare("L", "q", "x", "y")


def _arm_with_no_parsed_point():
    def ask(prompt, **_):
        if "ANSWER:\nunreadable" in prompt:
            return "Verdict:"
        return "YES" if "cover this" in prompt else "NO"

    records = [{"question_id": f"Q{i}", "question_text": "q",
                "arms": {"baseline": {"answer": "fine"}, "skill": {"answer": "unreadable"}}} for i in (1, 2)]
    return ck.run_checklist(records, {"Q1": ["p1", "p2"], "Q2": ["p1"]}, ask)


def test_checklist_markdown_renders_an_arm_with_no_parsed_point():
    report = _arm_with_no_parsed_point()
    md = report.format_markdown()
    row = next(line for line in md.splitlines() if line.startswith("| `skill`"))
    assert row == "| `skill` | - | [-, -] | - | - | 0/0/0 |  | - |"
    assert "3 point(s) got no usable reply" in md
    assert next(line for line in md.splitlines() if line.startswith("| `baseline`")).startswith("| `baseline` | 1.000")


def test_eval_checklist_writes_the_json_before_rendering(tmp_path, monkeypatch):
    import json

    from dokimi.cli import build_parser
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            self.llm = MagicMock()
            self.llm.invoke = lambda prompt, **_: MagicMock(content="YES" if "cover this" in prompt else "NO")

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)

    def boom(self, *_, **__):
        raise RuntimeError("render failed")

    monkeypatch.setattr(ck.ChecklistReport, "format_markdown", boom)
    answers, questions, out = tmp_path / "run.json", tmp_path / "q.json", tmp_path / "ck.json"
    answers.write_text(json.dumps({"answers": [{"question_id": "Q1", "question_text": "q", "arms": {
        "baseline": {"answer": "b"}, "skill": {"answer": "s"}}}]}), encoding="utf-8")
    questions.write_text(json.dumps({"questions": [{"id": "Q1", "expected_topics": ["p"]}]}), encoding="utf-8")
    args = build_parser().parse_args(["eval", "checklist", "--answers", str(answers),
                                      "--questions", str(questions), "--output", str(out)])
    with pytest.raises(RuntimeError, match="render failed"):
        args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["n_questions"] == 1


def test_score_measure_marks_a_measure_stored_only_if_an_arm_saw_material():
    from dokimi.eval.judge_context import JudgeContextStore

    records = [{"question_id": "q1", "question_text": "q?",
                "arms": {"baseline": {"answer": "a0"}, "skill": {"answer": "a1"}}}]

    def scorer(library, question, answer, ground_truth, context):
        return 50.0

    real = JudgeContextStore()
    real.put("q1", "skill", "real material")
    # the baseline answer falls back to snippets, so the judge saw both kinds: mixed, not stored
    assert cj.score_measure(records, scorer, name="p/m", library="lib", store=real).context == "mixed"
    real.put("q1", "baseline", "other material")
    assert cj.score_measure(records, scorer, name="p/m", library="lib", store=real).context == "stored"
    # An empty stored baseline is still "stored" (the arm was judged with the no-context
    # marker), and the skill answer falls back to snippets: a mix, which validates nothing.
    empty = JudgeContextStore()
    empty.put("q1", "baseline", "")
    assert cj.score_measure(records, scorer, name="p/m", library="lib", store=empty).context == "mixed"
    assert cj.score_measure(records, scorer, name="p/m", library="lib").context == "none"


def _material_arms_run(material="FULL SKILL BODY"):
    """An arms file that keeps its judge material, with digests on answers and stored scores."""
    from dokimi.utils import digest_text

    digest = digest_text(material)
    return {
        "library_name": "L", "judge_provider": "amazon-bedrock", "judge_model": "m",
        "judge_context": "treatment_material.v1",
        "judge_materials": {digest: material},
        "answers": [{"question_id": f"Q{i}", "question_text": "q", "arms": {
            "baseline": {"answer": f"base {i}", "metadata": {"arm_kind": "baseline"}},
            "skill": {"answer": f"skill {i}", "metadata": {"arm_kind": "skill"},
                      "judge_context_sha256": digest},
        }} for i in range(8)],
        "evaluations": [{"question_id": f"Q{i}", "scores": {
            "baseline": {"aggregate": 70, "judge_context_source": "stored"},
            "skill": {"aggregate": 90, "judge_context_source": "stored",
                      "judge_context_sha256": digest}}} for i in range(8)],
    }


def _cross_judge_args(tmp_path, data, *extra):
    import json

    from dokimi.cli import build_parser

    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data), encoding="utf-8")
    return build_parser().parse_args(["eval", "cross-judge", "--answers", str(answers),
                                      "--output", str(tmp_path / "cj.json"), *extra])


def _recording_judge(monkeypatch, seen):
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, *, answer, context="", **_):
            seen[answer] = context
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)


def test_cross_judge_judges_hands_the_arms_material_to_the_rescored_judge(tmp_path, monkeypatch):
    seen = {}
    _recording_judge(monkeypatch, seen)
    args = _cross_judge_args(tmp_path, _material_arms_run(), "--judges", "amazon-bedrock/m")
    args.func(args)
    assert seen["skill 0"] == "FULL SKILL BODY"


def test_cross_judge_judges_refuses_edited_material(tmp_path, monkeypatch, capsys):
    seen = {}
    _recording_judge(monkeypatch, seen)
    data = _material_arms_run()
    digest = next(iter(data["judge_materials"]))
    data["judge_materials"][digest] = "EDITED"
    args = _cross_judge_args(tmp_path, data, "--judges", "amazon-bedrock/m")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err
    assert not seen


def test_cross_judge_judges_with_pairwise_refuses_stored_scores(tmp_path, monkeypatch, capsys):
    seen = {}
    _recording_judge(monkeypatch, seen)
    args = _cross_judge_args(tmp_path, _material_arms_run(), "--judges", "amazon-bedrock/m",
                             "--pairwise", str(tmp_path / "pw.json"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "skill" in capsys.readouterr().err



def test_a_stored_measure_mixing_stored_and_snippet_scores_is_mixed_and_no_pass_validates_it():
    data = _stamped_run()
    for ev in data["evaluations"][:4]:
        for s in ev["scores"].values():
            s["judge_context_source"] = "snippet_fallback"
    assert cj.stored_measure(data).context == "mixed"
    assert cj.stored_measure(_stamped_run()).context == "stored"
    for context in ("stored", "none"):
        m = cj.stored_measure(data)
        report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[m])
        report.apply_validity([{"judge": "amazon-bedrock/haiku", "passed": True, "context": context}])
        assert m.gate is None
    m = cj.stored_measure(data)
    report = cj.CrossJudgeReport(baseline_arm="baseline", measures=[m])
    report.apply_validity([{"judge": "amazon-bedrock/haiku", "passed": False, "context": "stored"}])
    assert m.gate == "FAIL"

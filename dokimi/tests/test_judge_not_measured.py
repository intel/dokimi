# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Nothing measured is not a pass: the cross-judge and judge-wording verdicts, and their
`--strict` exits, when no voter or condition scored a single question."""

from dokimi.eval import cross_judge as cj
from dokimi.eval.judge_wording import WordingReport

from tests.test_judge_reliability import _stamped_run


def test_one_voter_that_compared_nothing_does_not_unmeasure_what_another_compared():
    measured = {"sign": 1, "p_sign": 0.001, "n": 8}
    errored = {"sign": 0, "p_sign": 1.0, "n": 0}
    assert cj.label([measured, errored]) == "CONSISTENT + (one judge)"
    assert cj.label([errored, dict(errored)]) == "NOT MEASURED"


def _strict_exit(argv):
    from dokimi.cli import build_parser

    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit as e:
        return e.code
    return 0


def test_cross_judge_strict_exits_1_when_no_arm_was_measured(tmp_path, monkeypatch, capsys):
    import json

    from dokimi.eval import judge as judge_mod

    class ErroringJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, **_):
            raise RuntimeError("ThrottlingException")

    monkeypatch.setattr(judge_mod, "Judge", ErroringJudge)
    errored = tmp_path / "errored.json"
    errored.write_text(json.dumps(_stamped_run()), encoding="utf-8")
    # Nothing to compare: the stored judge scored only the baseline arm.
    unpaired = _stamped_run()
    for ev in unpaired["evaluations"]:
        del ev["scores"]["skill"]
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps(unpaired), encoding="utf-8")
    for argv, arms in ((["--answers", str(errored), "--no-stored", "--judges", "amazon-bedrock/m"], {"skill": "NOT MEASURED"}),
                       (["--answers", str(empty)], {})):
        code = _strict_exit(["eval", "cross-judge", *argv, "--output", str(tmp_path / "cj.json"), "--strict"])
        assert code == 1
        assert "✗ incomplete" in capsys.readouterr().err
        summary = json.loads((tmp_path / "cj.json").read_text())["summary"]
        assert {a: r["label"] for a, r in summary["arms"].items()} == arms


def test_cross_judge_strict_exits_1_when_only_one_of_several_arms_was_measured(tmp_path, monkeypatch, capsys):
    import json

    from dokimi.eval import judge as judge_mod

    class HalfErroringJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, *, answer, **_):
            if answer.startswith("t"):  # the third arm's answers
                raise RuntimeError("ThrottlingException")
            return {"aggregate": 90.0 if answer.startswith("s") else 50.0}

    monkeypatch.setattr(judge_mod, "Judge", HalfErroringJudge)
    data = _stamped_run()
    for ans in data["answers"]:
        ans["arms"]["skill2"] = {"answer": "t" + ans["arms"]["skill"]["answer"][1:],
                                 "metadata": {"arm_kind": "skill"}}
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code = _strict_exit(["eval", "cross-judge", "--answers", str(path), "--no-stored", "--judges",
                         "amazon-bedrock/m", "--output", str(tmp_path / "cj.json"), "--strict"])
    summary = json.loads((tmp_path / "cj.json").read_text())["summary"]
    assert summary["arms"]["skill"]["label"] != "NOT MEASURED"
    assert summary["arms"]["skill2"]["label"] == "NOT MEASURED"
    assert code == 1
    assert "1 of 2 arm(s) NOT MEASURED: skill2" in capsys.readouterr().err


def test_judge_wording_strict_exits_1_when_one_condition_scored_and_another_did_not(tmp_path, monkeypatch, capsys):
    import json

    from dokimi.eval import judge_wording
    from dokimi.eval.judge_validity import ValidityReport
    from dokimi.eval.validity_scoring import ValidityRow

    from tests.test_judge_validity import _answers as validity_answers

    def rows(**kw):
        return [ValidityRow(base_id=f"Q{i}", arm="baseline", variant="wrong_numbers", **kw) for i in range(4)]

    report = WordingReport(judge="fake", reports={
        "original": ValidityReport(judge="fake", rows=rows(intact_score=90.0, degraded_score=40.0)),
        "persona": ValidityReport(judge="fake", rows=rows(error="ThrottlingException"))})
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(validity_answers()), encoding="utf-8")
    monkeypatch.setattr(judge_wording, "run_wording", lambda *a, **k: report)
    code = _strict_exit(["eval", "judge-wording", "--answers", str(answers), "--bases", "2",
                         "--judge-provider", "amazon-bedrock", "--judge-model", "m", "--strict"])
    out = capsys.readouterr()
    assert report.summary()["unmeasured_conditions"] == ["persona"]
    assert code == 1
    assert "No rewrite moves" not in out.out and "incomplete" in out.err


def test_judge_wording_strict_exits_1_when_a_condition_scored_no_pair(tmp_path, monkeypatch, capsys):
    import json

    from dokimi.eval import judge_wording
    from dokimi.eval.judge_validity import ValidityReport
    from dokimi.eval.validity_scoring import ValidityRow

    from tests.test_judge_validity import _answers as validity_answers

    def errored(n):
        return ValidityReport(judge="fake", rows=[
            ValidityRow(base_id=f"Q{i}", arm="baseline", variant="wrong_numbers", error="ThrottlingException")
            for i in range(n)])

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(validity_answers()), encoding="utf-8")
    for n in (0, 4):  # no pair built; every call errored
        report = WordingReport(judge="fake", reports={c: errored(n) for c in ("original", "persona")})
        monkeypatch.setattr(judge_wording, "run_wording", lambda *a, _r=report, **k: _r)
        code = _strict_exit(["eval", "judge-wording", "--answers", str(answers), "--bases", "2",
                             "--judge-provider", "amazon-bedrock", "--judge-model", "m", "--strict"])
        assert code == 1
        out = capsys.readouterr()
        assert "NOT MEASURED — no pair scored under: original, persona" in out.out
        assert "No rewrite moves" not in out.out and "incomplete" in out.err
        assert report.summary()["n_errors"] == {"original": n, "persona": n}

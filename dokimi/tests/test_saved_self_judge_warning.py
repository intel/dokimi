# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Every judge path that reads a saved file warns (never refuses) on self-judging.

The call-site tests run each command through the real parser with the warning replaced by
a stop, so a command that drops its call fails here before any model is called.
"""

import json

import pytest

from dokimi.cli import parse_args
from dokimi.utils import warn_recorded_self_judge

WARN = "Evaluator independence warning"
SELF = {"model": "gpt-6.1-sol", "provider": "openai"}


class Stop(Exception):
    pass


def _stop_recording(seen):
    """A stand-in for warn_recorded_self_judge: records the judges it is given, then stops the command."""
    def stop(data, judges, context, **_):
        seen.extend(judges)
        raise Stop
    return stop


def test_recorded_same_model_warns_once(capsys):
    assert warn_recorded_self_judge(
        {**SELF, "answers": []}, [("claude-sonnet-5-5", "anthropic"), ("gpt-6.1-sol", "openai")], "x")
    assert capsys.readouterr().err.count(WARN) == 1


def test_repeated_judge_warns_once_per_distinct_judge(capsys):
    judges = [("gpt-6.1-sol", "openai"), ("gpt-6.1-sol", "openai"), ("gpt-6.1-sol", "anthropic")]
    assert warn_recorded_self_judge(SELF, judges, "x")
    assert capsys.readouterr().err.count(WARN) == 2


def test_recorded_without_model_is_silent(capsys):
    assert not warn_recorded_self_judge({"answers": []}, [("gpt-6.1-sol", "openai")], "x")
    assert not warn_recorded_self_judge([], [("gpt-6.1-sol", "openai")], "x")
    assert capsys.readouterr().err == ""


def test_roles_name_the_two_sides(capsys):
    warn_recorded_self_judge(SELF, [("gpt-6.1-sol", "openai")], "x", roles=("generator", "reviewer"))
    err = capsys.readouterr().err
    assert "generator model equals reviewer model" in err
    assert "answer model" not in err


LONG = " Call fit() on the training data, then predict() on the test rows, and check the score." * 4


def _arms_file(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(json.dumps({**SELF, "library_name": "L", "baseline_arm": "baseline", "answers": [{
        "question_id": "Q1", "question_text": "q", "arms": {
            "baseline": {"answer": "base answer" + LONG}, "skill": {"answer": "skill answer" + LONG}}}]}),
        encoding="utf-8")
    return p


def _questions_file(tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({**SELF, "questions": [{"question": "How?"}]}), encoding="utf-8")
    return p


def _judge_argv(tmp_path):
    a, q, out = str(_arms_file(tmp_path)), str(_questions_file(tmp_path)), str(tmp_path / "o.json")
    j = ["--judge-provider", "openai"]
    return {
        "dokimi.commands.judge_reliability": [
            ["eval", "score", "pairwise", "--answers", a, "--output", out, *j],
            ["eval", "score", "checklist", "--answers", a, "--questions", q, "--output", out, *j],
            ["eval", "judge", "wording", "--answers", a, "--output", out, *j],
            ["eval", "judge", "agreement", "--answers", a, "--no-stored", "--judges", "openai/gpt-6.1-sol",
             "--output", out],
        ],
        "dokimi.commands.evaluate": [
            ["eval", "judge", "validate", "--answers", a, "--output", out, *j],
            ["eval", "score", "ragas", "--answers", a, "--ragas-provider", "openai",
             "--ragas-model", "gpt-6.1-sol"],
        ],
        "dokimi.utils": [
            ["eval", "score", "reference", "--answers", a, "--questions", q, "--output", out, *j],
        ],
        "dokimi.commands.questions": [
            ["eval", "questions", "analyze", "--questions", q, "--product", "L", "--output", out],
            ["eval", "questions", "review", "--questions", q, "--product", "L", "--output", out],
        ],
    }


CASES = [  # (module whose warn_recorded_self_judge the command calls, command words)
    ("dokimi.commands.judge_reliability", ("eval", "score", "pairwise")),
    ("dokimi.commands.judge_reliability", ("eval", "score", "checklist")),
    ("dokimi.commands.judge_reliability", ("eval", "judge", "wording")),
    ("dokimi.commands.judge_reliability", ("eval", "judge", "agreement")),
    ("dokimi.commands.evaluate", ("eval", "judge", "validate")),
    ("dokimi.commands.evaluate", ("eval", "score", "ragas")),
    ("dokimi.utils", ("eval", "score", "reference")),
    ("dokimi.commands.questions", ("eval", "questions", "analyze")),
    ("dokimi.commands.questions", ("eval", "questions", "review")),
]


@pytest.mark.parametrize("module,words", CASES, ids=[" ".join(w) for _, w in CASES])
def test_command_checks_the_recorded_model(module, words, tmp_path, monkeypatch):
    import importlib

    argv = next(a for argvs in _judge_argv(tmp_path).values() for a in argvs if tuple(a[:3]) == words)

    seen = []

    def stop(data, judges, context, roles=("answer", "judge")):
        seen.append((context, list(judges), roles, data))
        raise Stop

    monkeypatch.setattr(importlib.import_module(module), "warn_recorded_self_judge", stop)
    monkeypatch.setattr("dokimi.commands.evaluate._require_ragas", lambda: None)
    args = parse_args(argv)
    with pytest.raises(Stop):
        args.func(args)
    assert seen[0][0] == " ".join(words)
    assert ("gpt-6.1-sol", "openai") in seen[0][1]
    assert seen[0][2] == (("generator", "reviewer") if module.endswith("questions") else ("answer", "judge"))
    assert isinstance(seen[0][3], dict) and seen[0][3].get("model") == "gpt-6.1-sol"


def test_reference_with_no_judge_does_not_warn(tmp_path, monkeypatch):
    from dokimi.utils import warn_recorded_self_judge as real
    import dokimi.utils

    calls = []
    monkeypatch.setattr(dokimi.utils, "warn_recorded_self_judge", lambda *a, **k: calls.append(a) or real(*a, **k))
    args = parse_args(["eval", "score", "reference", "--answers", str(_arms_file(tmp_path)),
                       "--questions", str(_questions_file(tmp_path)), "--no-judge",
                       "--output", str(tmp_path / "o.json")])
    try:
        args.func(args)
    except SystemExit:
        pass
    assert calls == []


def test_validate_pairwise_checks_the_escalation_judge(tmp_path, monkeypatch):
    from dokimi.commands import evaluate

    seen = []
    monkeypatch.setattr(evaluate, "warn_recorded_self_judge", _stop_recording(seen))
    args = parse_args(["eval", "judge", "validate", "--answers", str(_arms_file(tmp_path)),
                       "--mode", "pairwise", "--judge-provider", "anthropic", "--escalate-model", "gpt-6.1-sol",
                       "--escalate-provider", "openai", "--output", str(tmp_path / "o.json")])
    with pytest.raises(Stop):
        args.func(args)
    assert ("gpt-6.1-sol", "openai") in seen


def test_pairwise_checks_the_escalation_judge_too(tmp_path, monkeypatch):
    from dokimi.commands import judge_reliability

    seen = []
    monkeypatch.setattr(judge_reliability, "warn_recorded_self_judge", _stop_recording(seen))
    args = parse_args(["eval", "score", "pairwise", "--answers", str(_arms_file(tmp_path)),
                       "--judge-provider", "anthropic", "--escalate-model", "gpt-6.1-sol",
                       "--escalate-provider", "openai", "--output", str(tmp_path / "o.json")])
    with pytest.raises(Stop):
        args.func(args)
    assert ("gpt-6.1-sol", "openai") in seen


def test_escalation_defaults_to_the_judge_provider_and_is_in_the_label(tmp_path, monkeypatch):
    from dokimi.commands.judge_reliability import _pairwise_judges, pairwise_judge

    built = []

    class FakeJudge:
        def __init__(self, model, provider, temperature=None):
            built.append((model, provider))
            self.llm = None

    monkeypatch.setattr("dokimi.eval.judge.Judge", FakeJudge)
    args = parse_args(["eval", "score", "pairwise", "--answers", str(_arms_file(tmp_path)),
                       "--judge-provider", "anthropic", "--escalate-model", "claude-opus-5-5"])
    assert _pairwise_judges(args)[1] == ("claude-opus-5-5", "anthropic")
    _, label = pairwise_judge(args)
    assert label.endswith(" pairwise → anthropic/claude-opus-5-5")  # stored as the measure name
    assert ("claude-opus-5-5", "anthropic") in built


def test_validate_with_a_typed_scorer_does_not_warn(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from dokimi.commands import evaluate

    seen = []  # a valid spec, so the run reaches the check instead of exiting at parse_spec
    monkeypatch.setattr("dokimi.eval.degrade.build_pairs", lambda *a, **k: [object()])
    monkeypatch.setattr("dokimi.eval.typed_scorer.typed_scorer",
                        lambda *a, **k: SimpleNamespace(name="typed", effective_max_len=None))
    monkeypatch.setattr(evaluate, "warn_recorded_self_judge", _stop_recording(seen))
    args = parse_args(["eval", "judge", "validate", "--answers", str(_arms_file(tmp_path)),
                       "--scorer", "typed:laya", "--judge-provider", "openai",
                       "--output", str(tmp_path / "o.json")])
    with pytest.raises(Stop):
        args.func(args)
    assert seen == []


def test_questions_review_on_an_answers_file_does_not_take_the_answer_model_as_generator(tmp_path, monkeypatch):
    from dokimi.commands import questions

    seen = []

    def stop(data, judges, context, **_):
        seen.append(data)
        raise Stop

    monkeypatch.setattr(questions, "warn_recorded_self_judge", stop)
    args = parse_args(["eval", "questions", "review", "--questions", str(_arms_file(tmp_path)),
                       "--product", "L", "--output", str(tmp_path / "o.json")])
    with pytest.raises(Stop):
        args.func(args)
    assert not seen[0].get("model")


@pytest.mark.parametrize("argv_tail,words", [
    (["--wordings", "nope"], ("eval", "judge", "wording")),
    (["--variants", "nope"], ("eval", "judge", "wording")),
    (["--variants", "nope"], ("eval", "judge", "validate")),
    (["--scorer", "nope"], ("eval", "judge", "validate")),
    (["--context", "stored"], ("eval", "judge", "validate")),
], ids=["wording-wordings", "wording-variants", "validate-variants", "validate-scorer", "validate-stored"])
def test_a_refused_run_does_not_warn(argv_tail, words, tmp_path, capsys):
    argv = next(a for argvs in _judge_argv(tmp_path).values() for a in argvs if tuple(a[:3]) == words)
    args = parse_args([*argv, *argv_tail])
    with pytest.raises((SystemExit, ValueError)):  # InputError is a ValueError; main turns it into exit 2
        args.func(args)
    assert WARN not in capsys.readouterr().err


def test_checklist_on_a_binary_answers_file_refuses_without_warning(tmp_path, capsys):
    p = tmp_path / "bin.json"
    p.write_text(json.dumps({**SELF, "answers": [{"question_id": "Q1", "question_text": "q",
                                                  "answer": "a" + LONG}]}), encoding="utf-8")
    args = parse_args(["eval", "score", "checklist", "--answers", str(p), "--questions",
                       str(_questions_file(tmp_path)), "--judge-provider", "openai",
                       "--output", str(tmp_path / "o.json")])
    with pytest.raises((SystemExit, ValueError)):  # InputError is a ValueError; main turns it into exit 2
        args.func(args)
    assert WARN not in capsys.readouterr().err

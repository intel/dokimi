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


def test_recorded_same_model_warns_once(capsys):
    assert warn_recorded_self_judge(
        {**SELF, "answers": []}, [("claude-sonnet-5-5", "anthropic"), ("gpt-6.1-sol", "openai")], "x")
    assert capsys.readouterr().err.count(WARN) == 1


def test_recorded_without_model_is_silent(capsys):
    assert not warn_recorded_self_judge({"answers": []}, [("gpt-6.1-sol", "openai")], "x")
    assert not warn_recorded_self_judge([], [("gpt-6.1-sol", "openai")], "x")
    assert capsys.readouterr().err == ""


def test_roles_name_the_two_sides(capsys):
    warn_recorded_self_judge(SELF, [("gpt-6.1-sol", "openai")], "x", roles=("generator", "reviewer"))
    err = capsys.readouterr().err
    assert "generator model equals reviewer model" in err
    assert "answer model" not in err


def _arms_file(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(json.dumps({**SELF, "library_name": "L", "baseline_arm": "baseline", "answers": [{
        "question_id": "Q1", "question_text": "q", "arms": {
            "baseline": {"answer": "base answer"}, "skill": {"answer": "skill answer"}}}]}),
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
        ],
        "dokimi.utils": [
            ["eval", "score", "reference", "--answers", a, "--questions", q, "--output", out, *j],
        ],
        "dokimi.commands.questions": [
            ["eval", "questions", "analyze", "--questions", q, "--product", "L", "--output", out],
            ["eval", "questions", "review", "--questions", q, "--product", "L", "--output", out],
        ],
    }


def _cases():
    import pathlib
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp())
    return [(mod, argv) for mod, argvs in _judge_argv(tmp).items() for argv in argvs]


@pytest.mark.parametrize("module,argv", _cases(), ids=lambda v: v if isinstance(v, str) else " ".join(v[:3]))
def test_command_checks_the_recorded_model(module, argv, monkeypatch):
    import importlib

    seen = []

    def stop(data, judges, context, roles=("answer", "judge")):
        seen.append((context, list(judges), roles))
        raise Stop

    monkeypatch.setattr(importlib.import_module(module), "warn_recorded_self_judge", stop)
    args = parse_args(argv)
    with pytest.raises(Stop):
        args.func(args)
    assert ("gpt-6.1-sol", "openai") in seen[0][1]


def test_pairwise_checks_the_escalation_judge_too(tmp_path, monkeypatch):
    from dokimi.commands import judge_reliability

    seen = []
    monkeypatch.setattr(judge_reliability, "warn_recorded_self_judge",
                        lambda data, judges, context, **_: (seen.extend(judges), (_ for _ in ()).throw(Stop)))
    args = parse_args(["eval", "score", "pairwise", "--answers", str(_arms_file(tmp_path)),
                       "--judge-provider", "anthropic", "--escalate-model", "gpt-6.1-sol",
                       "--escalate-provider", "openai", "--output", str(tmp_path / "o.json")])
    with pytest.raises(Stop):
        args.func(args)
    assert ("gpt-6.1-sol", "openai") in seen

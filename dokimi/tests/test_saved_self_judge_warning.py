"""Re-judging saved answers or reviewing generated questions warns (never refuses) on self-judging."""

import argparse

from dokimi.commands.judge_reliability import _warn_saved_answers
from dokimi.commands.questions import _warn_same_as_generator

WARN = "Evaluator independence warning"


def test_saved_answers_same_model_warns(capsys):
    data = {"model": "gpt-6.1-sol", "provider": "openai", "answers": []}
    _warn_saved_answers(data, [("claude-sonnet-5-5", "anthropic"), ("gpt-6.1-sol", "openai")], "x")
    assert capsys.readouterr().err.count(WARN) == 1


def test_saved_answers_without_model_is_silent(capsys):
    _warn_saved_answers({"answers": []}, [("gpt-6.1-sol", "openai")], "x")
    _warn_saved_answers([], [("gpt-6.1-sol", "openai")], "x")
    assert capsys.readouterr().err == ""


def test_question_reviewer_same_as_generator_warns(capsys):
    args = argparse.Namespace(model="gpt-6.1-sol", provider="openai")
    _warn_same_as_generator({"model": "gpt-6.1-sol", "provider": "openai", "questions": []}, args, "x")
    assert WARN in capsys.readouterr().err


def test_question_reviewer_other_family_is_silent(capsys):
    args = argparse.Namespace(model="claude-sonnet-5-5", provider="anthropic")
    _warn_same_as_generator({"model": "gpt-6.1-sol", "provider": "openai", "questions": []}, args, "x")
    _warn_same_as_generator({"questions": []}, args, "x")
    assert capsys.readouterr().err == ""

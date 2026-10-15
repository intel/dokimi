# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`baseline compare` names a change of answer or judge model between the two runs."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands import baseline as baseline_mod


def _eval(answer=("openai", "gpt-6.1-sol"), judge=("anthropic", "claude-sonnet-5-5"), delta=2.0) -> dict:
    return {
        "judge_provider": judge[0],
        "judge_model": judge[1],
        "run_metadata": {"answer_provider": answer[0], "answer_model": answer[1]},
        "evaluations": [{
            "question_id": "q1",
            "with_docs": {"aggregate": 7.0},
            "without_docs": {"aggregate": 7.0 - delta},
            "delta": delta,
        }],
    }


@pytest.fixture
def compare(tmp_path, monkeypatch):
    """Save ``base`` as a baseline, then run `baseline compare` on ``current``."""
    monkeypatch.setattr(baseline_mod, "BASELINES_DIR", tmp_path / "baselines")

    def run(base: dict, current: dict, *extra: str) -> None:
        (tmp_path / "base.json").write_text(json.dumps(base), encoding="utf-8")
        (tmp_path / "cur.json").write_text(json.dumps(current), encoding="utf-8")
        parser = build_parser()
        args = parser.parse_args(["baseline", "save", "--from-eval", str(tmp_path / "base.json"),
                                  "--name", "b"])
        args.func(args)
        args = parser.parse_args(["baseline", "compare", "--baseline", "b",
                                  "--eval", str(tmp_path / "cur.json"), *extra])
        args.func(args)

    return run


def test_compare_warns_when_the_answer_and_judge_models_differ(compare, capsys):
    old = _eval(answer=("openai", "gpt-4o-mini"), judge=("openai", "gpt-4o-mini"), delta=0.0)
    compare(old, _eval())
    out, err = capsys.readouterr()
    assert "Baseline model warning" in err
    assert "answer model: baseline openai/gpt-4o-mini, current openai/gpt-6.1-sol" in err
    assert "judge model: baseline openai/gpt-4o-mini, current anthropic/claude-sonnet-5-5" in err
    assert "Avg delta" in out  # a warning, not a refusal


def test_compare_strict_refuses_a_model_mismatch(compare, capsys):
    old = _eval(judge=("openai", "gpt-4o-mini"))
    with pytest.raises(SystemExit) as exc:
        compare(old, _eval(), "--strict")
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert "--strict" in err and "judge model: baseline openai/gpt-4o-mini" in err
    assert "answer model" not in err
    assert "Avg delta" not in out


def test_compare_is_silent_when_the_models_match(compare, capsys):
    compare(_eval(delta=1.0), _eval(), "--strict")
    out, err = capsys.readouterr()
    assert "warning" not in err.lower() and err == ""
    assert "Avg delta" in out


def test_compare_treats_a_bedrock_alias_and_its_profile_as_one_judge(compare, capsys):
    from dokimi.defaults import BEDROCK_MODEL_IDS

    bare = "claude-sonnet-5-5"
    compare(_eval(judge=("amazon-bedrock", bare)),
            _eval(judge=("amazon-bedrock", BEDROCK_MODEL_IDS[bare])), "--strict")
    assert capsys.readouterr().err == ""


def test_compare_skips_a_role_the_baseline_does_not_record(compare, capsys):
    old = _eval()
    del old["run_metadata"]
    compare(old, _eval(), "--strict")
    assert capsys.readouterr().err == ""

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


def test_compare_strict_exits_2_on_a_malformed_record(compare, capsys):
    """Exit 1 is the --strict verdict; a record with no question_id is an input error."""
    current = _eval()
    del current["evaluations"][0]["question_id"]
    with pytest.raises(SystemExit) as exc:
        compare(_eval(), current, "--strict")
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "missing question_id" in err and "missing question_id" not in out


def test_compare_strict_exits_2_on_a_malformed_record_when_no_models_are_recorded(compare, capsys):
    """The model checks must not run first: a baseline with no recorded models is still read."""
    base = {"evaluations": _eval()["evaluations"]}
    current = _eval()
    del current["evaluations"][0]["question_id"]
    with pytest.raises(SystemExit) as exc:
        compare(base, current, "--strict")
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "missing question_id" in err and "--strict cannot verify" not in err


def test_compare_strict_exits_2_on_a_malformed_saved_baseline_record(tmp_path, monkeypatch, capsys):
    """The saved baseline is read before the --strict model checks, so a malformed one exits 2."""
    monkeypatch.setattr(baseline_mod, "BASELINES_DIR", tmp_path / "baselines")
    (tmp_path / "base.json").write_text(json.dumps(_eval(judge=("openai", "gpt-4o-mini"))), encoding="utf-8")
    (tmp_path / "cur.json").write_text(json.dumps(_eval()), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(["baseline", "save", "--from-eval", str(tmp_path / "base.json"), "--name", "b"])
    args.func(args)
    saved = tmp_path / "baselines" / "b.json"  # the baseline records a judge model, so --strict would fire
    data = json.loads(saved.read_text(encoding="utf-8"))
    del data["evaluations"][0]["question_id"]
    saved.write_text(json.dumps(data), encoding="utf-8")
    args = parser.parse_args(["baseline", "compare", "--baseline", "b",
                              "--eval", str(tmp_path / "cur.json"), "--strict"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "missing question_id" in err and "--strict" not in err
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


def test_compare_names_a_role_the_baseline_does_not_record(compare, capsys):
    old = _eval()
    del old["run_metadata"]
    compare(old, _eval())
    out, err = capsys.readouterr()
    assert "answer model not recorded in baseline; not compared" in err
    assert "judge model" not in err
    assert "Avg delta" in out


def test_compare_strict_refuses_a_role_it_cannot_verify(compare, capsys):
    old = _eval()
    del old["run_metadata"]
    with pytest.raises(SystemExit) as exc:
        compare(old, _eval(), "--strict")
    assert exc.value.code == 1
    assert "--strict cannot verify the answer model" in capsys.readouterr().err


def test_an_unknown_answer_model_is_not_a_model_change(compare, capsys):
    # `eval score` writes "unknown" when the answers file names no model.
    old = _eval(answer=("unknown", "unknown"))
    compare(old, _eval())
    err = capsys.readouterr().err
    assert "answer model not recorded in baseline; not compared" in err
    assert "unknown/unknown" not in err and "Baseline model warning" not in err


def test_a_judge_recorded_only_in_run_metadata_is_compared(compare, capsys):
    old = _eval(judge=("openai", "gpt-4o-mini"))
    old["run_metadata"].update(judge_provider=old.pop("judge_provider"),
                               judge_model=old.pop("judge_model"))
    compare(old, _eval())
    assert "judge model: baseline openai/gpt-4o-mini, current anthropic/claude-sonnet-5-5" \
        in capsys.readouterr().err


def test_provider_and_model_case_and_whitespace_do_not_count_as_a_change(compare, capsys):
    compare(_eval(answer=(" OpenAI ", "GPT-6.1-sol "), judge=("Anthropic", "Claude-Sonnet-5-5")),
            _eval(), "--strict")
    assert capsys.readouterr().err == ""


def _panel(*models: str) -> dict:
    data = _eval()
    del data["judge_provider"]
    data["judge_model"] = f"panel({len(models)})"
    data["panel_config"] = {"size": len(models), "models": list(models)}
    return data


def test_compare_strict_refuses_a_panel_whose_judges_changed(compare, capsys):
    with pytest.raises(SystemExit) as exc:
        compare(_panel("anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
                _panel("openai/gpt-4o-mini", "openai/gpt-4o-mini"), "--strict")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "judge model: baseline panel/anthropic/claude-sonnet-5-5" in err
    assert "current panel/openai/gpt-4o-mini" in err


def test_a_panel_with_the_same_judges_in_another_order_is_one_judge(compare, capsys):
    compare(_panel("anthropic/claude-sonnet-5-5", "amazon-bedrock/claude-opus-5-5"),
            _panel("amazon-bedrock/us.anthropic.claude-opus-5-5", "Anthropic/claude-sonnet-5-5"),
            "--strict")
    assert capsys.readouterr().err == ""


def test_panel_score_records_the_answer_model_for_compare(tmp_path):
    from dokimi.eval.panel import JudgeConfig, JudgePanel, _save_incremental

    panel = JudgePanel(judges=[JudgeConfig(role="r", model="m", provider="anthropic")])
    path = tmp_path / "p.json"
    _save_incremental([], path, "lib", panel, {"answer_model": "a", "answer_provider": "openai"})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert baseline_mod._recorded_models(data) == {
        "answer": ("openai", "a"), "judge": ("panel", "anthropic/m")}



def _unscored() -> dict:
    data = _eval()
    data["evaluations"][0]["delta"] = None
    return data


@pytest.mark.parametrize("current", [_unscored(), {**_eval(), "evaluations": []}], ids=["unscored", "empty"])
def test_compare_with_nothing_scored_on_both_sides_is_not_no_change(compare, capsys, current):
    # An unscored or empty current eval printed "✅ No notable changes vs baseline."
    compare(_eval(), current)
    out = capsys.readouterr().out
    assert "Nothing to compare (0 scored)" in out
    assert "No notable changes" not in out


def test_compare_with_a_scored_pair_and_no_move_says_how_many_it_compared(compare, capsys):
    compare(_eval(), _eval(delta=2.5))
    assert "No notable changes vs baseline (1 of 1 questions compared)" in capsys.readouterr().out


def test_compare_counts_only_questions_scored_on_both_sides(compare, capsys):
    base, cur = _eval(), _eval(delta=2.5)
    extra = [{**cur["evaluations"][0], "question_id": f"extra{i}", "delta": 1.0} for i in range(2)]
    for e in extra:
        e.pop("key", None)
    cur["evaluations"] += extra
    compare(base, cur)
    assert "(1 of 3 questions compared)" in capsys.readouterr().out


@pytest.mark.outcome("cjm-baseline-regression")
@pytest.mark.parametrize("flag", ["--max-context-drop", "--max-lift-drop"])
def test_regression_gate_uses_unrounded_scores_not_stored_delta(compare, capsys, flag):
    base, cur = _eval(), _eval()
    cur["evaluations"][0]["with_docs"]["aggregate"] = 6.999
    with pytest.raises(SystemExit) as exc:
        compare(base, cur, flag, "0")
    assert exc.value.code == 1
    assert "REGRESSION" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--max-context-drop", "--max-lift-drop"])
def test_regression_gate_allows_equality(compare, capsys, flag):
    base, cur = _eval(), _eval()
    cur["evaluations"][0]["with_docs"]["aggregate"] = 6.5
    compare(base, cur, flag, "0.5")
    assert "Regression gate: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["empty", "unmatched", "unscored", "nan", "duplicate", "unknown", "mismatch"])
def test_regression_gate_refuses_incomplete_evidence(compare, capsys, kind):
    base, cur = _eval(), _eval()
    row = cur["evaluations"][0]
    if kind == "empty":
        cur["evaluations"] = []
    elif kind == "unmatched":
        row["question_id"] = "another"
    elif kind == "unscored":
        row["without_docs"] = None
    elif kind == "nan":
        row["with_docs"]["aggregate"] = float("nan")
    elif kind == "duplicate":
        cur["evaluations"].append(dict(row))
    elif kind == "unknown":
        cur.pop("run_metadata")
    else:
        cur["judge_model"] = "other"
    with pytest.raises(SystemExit) as exc:
        compare(base, cur, "--max-context-drop", "0")
    assert exc.value.code == 1
    assert "Regression gate: PASS" not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "oops"])
def test_regression_threshold_requires_finite_nonnegative_number(value, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["baseline", "compare", "--eval", "x", "--max-lift-drop", value])
    assert exc.value.code == 2
    assert "finite non-negative" in capsys.readouterr().err


def test_regression_minimum_matched_count(compare, capsys):
    with pytest.raises(SystemExit) as exc:
        compare(_eval(), _eval(), "--max-context-drop", "0", "--min-matched", "2")
    assert exc.value.code == 1
    assert "insufficient" in capsys.readouterr().err


def test_strict_remains_identity_only_even_for_large_regression(compare, capsys):
    cur = _eval(delta=-5)
    cur["evaluations"][0]["with_docs"]["aggregate"] = 0
    compare(_eval(), cur, "--strict")
    assert "Regression gate" not in capsys.readouterr().out


@pytest.mark.parametrize("score, limit", [(6.9, "0.1"), (6.8, "0.2"), (6.7, "0.3")])
def test_decimal_threshold_equality_passes(compare, capsys, score, limit):
    base, cur = _eval(), _eval()
    cur["evaluations"][0]["with_docs"]["aggregate"] = score
    compare(base, cur, "--max-context-drop", limit, "--max-lift-drop", limit)
    assert "Regression gate: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("flag, fails", [("--max-context-drop", False), ("--max-lift-drop", True)])
def test_context_score_and_lift_are_distinct_metrics(compare, capsys, flag, fails):
    base, cur = _eval(), _eval()
    # Context quality is unchanged, but the unaided model improved: lift fell.
    cur["evaluations"][0]["without_docs"]["aggregate"] = 6.0
    if fails:
        with pytest.raises(SystemExit) as exc:
            compare(base, cur, flag, "0")
        assert exc.value.code == 1
        assert "paired lift: drop=1" in capsys.readouterr().out
    else:
        compare(base, cur, flag, "0")
        assert "Regression gate: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("side", ["base", "current"])
def test_gate_refuses_partial_question_coverage(compare, capsys, side):
    base, cur = _eval(), _eval()
    data = base if side == "base" else cur
    data["evaluations"].append({**data["evaluations"][0], "question_id": "extra"})
    with pytest.raises(SystemExit) as exc:
        compare(base, cur, "--max-lift-drop", "100")
    assert exc.value.code == 1
    assert "complete unique coverage required" in capsys.readouterr().err


def test_gate_matches_questions_by_id_not_list_position(compare, capsys):
    base, cur = _eval(), _eval()
    base["evaluations"].append({**_eval(delta=3)["evaluations"][0], "question_id": "q2"})
    cur["evaluations"] = list(reversed(base["evaluations"]))
    compare(base, cur, "--max-context-drop", "0", "--max-lift-drop", "0", "--min-matched", "2")
    out = capsys.readouterr().out
    assert "(2 matched)" in out and "Regression gate: PASS" in out


@pytest.mark.parametrize("model", ["unknown/unknown", "openai/ Unknown ", "bare-model"])
def test_regression_gate_refuses_unknown_panel_members(compare, capsys, model):
    with pytest.raises(SystemExit) as exc:
        compare(_panel(model), _panel(model), "--max-context-drop", "0")
    assert exc.value.code == 1
    assert "cannot verify the judge model" in capsys.readouterr().err

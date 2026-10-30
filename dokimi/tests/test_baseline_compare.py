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


def test_one_judge_id_and_snapshot_behind_another_route_is_one_judge_model():
    dated = _eval(judge=("anthropic", "claude-sonnet-4-5-20250929"))
    assert baseline_mod._model_mismatches(dated, _eval(judge=("amazon-bedrock", "us.anthropic.claude-sonnet-4-5-20250929"))) == (
        [], [])
    alias = _eval(judge=("anthropic", "claude-sonnet-4-5"))
    assert baseline_mod._model_mismatches(alias, _eval(judge=("amazon-bedrock", "us.anthropic.claude-sonnet-4-5"))) == (
        [], [])


def test_a_dated_snapshot_and_its_undated_alias_are_not_verified_in_either_direction():
    """The alias does not record which snapshot answered: a role that was not measured, so it is named, not matched."""
    dated = _eval(judge=("anthropic", "claude-sonnet-4-5-20250929"))
    alias = _eval(judge=("anthropic", "claude-sonnet-4-5"))
    assert baseline_mod._model_mismatches(dated, alias) == ([], ["judge"])
    assert baseline_mod._model_mismatches(alias, dated) == ([], ["judge"])


def test_a_panel_is_the_same_only_when_its_judges_pair_one_to_one():
    verdict = baseline_mod._recorded_verdict
    assert verdict(("panel", "anthropic/m,anthropic/m"), ("panel", "m,anthropic/m")) == "match"
    assert verdict(("panel", "anthropic/m,anthropic/m,anthropic/m"), ("panel", "anthropic/m,anthropic/m")) == "mismatch"
    assert verdict(("panel", "m-20240101,m-20240101"), ("panel", "m,m-20250101")) == "mismatch"
    assert verdict(("panel", "m"), ("anthropic", "m")) == "mismatch"


def test_a_panel_pairs_its_judges_by_augmenting_paths_not_first_match():
    """``m`` matches both snapshots and ``m-20240101`` only its own: a first-match pairing gives ``m`` the
    judge ``m-20240101`` and strands the second baseline judge, though a valid pairing exists. The pairing
    alone is only "not verified" (a dated judge against an undated one), never a match."""
    verdict = baseline_mod._recorded_verdict
    assert verdict(("panel", "m,m-20240101"), ("panel", "m-20240101,m-20250101")) == "unverified"
    assert verdict(("panel", "m-20240101,m-20250101"), ("panel", "m,m-20240101")) == "unverified"  # the reverse side
    assert verdict(("panel", "m-20240101,m"), ("panel", "m-20240101,m-20250101")) == "unverified"
    assert verdict(("panel", "m-20240101,m-20240101"), ("panel", "m,m-20250101")) == "mismatch"
    assert verdict(("panel", "m,m-20240101"), ("panel", "m-20240101,m")) == "match"  # the same judges, reordered


def test_a_panel_of_names_with_no_id_is_a_mismatch_and_digit_ids_still_match():
    """A judge name with no id (separators only) is no model, so two such panels are not the same panel;
    a digit-only id is an id, as it was on this branch before the round's first fix."""
    verdict = baseline_mod._recorded_verdict
    assert verdict(("panel", "/,/"), ("panel", "-,-")) == "mismatch"
    assert verdict(("panel", "/,/"), ("panel", "/,/")) == "mismatch"
    assert verdict(("anthropic", "8"), ("anthropic", "8")) == "match"
    assert verdict(("panel", "anthropic/8,anthropic/9"), ("panel", "openai/9,openai/8")) == "match"


def test_a_panel_that_gained_a_judge_is_a_mismatch():
    """Every judge of the baseline panel can pair with one of the current, and a third one is added:
    pairing alone would pass it, so the sizes are compared first."""
    verdict = baseline_mod._recorded_verdict
    assert verdict(("panel", "m,m-20240101"), ("panel", "m,m-20240101,m")) == "mismatch"
    assert verdict(("panel", "m,m-20240101,m"), ("panel", "m,m-20240101")) == "mismatch"


def test_a_panel_is_matched_by_its_judges_multiset_not_their_set():
    """The same distinct judges in the same number, one of them twice in the baseline and not in the current:
    comparing sets would call the two panels the same, comparing multisets does not."""
    verdict = baseline_mod._recorded_verdict
    assert verdict(("panel", "anthropic/m,anthropic/m,openai/n"),
                   ("panel", "anthropic/m,openai/n,openai/n")) == "mismatch"


def test_custom_dotted_names_and_a_huggingface_organization_are_different_models():
    """A custom deployment name is not its first dotted word, and a Hugging Face organization is part of the id:
    `--strict` refuses both changes."""
    verdict = baseline_mod._recorded_verdict
    assert verdict(("azure", "alice.model"), ("azure", "bob.model")) == "mismatch"
    assert verdict(("huggingface", "huggingface/google/gemma-2-9b-it"), ("huggingface", "gemma-2-9b-it")) == "mismatch"
    lines, _ = baseline_mod._model_mismatches(
        _eval(judge=("azure", "alice.model")), _eval(judge=("azure", "bob.model")))
    assert lines == ["judge model: baseline azure/alice.model, current azure/bob.model"]


def test_two_snapshots_of_one_judge_model_are_a_mismatch():
    lines, _ = baseline_mod._model_mismatches(
        _eval(judge=("anthropic", "claude-sonnet-4-5-20240620")),
        _eval(judge=("anthropic", "claude-sonnet-4-5-20250929")))
    assert lines == ["judge model: baseline anthropic/claude-sonnet-4-5-20240620, "
                     "current anthropic/claude-sonnet-4-5-20250929"]


@pytest.mark.parametrize("base_judge, cur_judge", [
    ("claude-sonnet-4-5-20240620", "claude-sonnet-4-5"),  # the baseline dated, the current an undated alias
    ("claude-sonnet-4-5", "claude-sonnet-4-5-20240620"),  # and the other way round
])
def test_compare_strict_refuses_a_dated_judge_against_its_undated_alias(compare, capsys, base_judge, cur_judge):
    """The alias does not say which snapshot answered, so --strict cannot verify that the judge did not change."""
    with pytest.raises(SystemExit) as exc:
        compare(_eval(judge=("anthropic", base_judge)), _eval(judge=("anthropic", cur_judge)), "--strict")
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert "--strict cannot verify the judge model" in err
    assert "Avg delta" not in out


def test_compare_warns_that_a_dated_judge_against_its_undated_alias_is_not_verified(compare, capsys):
    compare(_eval(judge=("anthropic", "claude-sonnet-4-5-20240620")), _eval(judge=("anthropic", "claude-sonnet-4-5")))
    out, err = capsys.readouterr()
    assert "judge model" in err and "not verified" in err
    assert "Baseline model warning" not in err and "judge model: baseline" not in err
    assert "Avg delta" in out


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

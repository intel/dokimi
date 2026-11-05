"""arms run / subjects run warn (never refuse) when the judge is the answer model."""

from dokimi.eval.arms_run import ArmsRunConfig, warn_self_judging


def test_same_model_warns(capsys):
    cfg = ArmsRunConfig(product="p", model="gpt-6.1-sol", provider="openai",
                        judge=True, judge_model="gpt-6.1-sol", judge_provider="openai")
    assert warn_self_judging(cfg) is True
    assert "Evaluator independence warning" in capsys.readouterr().err


def test_other_family_is_silent(capsys):
    cfg = ArmsRunConfig(product="p", model="gpt-6.1-sol", provider="openai",
                        judge=True, judge_model="claude-sonnet-5-5", judge_provider="anthropic")
    assert warn_self_judging(cfg) is False
    assert capsys.readouterr().err == ""

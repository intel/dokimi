# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The answer/judge/generator defaults every CLI surface and library class is fed."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
import yaml

from dokimi import config, defaults
from dokimi.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]

# Pinned on purpose, by value: a default decides which model answers and which one
# judges, so moving one changes every default-flag result and its cost and has to show
# up here as a deliberate edit. Every surface shares the same three pairs.
ANSWER = ("gpt-6.1-sol", "openai")
GENERATOR = ("gpt-6.1-sol", "openai")
JUDGE = ("claude-sonnet-5-5", "anthropic")


def test_the_defaults_are_the_pinned_values():
    assert (defaults.DEFAULT_ANSWER_MODEL, defaults.DEFAULT_ANSWER_PROVIDER) == ANSWER
    assert (defaults.DEFAULT_GENERATOR_MODEL, defaults.DEFAULT_GENERATOR_PROVIDER) == GENERATOR
    assert (defaults.DEFAULT_JUDGE_MODEL, defaults.DEFAULT_JUDGE_PROVIDER) == JUDGE
    # Cross-family, so the judge-independence warning stays quiet on default flags.
    assert ANSWER[1] != JUDGE[1]


def test_config_products_yaml_names_the_same_defaults():
    llm = yaml.safe_load(config.DEFAULT_PRODUCTS_PATH.read_text(encoding="utf-8"))["llm"]
    assert llm["answerer"]["default"] == ANSWER[0]
    assert llm["judge"]["default"] == JUDGE[0]
    assert llm["generator"]["default"] == GENERATOR[0]


def test_no_source_file_still_names_gpt_4o_mini():
    retired = "-".join(("gpt-4o", "mini"))  # spelled so that this file does not match
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "dokimi").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
        if retired in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize("argv, expected", [
    (["arms", "run", "--product", "p", "--questions", "q", "--arms", "baseline"],
     {"model": ANSWER, "judge": JUDGE}),
    (["arms", "matrix-run", "--product", "p", "--questions", "q", "--arms", "baseline",
      "--matrix-cells", "m"], {"model": ANSWER, "judge": JUDGE}),
    (["subjects", "run", "s.toml"], {"model": ANSWER, "judge": JUDGE}),
    (["evaluate", "--product", "p"], {"model": ANSWER, "judge": JUDGE}),
    (["benchmark", "run", "--target", "t"], {"model": ANSWER, "judge": JUDGE}),
    (["eval", "score", "--answers", "a", "--product", "p"], {"judge": JUDGE}),
    (["eval", "panel-score", "--answers", "a", "--product", "p"], {"model": JUDGE}),
    (["eval", "ragas", "--answers", "a"], {"ragas": JUDGE}),
    (["answers", "generate", "--product", "p", "--questions", "q"],
     {"model": ANSWER, "ragas": JUDGE}),
    (["questions", "generate", "--product", "p", "--personas", "x"], {"model": GENERATOR}),
    (["questions", "analyze", "--questions", "q", "--product", "p"], {"model": GENERATOR}),
    (["questions", "refine", "--questions", "q", "--product", "p"], {"fill": GENERATOR}),
    (["questions", "panel-review", "--questions", "q", "--product", "p"], {"model": GENERATOR}),
    (["personas", "discover", "--product", "p"], {"model": GENERATOR}),
    (["novelty", "analyze", "--artifact", "skill:x"], {"model": ANSWER}),
    (["novelty", "probe", "--artifact", "skill:x", "--atoms", "a"], {"model": ANSWER}),
    (["novelty", "atoms", "--artifact", "skill:x"], {"model": JUDGE}),
    (["novelty", "shorten", "--artifact", "skill:x", "--atoms", "a"], {"model": JUDGE}),
])
def test_each_surface_takes_the_central_defaults(argv, expected):
    args = vars(build_parser().parse_args(argv))
    for role, pair in expected.items():
        model_key = "model" if role == "model" else f"{role}_model"
        provider_key = "provider" if role == "model" else f"{role}_provider"
        assert (args[model_key], args[provider_key]) == pair, (argv, role)


@pytest.mark.parametrize("target, params, expected", [
    ("dokimi.eval.answerer:Answerer", ("model", "provider"), ANSWER),
    ("dokimi.eval.arm_runner:ArmRunner", ("model", "provider"), ANSWER),
    ("dokimi.eval.judge:Judge", ("model", "provider"), JUDGE),
    ("dokimi.eval.panel:JudgePanel", ("default_model", "default_provider"), JUDGE),
    ("dokimi.eval.ragas_eval:RagasEvaluator", ("llm_model", "provider"), JUDGE),
    ("dokimi.personas.generator:PersonaGenerator", ("model", "provider"), GENERATOR),
    ("dokimi.questions.llm_gen:QuestionGenerator", ("model", "provider"), GENERATOR),
    ("dokimi.questions.chunk_gen:ChunkBasedQuestionGenerator", ("model", "provider"), GENERATOR),
    ("dokimi.questions.validator:QuestionValidator", ("llm_model", "llm_provider"), GENERATOR),
    ("dokimi.questions.quality_analyzer:QuestionQualityAnalyzer", ("model", "provider"),
     GENERATOR),
    ("dokimi.questions.panel_reviewer:QuestionPanelReviewer", ("model", "provider"), GENERATOR),
    ("dokimi.questions.refiner:GapFiller", ("model", "provider"), GENERATOR),
])
def test_library_classes_share_the_cli_defaults(target, params, expected):
    module, name = target.split(":")
    sig = inspect.signature(getattr(importlib.import_module(module), name))
    assert tuple(sig.parameters[p].default for p in params) == expected


def test_a_subject_without_cells_runs_on_the_default_answerer(tmp_path):
    from dokimi.subjects.loader import load_subject

    path = tmp_path / "subject.toml"
    path.write_text(
        '[subject]\nid = "s"\nkind = "skill"\nref = "skills/s"\n\n'
        '[suite]\nproducts = ["p"]\nquestions = ["q.json"]\ntasks = []\n',
        encoding="utf-8",
    )
    cell = load_subject(path).matrix_cells[0]
    assert (cell["model"], cell["provider"]) == ANSWER


def test_ragas_falls_back_to_the_default_judge_for_an_unsupported_provider(monkeypatch):
    import sys
    import types

    from dokimi.eval.ragas_eval import RagasEvaluator

    built = []

    def chat(family):
        return lambda **kw: built.append((family, kw)) or (family, kw)

    ragas_llms = types.ModuleType("ragas.llms")
    ragas_llms.LangchainLLMWrapper = lambda llm: llm
    openai_mod = types.ModuleType("langchain_openai")
    openai_mod.ChatOpenAI = chat("openai")
    anthropic_mod = types.ModuleType("langchain_anthropic")
    anthropic_mod.ChatAnthropic = chat("anthropic")
    core_models = types.ModuleType("langchain_core.language_models")
    core_models.BaseChatModel = object
    monkeypatch.setitem(sys.modules, "ragas.llms", ragas_llms)
    monkeypatch.setitem(sys.modules, "langchain_openai", openai_mod)
    monkeypatch.setitem(sys.modules, "langchain_anthropic", anthropic_mod)
    monkeypatch.setitem(sys.modules, "langchain_core.language_models", core_models)

    evaluator = RagasEvaluator.__new__(RagasEvaluator)
    evaluator.llm_model, evaluator.provider, evaluator.api_key = "gemini-x", "google", None
    evaluator._build_ragas_llm()
    assert built == [(JUDGE[1], {"model": JUDGE[0]})]


def test_independence_warning_sees_a_bedrock_alias_and_its_profile_as_one_model(capsys):
    from dokimi.commands.evaluate import _warn_judge_independence

    bare, profile = "claude-sonnet-5-5", defaults.BEDROCK_MODEL_IDS["claude-sonnet-5-5"]
    assert _warn_judge_independence("amazon-bedrock", profile, "amazon-bedrock", bare)
    assert "independence warning" in capsys.readouterr().err
    assert not _warn_judge_independence("amazon-bedrock", "claude-haiku-5-5", "amazon-bedrock", bare)
    # the same model answering and judging is the same bias on any route, so it warns on the direct API too
    capsys.readouterr()
    assert _warn_judge_independence("anthropic", profile, "anthropic", bare)
    assert "independence warning" in capsys.readouterr().err
    assert _warn_judge_independence("anthropic", "claude-sonnet-5-5", "amazon-bedrock", "claude-sonnet-5-5")
    assert "independence warning" in capsys.readouterr().err
    assert not _warn_judge_independence("anthropic", "claude-sonnet-5-5", "anthropic", "claude-haiku-5-5")
    assert capsys.readouterr().err == ""
    # an unset model is not evidence of sameness
    assert not _warn_judge_independence("anthropic", "", "anthropic", "")
    assert not _warn_judge_independence("anthropic", "claude-sonnet-5-5", "anthropic", "")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("model, provider", [
    ANSWER, GENERATOR, JUDGE, (JUDGE[0], "amazon-bedrock"),
])
def test_the_locked_litellm_prices_every_default(model, provider):
    # Offline (conftest pins litellm's bundled price map). Below litellm 1.105.0 these
    # models read as $0 or unpriced; requirements*.txt and pyproject.toml floor it there.
    from litellm import ModelResponse

    from dokimi.llm import _extract_usage

    resp = ModelResponse(
        model=model,
        choices=[{"message": {"role": "assistant", "content": "ok"}}],
        usage={"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    )
    rec = _extract_usage(resp, model, provider)
    assert rec.cost_usd is not None and rec.cost_usd > 0
    assert rec.cost_known_calls == 1


@pytest.mark.parametrize("litellm_model, model, key", [
    ("bedrock/us.anthropic.x", "x", "bedrock/us.anthropic.x"),       # the routed name
    ("bedrock/us.anthropic.x", "x", "us.anthropic.x"),               # route prefix stripped
    ("bedrock/converse/openai.x", "openai.x", "converse/openai.x"),  # first prefix only
    ("bedrock/converse/openai.y", "y", "openai.y"),                  # last segment
    ("bedrock/us.anthropic.claude-z", "claude-z", "claude-z"),       # the caller's bare name
])
def test_a_model_priced_under_any_of_its_names_stays_a_known_zero(monkeypatch, litellm_model, model, key):
    import litellm

    from dokimi.llm import _has_price_entry

    monkeypatch.setattr(litellm, "model_cost", {key: {}})
    assert _has_price_entry(litellm_model, model)
    monkeypatch.setattr(litellm, "model_cost", {})
    assert not _has_price_entry(litellm_model, model)


def test_independence_warning_names_the_judge_route_as_well_as_the_answer_route(capsys):
    from dokimi.commands.evaluate import _warn_judge_independence

    assert _warn_judge_independence("anthropic", "claude-haiku-5-5", "openai", "claude-haiku-5-5")
    err = capsys.readouterr().err
    assert "[anthropic/claude-haiku-5-5]" in err and "[openai/claude-haiku-5-5]" in err

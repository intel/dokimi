# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the N-arm runner (answer generation + judging)."""

from unittest.mock import MagicMock

import pytest
import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.eval.arm_runner import ArmRunner
from dokimi.treatments import BaselineTreatment, SkillAgentTreatment, SkillTreatment
from dokimi.skills import load_skill
from dokimi.plugins import create_plugins, plugin_set_metadata, wrap_treatments


def _patch_llm(monkeypatch, captured):
    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        captured.append({"prompt": prompt, "system": system,
                         "cache_prefix": kw.get("cache_prefix")})
        return f"answer (system={'yes' if system else 'no'})", {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15
        }

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)


QUESTIONS = [
    {"id": "q1", "question": "How do I parallelize a loop?", "category": "usage"},
    {"id": "q2", "question": "How do I sum an array in parallel?"},
]


def test_arm_runner_generates_per_arm(monkeypatch, demo_skill):
    captured = []
    _patch_llm(monkeypatch, captured)

    skill = load_skill(demo_skill)
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(skill)])
    records = runner.run("oneTBB", QUESTIONS, concurrency=1)

    assert len(records) == 2
    rec = records[0]
    assert set(rec["arms"].keys()) == {"baseline", "skill:demo-skill"}
    # baseline has no context, skill arm does
    assert rec["arms"]["baseline"]["context_chunks"] == []
    assert rec["arms"]["skill:demo-skill"]["context_chunks"]
    # 2 questions x 2 arms = 4 LLM calls
    assert len(captured) == 4


def test_arm_runner_requires_at_least_one_arm():
    import pytest
    with pytest.raises(ValueError):
        ArmRunner([])


class _FakeJudge:
    """Scores by answer length so arms get distinct, deterministic numbers."""

    model = "fake-judge"
    provider = "fake-provider"

    def score_answer(self, library_name, question, answer, context="", ground_truth=None):
        agg = 80 if context.strip() and context != "(No documentation retrieved)" else 60
        return {"aggregate": agg, "correctness": agg}


def test_arm_runner_judge_computes_deltas(monkeypatch, demo_skill):
    captured = []
    _patch_llm(monkeypatch, captured)

    skill = load_skill(demo_skill)
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(skill)])
    records = runner.run("oneTBB", QUESTIONS, concurrency=1)

    evaluations = runner.judge(_FakeJudge(), "oneTBB", records, baseline_arm="baseline")
    assert len(evaluations) == 2
    ev = evaluations[0]
    assert ev["scores"]["baseline"]["aggregate"] == 60
    assert ev["scores"]["skill:demo-skill"]["aggregate"] == 80
    assert ev["deltas_vs_baseline"]["skill:demo-skill"] == 20.0

    output = runner.build_output(
        "oneTBB", records, evaluations, baseline_arm="baseline", judge=_FakeJudge()
    )
    summary = output["summary"]["per_arm"]
    assert summary["baseline"]["avg_aggregate"] == 60.0
    assert summary["skill:demo-skill"]["delta_vs_baseline"] == 20.0
    assert output["judge_model"] == "fake-judge"
    assert output["judge_provider"] == "fake-provider"


def test_build_output_without_evaluations(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    runner = ArmRunner([BaselineTreatment()])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    output = runner.build_output("oneTBB", records)
    assert "summary" not in output
    assert output["arms"] == ["baseline"]


def test_build_output_summarizes_skill_agent_discoverability(monkeypatch, demo_skill):
    import dokimi.eval.agent_runner as agent_runner_mod

    def fake_agent_loop(**kwargs):
        return {
            "answer": "used skill",
            "transcript": [{"iteration": 1, "tool": "view_skill_demo_skill"}],
            "tool_call_count": 1,
            "tool_usage": {
                "used_any_tool": True,
                "called_tools": ["view_skill_demo_skill"],
                "tool_call_count_by_name": {"view_skill_demo_skill": 1},
                "first_call_iteration_by_name": {"view_skill_demo_skill": 1},
                "skill": {
                    "offered": True,
                    "called": True,
                    "call_count": 1,
                    "first_call_iteration": 1,
                    "offered_tools": [{"name": "view_skill_demo_skill"}],
                },
            },
            "iterations": 2,
            "stopped_reason": "answered",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "per_turn": [],
        }

    monkeypatch.setattr(agent_runner_mod, "run_agent_loop", fake_agent_loop)

    skill = load_skill(demo_skill)
    runner = ArmRunner([SkillAgentTreatment(skill)])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    arm = records[0]["arms"]["skill_agent:demo-skill"]
    assert arm["discoverability"]["called"] is True

    output = runner.build_output("oneTBB", records)
    usage = output["agentic_usage_summary"]["skill_agent:demo-skill"]
    assert usage["tool_use_rate"] == 1.0
    assert usage["skill_load_rate"] == 1.0
    assert usage["avg_skill_calls"] == 1.0


def test_arm_runner_emits_metrics_block_and_alias(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    runner = ArmRunner([BaselineTreatment()])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    arm = records[0]["arms"]["baseline"]

    # Legacy alias preserved.
    assert arm["token_usage"] == {"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15}
    # New metrics block present with required + optional fields.
    m = arm["metrics"]
    assert m["prompt_tokens"] == 10 and m["completion_tokens"] == 5
    assert m["total_tokens"] == 15
    assert m["cost_usd"] is None          # dict stub carries no pricing
    assert "latency_sec" in m and "ttft_sec" in m
    assert m["raw_answer_chars"] == m["final_answer_chars"]

    output = runner.build_output("oneTBB", records)
    cs = output["cost_summary"]["baseline"]
    assert cs["n"] == 1
    assert cs["total_tokens"] == 15
    assert cs["total_cost_usd"] is None
    assert cs["cost_known_n"] == 0   # dict stub has no pricing
    assert cs["mean_ttft_sec"] is None


def test_cost_summary_flags_partial_pricing_by_call():
    runner = ArmRunner([BaselineTreatment()])
    records = [{
        "question_id": "q1",
        "arms": {
            "agent": {
                "metrics": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost_usd": 0.01,
                    "cost_known_calls": 1,
                    "n_calls": 2,
                    "latency_sec": 1.0,
                }
            }
        }
    }]

    cs = runner._cost_summary(records)["agent"]
    assert cs["total_cost_usd"] == 0.01
    assert cs["cost_known_n"] == 1
    assert cs["total_llm_calls"] == 2


def test_arm_runner_stamps_plugin_set_and_harness(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    plugins = create_plugins(["plugin:caveman"])
    plugin_set = plugin_set_metadata(plugins)
    treatments = wrap_treatments([BaselineTreatment()], plugins)

    runner = ArmRunner(
        treatments,
        harness="openclaw-agent",
        plugin_set=plugin_set,
        matrix_cell="agent-caveman",
    )
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    output = runner.build_output("oneTBB", records)

    assert "caveman style" in captured[0]["system"]
    assert records[0]["arms"]["baseline"]["plugin_set"] == "caveman:full"
    assert records[0]["arms"]["baseline"]["harness"] == "openclaw-agent"
    assert output["plugin_set"] == "caveman:full"
    assert output["harness"] == "openclaw-agent"
    assert output["matrix_cell"] == "agent-caveman"


def test_arm_runner_records_request_shaping_config(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)

    runner = ArmRunner(
        [BaselineTreatment()],
        max_tokens=32_000,
        max_iterations=9,
        grounded_from_topics=True,
        temperature=0.0,
        seed=7,
    )
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    output = runner.build_output("oneTBB", records)

    # Without these a truncated run and a complete one are indistinguishable.
    # temperature/seed matter for the same reason: a pinned run and a
    # free-sampling one are differently reproducible.
    assert output["run_config"] == {
        "max_tokens": 32_000,
        "max_iterations": 9,
        "grounded_from_topics": True,
        "temperature": 0.0,
        "seed": 7,
    }


def test_arm_runner_run_config_defaults(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)

    runner = ArmRunner([BaselineTreatment()])
    output = runner.build_output(
        "oneTBB", runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    )

    assert output["run_config"]["max_tokens"] is None
    assert output["run_config"]["grounded_from_topics"] is False
    # Unset by default, so the request keeps the shape it had before these
    # knobs existed.
    assert output["run_config"]["temperature"] is None
    assert output["run_config"]["seed"] is None


def test_arm_runner_forwards_sampling_params_to_the_llm_call(monkeypatch):
    # Recording temperature/seed in run_config is worthless if they never reach
    # the request, so assert the call itself, not just the artifact.
    seen = {}

    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        seen.update(kw)
        return "answer", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)

    runner = ArmRunner([BaselineTreatment()], temperature=0.0, seed=123)
    runner.run("oneTBB", QUESTIONS[:1], concurrency=1)

    assert seen["temperature"] == 0.0
    assert seen["seed"] == 123


def test_arm_runner_omits_sampling_params_when_unset(monkeypatch):
    seen = {}

    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        seen.update(kw)
        return "answer", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)

    runner = ArmRunner([BaselineTreatment()])
    runner.run("oneTBB", QUESTIONS[:1], concurrency=1)

    # Passed as None rather than absent is fine at this layer — _sampling_kwargs
    # is what drops them before the provider sees the request.
    assert seen.get("temperature") is None
    assert seen.get("seed") is None


def test_arm_runner_rejects_unknown_stats_correction():
    """A typo must fail at construction, not after a whole run is paid for."""
    with pytest.raises(ValueError, match="stats_correction"):
        ArmRunner([BaselineTreatment()], stats_correction="bonferroni")


def test_arm_runner_accepts_bh_correction_and_applies_it(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    runner = ArmRunner([BaselineTreatment()], stats_correction="bh")
    assert runner.stats_correction == "bh"

    evaluations = [
        {
            "question_id": f"q{i}",
            "scores": {"baseline": {"aggregate": 50.0 + i}, "docs": {"aggregate": 60.0 + i}},
        }
        for i in range(6)
    ]
    summary = runner._summarize(evaluations, "baseline")
    assert summary["per_arm"]["docs"]["significance"]["correction"] == "bh"


def _parse_arms_run(argv):
    from dokimi.cli import build_parser

    return build_parser().parse_args(
        ["arms", "run", "--product", "oneTBB",
         "--questions", "questions.json", "--arms", "baseline"] + argv
    )


def test_cli_defaults_stats_correction_to_holm():
    assert _parse_arms_run([]).stats_correction == "holm"


def test_cli_accepts_every_correction_stats_supports():
    """Guards against the CLI literal drifting from eval.stats.CORRECTIONS."""
    from dokimi.eval import stats

    for method in stats.CORRECTIONS:
        assert _parse_arms_run(["--stats-correction", method]).stats_correction == method


def test_cli_rejects_unknown_stats_correction():
    with pytest.raises(SystemExit):
        _parse_arms_run(["--stats-correction", "bonferroni"])


def test_matrix_run_also_exposes_stats_correction():
    """matrix-run copies the namespace wholesale, so the flag must exist there too."""
    from dokimi.cli import build_parser

    args = build_parser().parse_args(
        ["arms", "matrix-run", "--product", "oneTBB",
         "--questions", "questions.json", "--arms", "baseline",
         "--matrix-cells", "cells.json", "--stats-correction", "bh"]
    )
    assert args.stats_correction == "bh"


# ── cacheable prompt prefix ──────────────────────────────────────────────────


def test_context_precedes_the_question_in_the_prompt():
    """Providers only reuse a prefix, so the stable half has to come first."""
    from dokimi.eval.arm_runner import (
        ANSWER_PROMPT_WITH_CONTEXT,
        CONTEXT_PROMPT_PREFIX,
        CONTEXT_PROMPT_SUFFIX,
    )

    full = ANSWER_PROMPT_WITH_CONTEXT.format(question="Q?", context="CTX")
    assert full.index("CTX") < full.index("Q?")
    # The split must not introduce a third copy of the wording.
    assert full == (CONTEXT_PROMPT_PREFIX.format(context="CTX")
                    + CONTEXT_PROMPT_SUFFIX.format(question="Q?"))


def test_static_context_is_passed_as_a_cache_prefix(monkeypatch, demo_skill):
    """A skill body is identical for every question — a real cacheable prefix."""
    captured = []
    _patch_llm(monkeypatch, captured)

    skill = load_skill(demo_skill)
    runner = ArmRunner([SkillTreatment(skill)])
    runner.run("oneTBB", QUESTIONS, concurrency=1)

    prefixes = [c["cache_prefix"] for c in captured]
    assert all(p for p in prefixes)
    # Identical across questions — that is the whole point.
    assert len(set(prefixes)) == 1
    # And the question stays out of the cached half.
    assert QUESTIONS[0]["question"] not in prefixes[0]


def test_volatile_context_is_not_offered_for_caching(monkeypatch):
    """Query-driven retrieval would pay the write surcharge and never hit."""
    from dokimi.treatments import DocTreatment

    class _Client:
        def get_library_docs(self, library_id, query, max_results=5):
            return [{"content": f"chunk for {query}", "relevance_score": 0.9}]

    captured = []
    _patch_llm(monkeypatch, captured)
    runner = ArmRunner([DocTreatment(_Client())])
    runner.run("oneTBB", QUESTIONS, concurrency=1)

    assert [c["cache_prefix"] for c in captured] == [None, None]
    # Content is unchanged; it just travels as one string.
    assert "chunk for" in captured[0]["prompt"]


def test_baseline_arm_sends_no_prefix(monkeypatch):
    captured = []
    _patch_llm(monkeypatch, captured)
    ArmRunner([BaselineTreatment()]).run("oneTBB", QUESTIONS[:1], concurrency=1)
    assert captured[0]["cache_prefix"] is None


def test_arm_runner_judge_skips_an_arm_whose_aggregate_is_none(monkeypatch, demo_skill):
    # A panel whose judges all fail returns {"aggregate": None}; the delta is skipped, not a crash.
    _patch_llm(monkeypatch, [])
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(load_skill(demo_skill))])
    records = runner.run("oneTBB", QUESTIONS, concurrency=1)

    class _HalfFailing(_FakeJudge):
        def score_answer(self, library_name, question, answer, context="", ground_truth=None):
            return {"aggregate": 60 if context == "(No documentation retrieved)" else None}

    evaluations = runner.judge(_HalfFailing(), "oneTBB", records, baseline_arm="baseline")
    assert all(ev["deltas_vs_baseline"] == {} for ev in evaluations)
    assert runner.build_output("oneTBB", records, evaluations, judge=_HalfFailing())["summary"]


def test_arm_runner_judge_makes_no_call_for_a_binary_record():
    """A binary record holds no arms: nothing is scored and the judge is never called."""
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    judge = MagicMock()
    rec = {"question_id": "q1", "question_text": "How?",
           "with_docs": {"answer": "with"}, "without_docs": {"answer": "without"}}
    [row] = runner.judge(judge, "lib", [rec])
    assert row["scores"] == {}
    assert judge.score_answer.call_count == 0


def test_arm_runner_judge_keeps_a_null_arm_as_an_unscored_row():
    """An arm that ran and produced nothing keeps its row with a None score."""
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    runner.judge_context.resolve_with_source.return_value = ("", "snippets")
    judge = MagicMock()
    judge.score_answer.return_value = {"aggregate": 60.0}
    rec = {"question_id": "q1", "question_text": "How?",
           "arms": {"baseline": {"answer": "base"}, "skill": None}}
    [row] = runner.judge(judge, "lib", [rec], baseline_arm="baseline")
    assert "skill" in row["scores"] and row["scores"]["skill"] is None
    assert judge.score_answer.call_count == 1


def test_cli_rejects_an_unknown_judge_panel_role_before_running(capsys):
    assert _parse_arms_run(["--judge-panel", "technical_expert"]).judge_panel == "technical_expert"
    with pytest.raises(SystemExit) as exc:
        _parse_arms_run(["--judge-panel", "technical_expert,doc_reveiwer"])
    assert exc.value.code == 2 and "doc_reveiwer" in capsys.readouterr().err


def test_build_output_records_judge_temperature_and_prompt_digest(monkeypatch):
    from dokimi.eval import JudgePanel
    from dokimi.eval.judge import JUDGE_PROMPT_SHA256, Judge
    from dokimi.eval.panel import JudgeConfig

    _patch_llm(monkeypatch, [])
    runner = ArmRunner([BaselineTreatment()])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    judge = Judge(model="m1", provider="amazon-bedrock", temperature=0.0)
    out = runner.build_output("oneTBB", records, [], judge=judge)
    assert (out["judge_temperature"], out["judge_prompt_sha256"]) == (0.0, JUDGE_PROMPT_SHA256)
    one = JudgePanel(judges=[JudgeConfig(role="technical_expert", model="m1", provider="amazon-bedrock")])
    two = JudgePanel(judges=[JudgeConfig(role="doc_reviewer", model="m1", provider="amazon-bedrock")])
    assert len({one.prompt_sha256, two.prompt_sha256, JUDGE_PROMPT_SHA256}) == 3


def test_an_unbuildable_judge_fails_before_any_answer_is_generated(monkeypatch):
    from dokimi.eval.arms_run import ArmsRunConfig, run_arms

    calls = []
    monkeypatch.setattr(ArmRunner, "run", lambda self, *a, **kw: calls.append(1) or [])
    # The CLI no longer offers a provider Judge rejects, so set one the way a
    # caller building its own config could.
    config = ArmsRunConfig(product="oneTBB", judge=True, judge_model="m", judge_provider="openai-codex")
    with pytest.raises(ValueError, match="openai-codex"):
        run_arms(config, ["baseline"], QUESTIONS[:1])
    assert calls == []


def test_matrix_run_checks_every_cell_before_running_the_first(monkeypatch, tmp_path):
    import json
    import dokimi.eval.arms_run as arms_run
    from dokimi.cli import build_parser

    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"matrix": {"cells": [
        {"id": "ok", "model": "m-ok", "provider": "openai", "harness": "arms-runner"},
        {"id": "bad", "model": "m-bad", "provider": "amazon-bedrock", "harness": "arms-runner"},
    ]}}), encoding="utf-8")
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Q?"}]), encoding="utf-8")

    monkeypatch.setattr("dokimi.llm.unsupported_sampling_params",
                        lambda model, provider, **kw: ["seed"] if model == "m-bad" else [])
    monkeypatch.setattr(arms_run, "probe_sampling", lambda *a: None)
    ran = []
    monkeypatch.setattr(arms_run, "run_arms", lambda *a, **kw: ran.append(1))
    args = build_parser().parse_args(
        ["arms", "matrix-run", "--product", "oneTBB", "--questions", str(questions),
         "--arms", "baseline", "--matrix-cells", str(cells), "--seed", "1",
         "--out-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit):
        args.func(args)
    assert ran == []


def test_probe_rejects_a_sampling_value_the_provider_refuses(monkeypatch):
    import dokimi.eval.arms_run as arms_run
    import dokimi.llm as llm_mod

    def refuse(prompt, model, provider, **kw):
        raise Exception("UnsupportedParamsError: gpt-5 models don't support temperature=0.0")

    monkeypatch.setattr(llm_mod, "llm_call_with_usage", refuse)
    arms_run.probe_sampling.cache_clear()
    with pytest.raises(ValueError, match="temperature=0.0"):
        arms_run.probe_sampling("gpt-5", "openai", 0.0, None)


def test_probe_makes_no_call_without_sampling_flags_and_one_per_configuration(monkeypatch):
    import dokimi.eval.arms_run as arms_run
    import dokimi.llm as llm_mod

    calls = []
    monkeypatch.setattr(llm_mod, "llm_call_with_usage",
                        lambda prompt, model, provider, **kw: calls.append(kw) or ("OK", None))
    arms_run.probe_sampling.cache_clear()
    arms_run.probe_sampling("m", "openai", None, None)
    assert calls == []
    arms_run.probe_sampling("m", "openai", 0.0, None)
    arms_run.probe_sampling("m", "openai", 0.0, None)
    assert len(calls) == 1 and calls[0]["temperature"] == 0.0


# ── What the judge is shown about an arm's material ──────────────────────
#
# A judge that cannot see the treatment material scores an arm *down* for
# citing it ("fabricated path"), which inverts arm comparisons. These tests
# pin what reaches the judge, not how it scores.

class _ContextCapturingJudge:
    model = "ctx-judge"
    provider = "fake-provider"

    def __init__(self):
        self.seen = {}

    def score_answer(self, library_name, question, answer, context="", ground_truth=None):
        self.seen[(question, len(self.seen))] = context
        self.last = context
        return {"aggregate": 70, "correctness": 70}


@pytest.fixture
def skill_with_files(tmp_path):
    """A skill whose detail lives in bundled files, like real skills."""
    root = tmp_path / "bundled-demo"
    (root / "patterns").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: bundled-demo\ndescription: Bundled-file skill fixture.\n---\n"
        "# bundled-demo\n\nSee patterns/simd.md for the details.\n",
        encoding="utf-8",
    )
    (root / "patterns" / "simd.md").write_text("# SIMD detail", encoding="utf-8")
    return root


def test_judge_sees_the_whole_injected_context_not_the_report_snippet(
    monkeypatch, skill_with_files
):
    _patch_llm(monkeypatch, [])
    skill = load_skill(skill_with_files)
    runner = ArmRunner([SkillTreatment(skill)])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)

    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)

    # The snippet kept in the record is a 200-char preview; the judge gets the body.
    snippet = records[0]["arms"]["skill:bundled-demo"]["context_chunks"][0]["snippet"]
    assert "See patterns/simd.md" in judge.last
    assert len(judge.last) > len(snippet)


def test_judge_sees_the_bundled_file_manifest(monkeypatch, skill_with_files):
    """The measured defect: a cited bundled path read as a fabrication."""
    _patch_llm(monkeypatch, [])
    runner = ArmRunner([SkillTreatment(load_skill(skill_with_files))])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)

    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)
    assert "patterns/simd.md" in judge.last


def test_oversized_context_keeps_the_manifest():
    """A combined doc+skill arm can exceed the cap; the manifest must survive it."""
    from dokimi.eval.judge_context import build

    manifest = "Files bundled with skill 'x' (these all exist):\n  patterns/simd.md"
    out = build([{"content": "d" * 9_000}, {"content": "s" * 12_000}], manifest)
    assert out.endswith(manifest)


def test_an_oversized_reference_is_cut_and_marked():
    from dokimi.eval.judge_context import MAX_JUDGE_REFERENCE_CHARS, build

    out = build([], "r" * (MAX_JUDGE_REFERENCE_CHARS + 500))
    assert out == "r" * MAX_JUDGE_REFERENCE_CHARS + "\n…"


class _StaticDocs:
    """A non-agentic treatment that injects fixed chunks, incl. an empty one."""

    name = "docs-fixture"

    def __init__(self, chunks, reference=""):
        self.chunks = chunks
        self.reference = reference

    def prepare(self, question_text, library_name, library_id=None):
        from dokimi.treatments.base import AgentConfig

        return AgentConfig(injected_context=[dict(c) for c in self.chunks],
                           judge_reference=self.reference, context_is_static=False)


def _answer_prompt_context(prompt, question):
    """Pull the injected material back out of the prompt the answerer was sent:
    everything between the prefix's head and the known prefix tail + suffix."""
    from dokimi.eval.arm_runner import CONTEXT_PROMPT_PREFIX, CONTEXT_PROMPT_SUFFIX

    head, _, tail = CONTEXT_PROMPT_PREFIX.partition("{context}")
    end = tail + CONTEXT_PROMPT_SUFFIX.format(question=question)
    assert prompt.startswith(head) and prompt.endswith(end)
    return prompt[len(head):len(prompt) - len(end)]


def test_judge_sees_exactly_the_injected_text_the_answerer_was_sent(monkeypatch):
    """No less (the defect being fixed) and no more: compared against the prompt
    actually handed to the model, not a copy of its join. An empty chunk is in
    the set because it still contributes a separator to the answering prompt."""
    from dokimi.eval.arm_runner import MAX_CONTEXT_CHARS

    captured = []
    _patch_llm(monkeypatch, captured)
    chunks = [{"content": "d" * 9_000}, {"content": ""}, {"content": "s" * 12_000 + "TAIL"}]
    manifest = "Files bundled with skill 'x' (these all exist):\n  a.md"
    runner = ArmRunner([_StaticDocs(chunks, manifest)])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)

    sent = _answer_prompt_context(captured[0]["prompt"], QUESTIONS[0]["question"])
    assert len(sent) == MAX_CONTEXT_CHARS
    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)
    assert judge.last == sent + "\n\n---\n\n" + manifest
    assert "TAIL" not in judge.last


def test_combined_arm_with_an_agentic_part_does_not_show_the_judge_unsent_material(
    monkeypatch, skill_with_files
):
    """The agentic path hands the loop only question, tools and system prompt,
    so a combined skill + skill_agent arm's injected skill body never reaches the
    answerer — the judge must not get it either. The reference still does."""
    from dokimi.treatments.arms import CombinedTreatment

    sent = {}

    def fake_loop(question, tools, model, provider, system_prompt=None, api_key=None,
                  max_iterations=6, max_tokens=None, **_sampling):
        sent.update(question=question, system_prompt=system_prompt or "")
        return {"answer": "see patterns/simd.md", "transcript": [], "tool_call_count": 0,
                "tool_usage": {}, "iterations": 1, "stopped_reason": "answered",
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                "total_tokens": 2}}

    import dokimi.eval.agent_runner as agent_runner_mod
    monkeypatch.setattr(agent_runner_mod, "run_agent_loop", fake_loop)

    skill = load_skill(skill_with_files)
    combined = CombinedTreatment([SkillTreatment(skill), SkillAgentTreatment(skill)])
    runner = ArmRunner([combined])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    body_marker = "See patterns/simd.md for the details."
    assert body_marker not in sent["question"] + sent["system_prompt"]

    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)
    reference = combined.prepare("q", "oneTBB").judge_reference
    assert judge.last == reference
    assert "patterns/simd.md" in judge.last


def test_agentic_combined_arm_drops_the_non_agentic_skills_manifest(tmp_path):
    """In `skill:x+skill_agent:y` the agent can open y's files through its tools
    but has no way to reach x's; x's manifest must not tell the judge they exist."""
    from dokimi.treatments.arms import CombinedTreatment

    def make(name):
        root = tmp_path / name
        (root / "ref").mkdir(parents=True)
        (root / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: fixture {name}.\n---\n# {name}\n",
            encoding="utf-8")
        (root / "ref" / f"{name}-only.md").write_text("detail", encoding="utf-8")
        return load_skill(root)

    x, y = make("skill-x"), make("skill-y")
    agentic = CombinedTreatment([SkillTreatment(x), SkillAgentTreatment(y)]).prepare("q", "lib")
    assert agentic.is_agentic
    assert "skill-y-only.md" in agentic.judge_reference
    assert "skill-x-only.md" not in agentic.judge_reference

    plain = CombinedTreatment([SkillTreatment(x), SkillTreatment(y)]).prepare("q", "lib")
    assert not plain.is_agentic
    assert "skill-x-only.md" in plain.judge_reference
    assert "skill-y-only.md" in plain.judge_reference


def test_all_empty_chunks_are_no_material():
    from dokimi.eval.judge_context import build

    assert build([{"content": ""}, {"content": ""}]) == ""
    assert build([{"content": ""}, {"content": " "}], "REF") == "REF"


def test_id_less_questions_are_not_cross_judged_through_the_runner(monkeypatch):
    """End to end: two id-less questions get different docs; each is judged
    against its own record, not the other question's retrieval."""
    _patch_llm(monkeypatch, [])

    class _PerQuestionDocs(_StaticDocs):
        def prepare(self, question_text, library_name, library_id=None):
            from dokimi.treatments.base import AgentConfig

            return AgentConfig(injected_context=[{"content": f"docs for {question_text}",
                                                  "snippet": f"snip {question_text}"}],
                               context_is_static=False)

    runner = ArmRunner([_PerQuestionDocs([])])
    qs = [{"question": "first?"}, {"question": "second?"}]
    records = runner.run("oneTBB", qs, concurrency=1)
    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)
    by_question = {q: ctx for (q, _), ctx in judge.seen.items()}
    # Each falls back to its own record's snippet; neither sees the other's docs.
    assert "docs for first?" in by_question["first?"]
    assert "second?" not in by_question["first?"]
    assert "docs for second?" in by_question["second?"]
    assert "first?" not in by_question["second?"]


def test_runner_passes_its_own_cap_to_the_judge_context(monkeypatch):
    """The cap is passed, not duplicated, so the two cannot drift."""
    import dokimi.eval.arm_runner as ar

    seen = {}
    real = ar.build_judge_context

    def spy(injected, reference="", **kw):
        seen.update(kw)
        return real(injected, reference, **kw)

    monkeypatch.setattr(ar, "build_judge_context", spy)
    _patch_llm(monkeypatch, [])
    ArmRunner([BaselineTreatment()]).run("oneTBB", QUESTIONS[:1], concurrency=1)
    assert seen["injected_chars"] == ar.MAX_CONTEXT_CHARS


def test_questions_without_ids_are_not_judged_against_each_others_context(caplog):
    """Every id-less question maps to ``unknown_q``. A shared key must not hand
    one question's retrieved docs to another; fall back to the record's own
    snippets, which cannot mix questions."""
    from dokimi.eval.judge_context import JudgeContextStore

    store = JudgeContextStore()
    with caplog.at_level("WARNING"):
        store.put("unknown_q", "docs", "docs retrieved for question A")
        store.put("unknown_q", "docs", "docs retrieved for question B")
        store.put("unknown_q", "docs", "docs retrieved for question C")
    assert store.get("unknown_q", "docs") is None
    assert "more than one question" in caplog.text
    arm = {"context_chunks": [{"snippet": "own snippet"}]}
    assert store.resolve("unknown_q", "docs", arm) == "own snippet"


def test_identical_context_under_a_shared_key_is_not_ambiguous():
    """A skill arm injects the same body for every question; sharing a key is harmless then."""
    from dokimi.eval.judge_context import JudgeContextStore

    store = JudgeContextStore()
    store.put("unknown_q", "skill:x", "same skill body")
    store.put("unknown_q", "skill:x", "same skill body")
    assert store.get("unknown_q", "skill:x") == "same skill body"


def test_agentic_skill_arm_is_not_judged_against_an_empty_context(
    monkeypatch, skill_with_files
):
    """``context_chunks`` is empty by construction for an agentic arm."""
    def fake_loop(question, tools, model, provider, system_prompt=None, api_key=None,
                  max_iterations=6, max_tokens=None, **_sampling):
        return {"answer": "see patterns/simd.md", "transcript": [], "tool_call_count": 0,
                "tool_usage": {}, "iterations": 1, "stopped_reason": "answered",
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                "total_tokens": 2}}

    import dokimi.eval.agent_runner as agent_runner_mod
    monkeypatch.setattr(agent_runner_mod, "run_agent_loop", fake_loop)

    runner = ArmRunner([SkillAgentTreatment(load_skill(skill_with_files))])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)
    assert records[0]["arms"]["skill_agent:bundled-demo"]["context_chunks"] == []

    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)
    # Both the skill itself and its manifest, since nothing was injected. The
    # name and the path appear in the manifest too; only the body has the prose.
    assert "for the details" in judge.last
    assert "Files bundled with skill" in judge.last


def test_baseline_arm_still_declares_that_it_had_no_context(monkeypatch):
    _patch_llm(monkeypatch, [])
    runner = ArmRunner([BaselineTreatment()])
    records = runner.run("oneTBB", QUESTIONS[:1], concurrency=1)

    judge = _ContextCapturingJudge()
    runner.judge(judge, "oneTBB", records)
    assert judge.last == "(No documentation retrieved)"


def test_judging_records_from_disk_falls_back_to_snippets_and_warns(monkeypatch, caplog):
    """A fresh runner has no store, so judging a loaded run must degrade visibly."""
    runner = ArmRunner([BaselineTreatment()])
    records = [{
        "question_id": "q1", "question_text": "How do I parallelize a loop?",
        "arms": {"docs": {"answer": "use parallel_for",
                          "context_chunks": [{"snippet": "parallel_for docs…"}]}},
    }]
    judge = _ContextCapturingJudge()
    with caplog.at_level("WARNING"):
        runner.judge(judge, "oneTBB", records, baseline_arm="docs")

    assert judge.last == "parallel_for docs…"
    assert "No stored judge context" in caplog.text


def test_build_output_stamps_the_judge_context_protocol(monkeypatch, demo_skill):
    """The stamp is what lets plugin_delta / model_compare refuse a pre-#51 pairing."""
    _patch_llm(monkeypatch, [])
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(load_skill(demo_skill))])
    records = runner.run("oneTBB", QUESTIONS, concurrency=1)
    evaluations = runner.judge(_FakeJudge(), "oneTBB", records)
    output = runner.build_output("oneTBB", records, evaluations, judge=_FakeJudge())
    assert output["judge_context"] == "treatment_material.v1"
    # Unjudged output has nothing judged to stamp.
    assert "judge_context" not in runner.build_output("oneTBB", records)

    from dokimi.artifacts import validate_artifact

    validate_artifact("arms", output)


def test_each_score_records_that_it_was_judged_against_the_stored_context(
    monkeypatch, demo_skill
):
    _patch_llm(monkeypatch, [])
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(load_skill(demo_skill))])
    records = runner.run("oneTBB", QUESTIONS, concurrency=1)
    evaluations = runner.judge(_FakeJudge(), "oneTBB", records)
    sources = [s["judge_context_source"] for ev in evaluations for s in ev["scores"].values()]
    assert sources == ["stored"] * 4


def test_a_fallback_to_report_snippets_is_recorded_per_score():
    """One warning per run cannot say which scores fell back; the score must."""

    class _FailingOnSecond(_FakeJudge):
        def score_answer(self, library_name, question, answer, context="", ground_truth=None):
            if question == "second?":
                raise RuntimeError("judge down")
            return super().score_answer(library_name, question, answer, context, ground_truth)

    runner = ArmRunner([BaselineTreatment()])
    records = [
        {"question_id": qid, "question_text": text,
         "arms": {"docs": {"answer": "use parallel_for",
                           "context_chunks": [{"snippet": "parallel_for docs…"}]}}}
        for qid, text in (("q1", "first?"), ("q2", "second?"))
    ]
    evaluations = runner.judge(_FailingOnSecond(), "oneTBB", records, baseline_arm="docs")
    assert evaluations[0]["scores"]["docs"]["judge_context_source"] == "snippet_fallback"
    # The error dict carries it too, so a re-judge knows what the failed call was shown.
    assert evaluations[1]["scores"]["docs"]["error"] == "judge down"
    assert evaluations[1]["scores"]["docs"]["judge_context_source"] == "snippet_fallback"


def test_an_ambiguous_key_is_recorded_as_a_snippet_fallback(monkeypatch):
    _patch_llm(monkeypatch, [])

    class _PerQuestionDocs(_StaticDocs):
        def prepare(self, question_text, library_name, library_id=None):
            from dokimi.treatments.base import AgentConfig

            return AgentConfig(injected_context=[{"content": f"docs for {question_text}",
                                                  "snippet": f"snip {question_text}"}],
                               context_is_static=False)

    runner = ArmRunner([_PerQuestionDocs([])])
    records = runner.run("oneTBB", [{"question": "first?"}, {"question": "second?"}],
                         concurrency=1)
    assert {r["question_id"] for r in records} == {"unknown_q"}
    evaluations = runner.judge(_ContextCapturingJudge(), "oneTBB", records)
    sources = [s["judge_context_source"] for ev in evaluations for s in ev["scores"].values()]
    assert sources == ["snippet_fallback"] * 2


def _litellm_resp(text):
    from types import SimpleNamespace

    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=None)


def test_a_bare_bedrock_judge_name_is_checked_as_the_judge_will_call_it(monkeypatch):
    import litellm
    import dokimi.eval.arms_run as arms_run
    from dokimi.commands.arms import run_config_from_args
    from dokimi.utils import get_llm

    mapped = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", mapped)
    checked, sent = [], []

    def supported(model=None, **kw):
        checked.append(model)
        return ["temperature", "max_tokens"]

    def completion(model, messages, **kw):
        sent.append((model, kw.get("temperature")))
        if model != f"bedrock/{mapped}":
            raise Exception("BadRequestError: The provided model identifier is invalid.")
        return _litellm_resp("OK")

    monkeypatch.setattr(litellm, "get_supported_openai_params", supported)
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(ArmRunner, "run", lambda self, *a, **kw: [])
    arms_run.probe_sampling.cache_clear()
    args = _parse_arms_run(["--judge", "--judge-provider", "amazon-bedrock",
                            "--judge-model", "claude-sonnet-4-5", "--judge-temperature", "0"])
    arms_run.run_arms(run_config_from_args(args), ["baseline"], QUESTIONS[:1])
    get_llm("amazon-bedrock", "claude-sonnet-4-5", temperature=0.0).invoke("p")

    assert [m for m in checked if m.startswith("bedrock/")] == [f"bedrock/{mapped}"]
    assert sent == [(f"bedrock/{mapped}", 0.0), (f"bedrock/{mapped}", 0.0)]


# ── The stamp follows what the judge actually saw ─────────────────────────

class _DocsPerQuestion:
    name = "docs-q"

    def prepare(self, question_text, library_name, library_id=None):
        from dokimi.treatments.base import AgentConfig

        return AgentConfig(injected_context=[{"content": f"docs for {question_text}"}],
                           context_is_static=False)


@pytest.mark.parametrize("questions, from_disk, stamp, sources", [
    ([{"id": "a", "question": "q1?"}, {"id": "b", "question": "q2?"}], False,
     "treatment_material.v1", {"stored": 2}),
    # Measured before stamp_for: these three were stamped v1 with 100% fallback.
    ([{"question": "q1?"}, {"question": "q2?"}], False, None, {"snippet_fallback": 2}),
    ([{"id": "a", "question": "q1?"}, {"id": "a", "question": "q2?"}], False,
     None, {"snippet_fallback": 2}),
    ([{"id": "a", "question": "q1?"}, {"id": "b", "question": "q2?"}], True,
     None, {"snippet_fallback": 2}),
])
def test_stamp_follows_the_score_sources(monkeypatch, questions, from_disk, stamp, sources):
    _patch_llm(monkeypatch, [])
    runner = ArmRunner([_DocsPerQuestion()])
    records = runner.run("oneTBB", questions, concurrency=1)
    if from_disk:
        runner = ArmRunner([_DocsPerQuestion()])  # a new process: empty store
    evaluations = runner.judge(_FakeJudge(), "oneTBB", records, baseline_arm="docs-q")
    out = runner.build_output("oneTBB", records, evaluations, baseline_arm="docs-q",
                              judge=_FakeJudge())
    assert out.get("judge_context") == stamp
    assert out["judge_context_sources"] == sources


def test_baseline_arm_makes_missing_ids_a_mixed_stamp(monkeypatch):
    """The baseline's empty material is never ambiguous, so it counts as stored."""
    from dokimi.eval.judge_context import PROTOCOL_MIXED

    _patch_llm(monkeypatch, [])
    runner = ArmRunner([BaselineTreatment(), _DocsPerQuestion()])
    records = runner.run("oneTBB", [{"question": "q1?"}, {"question": "q2?"}], concurrency=1)
    evaluations = runner.judge(_FakeJudge(), "oneTBB", records, baseline_arm="baseline")
    out = runner.build_output("oneTBB", records, evaluations, baseline_arm="baseline",
                              judge=_FakeJudge())
    assert out["judge_context"] == PROTOCOL_MIXED
    assert out["judge_context_sources"] == {"stored": 2, "snippet_fallback": 2}


def test_stamp_for_mixed_and_empty_sources():
    from dokimi.eval.judge_context import PROTOCOL, PROTOCOL_MIXED, stamp_for

    assert stamp_for(["stored", "snippet_fallback"]) == PROTOCOL_MIXED
    assert stamp_for(["stored", None]) == PROTOCOL
    assert stamp_for([None]) == PROTOCOL       # nothing judged: keep the protocol
    assert stamp_for([]) == PROTOCOL
    assert stamp_for(["snippet_fallback", None]) is None


# ── Judge material persisted in the artifact ──────────────────────────────

def _judged_output(monkeypatch, treatments, questions, judge=None):
    _patch_llm(monkeypatch, [])
    runner = ArmRunner(treatments)
    records = runner.run("oneTBB", questions, concurrency=1)
    judge = judge or _ContextCapturingJudge()
    evaluations = runner.judge(judge, "oneTBB", records, baseline_arm=treatments[0].name)
    return runner.build_output("oneTBB", records, evaluations, baseline_arm=treatments[0].name,
                               judge=judge), judge


def test_a_judge_reading_the_artifact_sees_what_the_arms_run_judge_saw(
    monkeypatch, skill_with_files
):
    """The material used to live in memory only, so a judge reading the file got
    the 200-char snippets. Round trip through JSON and a fresh runner."""
    import json

    from dokimi.eval.judge_context import JudgeContextStore

    skill = SkillTreatment(load_skill(skill_with_files))
    out, first = _judged_output(monkeypatch, [BaselineTreatment(), skill], QUESTIONS)
    data = json.loads(json.dumps(out))

    later = ArmRunner([BaselineTreatment(), skill])
    later.judge_context = JudgeContextStore.from_artifact(data)
    second = _ContextCapturingJudge()
    evaluations = later.judge(second, "oneTBB", data["answers"])

    assert sorted(second.seen.values()) == sorted(first.seen.values())
    assert all(s["judge_context_source"] == "stored"
               for ev in evaluations for s in ev["scores"].values() if s)
    rejudged = later.build_output("oneTBB", data["answers"], evaluations, judge=second)
    assert rejudged["judge_context"] == "treatment_material.v1"


def test_a_skill_body_shared_by_every_question_is_stored_once(monkeypatch, skill_with_files):
    skill = SkillTreatment(load_skill(skill_with_files))
    out, _ = _judged_output(monkeypatch, [BaselineTreatment(), skill], QUESTIONS)
    digests = {r["arms"][skill.name]["judge_context_sha256"] for r in out["answers"]}
    assert len(digests) == 1 and len(QUESTIONS) > 1
    assert digests <= set(out["judge_materials"])
    score_digests = {ev["scores"][skill.name]["judge_context_sha256"] for ev in out["evaluations"]}
    assert score_digests == digests


def test_question_id_zero_is_cited_and_served_after_a_round_trip(monkeypatch, skill_with_files):
    """0 is falsy but a valid id; cite() must stamp it and from_artifact must serve it."""
    import json

    from dokimi.eval.judge_context import JudgeContextStore

    skill = SkillTreatment(load_skill(skill_with_files))
    questions = [{"id": 0, "question": "How do I parallelize a loop?"},
                 {"id": 1, "question": "How do I sum an array in parallel?"}]
    out, _ = _judged_output(monkeypatch, [BaselineTreatment(), skill], questions)
    assert all(r["arms"][skill.name].get("judge_context_sha256") for r in out["answers"])
    store = JudgeContextStore.from_artifact(json.loads(json.dumps(out)))
    assert store.get(0, skill.name) and store.get(1, skill.name)


def test_an_unjudged_run_still_keeps_the_material(monkeypatch, skill_with_files):
    """So an answers-only artifact can be judged later on the same evidence."""
    _patch_llm(monkeypatch, [])
    skill = SkillTreatment(load_skill(skill_with_files))
    runner = ArmRunner([BaselineTreatment(), skill])
    out = runner.build_output("oneTBB", runner.run("oneTBB", QUESTIONS, concurrency=1))
    assert "evaluations" not in out and out["judge_materials"]


def test_material_that_does_not_match_its_digest_is_not_used(monkeypatch, skill_with_files, caplog):
    import json

    from dokimi.eval.judge_context import JudgeContextStore

    skill = SkillTreatment(load_skill(skill_with_files))
    out, _ = _judged_output(monkeypatch, [BaselineTreatment(), skill], QUESTIONS)
    data = json.loads(json.dumps(out))
    digest = data["answers"][0]["arms"][skill.name]["judge_context_sha256"]
    data["judge_materials"][digest] += " tampered"
    with caplog.at_level("WARNING"):
        store = JudgeContextStore.from_artifact(data)
    assert store.get(data["answers"][0]["question_id"], skill.name) is None
    assert "does not match its digest" in caplog.text


def test_an_artifact_without_materials_falls_back_as_before():
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.utils import digest_text

    # No judge_materials key at all; record has digest but material is missing
    store = JudgeContextStore.from_artifact({
        "answers": [{"question_id": "q1", "arms": {"a": {"judge_context_sha256": digest_text("text")}}}]
    })
    # Store is empty; from_artifact found no materials key and returned early
    assert store.materials() == {}


def test_judge_materials_guard_requires_material_for_stored_scores(caplog):
    """The guard against missing material does not skip records with no digest."""
    from dokimi.eval.judge_context import JudgeContextStore
    from dokimi.utils import digest_text

    # Record with no digest — should be skipped without error.
    store = JudgeContextStore.from_artifact({
        "judge_materials": {},
        "answers": [{"question_id": "q1", "arms": {"a": {"answer": "text"}}}],
    })
    # Store is empty; the arm record had no judge_context_sha256, so nothing was added.
    assert store.materials() == {}
    # Reaching here proves `materials.get(digest)` never ran with a missing digest.

    # Record with a digest whose text is not in materials (hand-trimmed file).
    store = JudgeContextStore.from_artifact({
        "judge_materials": {},
        "answers": [{
            "question_id": "q1",
            "arms": {"a": {"answer": "text", "judge_context_sha256": "sha256:00"}},
        }],
    })
    # The arm record is skipped, and the store stays empty.
    assert store.materials() == {}
    assert "1 arm records cite judge material" in caplog.text

    caplog.clear()
    # Record with digest and material text that is not a string
    store = JudgeContextStore.from_artifact({
        "judge_materials": {digest_text("text"): 123},  # material is int, not str
        "answers": [{
            "question_id": "q1",
            "arms": {"a": {"judge_context_sha256": digest_text("text")}},
        }],
    })
    # The record is skipped due to non-string material
    assert store.materials() == {}
    assert "1 arm records cite judge material" in caplog.text

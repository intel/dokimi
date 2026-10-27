# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A model on an OpenAI-compatible endpoint (``--provider openai``, ``OPENAI_API_BASE``) is
called, not refused by litellm's provider lookup (#136).

The production adapter runs here with litellm's own routing: ``litellm.completion`` gets
``mock_response``, so it still resolves the provider from the model string, and refuses a name
it cannot route ("LLM Provider NOT provided"), but sends nothing.
"""

import json

import pytest

from dokimi.llm import LLM

JUDGE_REPLY = json.dumps({"correctness": 7, "completeness": 7, "specificity": 7, "code_quality": 7,
                          "actionability": 7, "aggregate": 0})


@pytest.fixture
def litellm_routes(monkeypatch):
    """``litellm.completion`` with its real provider lookup and a canned reply; the routed names land in the list."""
    import litellm

    from dokimi import caching

    real, routed = litellm.completion, []

    def completion(**kwargs):
        reply = real(**kwargs, mock_response=JUDGE_REPLY)  # raises where litellm cannot route the name
        routed.append(kwargs["model"])
        return reply

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return routed


@pytest.mark.parametrize("model", ["glm-5.3", "meta-llama/Llama-3.1-8B", "claude-sonnet-5-5"])
def test_a_model_off_litellms_openai_lists_is_sent_to_the_openai_endpoint(litellm_routes, model):
    assert LLM(model, "openai").ask("Reply with OK.") == JUDGE_REPLY
    assert litellm_routes == [f"openai/{model}"]


def test_cross_judge_scores_with_an_openai_compatible_judge(litellm_routes, tmp_path):
    """The reported command: before the fix every score failed and the arm read NOT MEASURED."""
    from dokimi.cli import build_parser

    answers = tmp_path / "run.json"
    answers.write_text(json.dumps({"library_name": "L", "baseline_arm": "baseline", "answers": [
        {"question_id": f"Q{i}", "question_text": "How do I use X?",
         "arms": {"baseline": {"answer": f"b{i}"}, "skill": {"answer": f"s{i}"}}} for i in range(3)]}),
        encoding="utf-8")
    out = tmp_path / "cj.json"
    args = build_parser().parse_args(["eval", "cross-judge", "--answers", str(answers), "--no-stored",
                                      "--judges", "openai/glm-5.3", "--output", str(out)])
    args.func(args)
    assert litellm_routes == ["openai/glm-5.3"] * 6  # three questions, two arms
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["arms"]["skill"]["label"] != "NOT MEASURED"

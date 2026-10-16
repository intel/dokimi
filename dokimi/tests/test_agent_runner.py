# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the agentic tool-calling loop and agentic treatment arms."""

import json

import pytest
from conftest import DEMO_SKILL_MARKER

import dokimi.eval.agent_runner as ar_mod
from dokimi.eval.agent_runner import run_agent_loop
from dokimi.eval.arm_summary import TOOL_COUNTS, TOOL_COUNTS_MIXED, summarize_agentic_usage
from dokimi.treatments import (
    MCPAgentTreatment,
    SkillAgentTreatment,
    create_treatment,
)
from dokimi.treatments.tools import DocQueryTool, ViewSkillTool
from dokimi.skills import load_skill


# ── fake litellm plumbing ────────────────────────────────────────────────
class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tool_call_id, name, arguments):
        self.id = tool_call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeUsage:
    prompt_tokens = 5
    completion_tokens = 3
    total_tokens = 8


class _FakeResp:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]
        self.usage = _FakeUsage()


def _scripted_completion(steps):
    """Return a chat_completion stub yielding the given messages in order."""
    calls = {"n": 0}

    def fake(messages, model, provider="openai", tools=None, tool_choice=None,
             api_key=None, **kw):
        idx = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        return _FakeResp(steps[idx])

    return fake, calls


class _FakeDocClient:
    def get_library_docs(self, library_id, query, max_results=5, **kw):
        return [{"content": f"DOC about {query}", "relevance_score": 1.0}]


# ── tools ─────────────────────────────────────────────────────────────────
def test_doc_query_tool_schema_and_call():
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    schema = tool.schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "search_documentation"
    assert "query" in schema["function"]["parameters"]["properties"]
    out = tool.call(query="parallel_for")
    assert "DOC about parallel_for" in out
    assert tool.calls == ["parallel_for"]


def test_view_skill_tool(demo_skill):
    skill = load_skill(demo_skill)
    tool = ViewSkillTool(skill)
    assert tool.name.startswith("view_skill_")
    body = tool.call()
    assert DEMO_SKILL_MARKER in body
    assert tool.viewed is True


def test_view_skill_tool_multifile(tmp_path):
    """Test that multi-file skills offer file parameter and can load sibling files."""
    # Create a minimal multi-file skill fixture
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()

    # Create SKILL.md
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: test-skill
description: Test skill for multi-file support
---
# Test Skill

Navigation table here.
""")

    # Create topic files
    (skill_dir / "topic-a.md").write_text("# Topic A\nContent for topic A.")
    (skill_dir / "topic-b.md").write_text("# Topic B\nContent for topic B.")

    # Load skill and create tool
    skill = load_skill(skill_dir)
    tool = ViewSkillTool(skill)

    # Check that tool has file parameter with enum of available files
    assert "properties" in tool.parameters
    assert "file" in tool.parameters["properties"]

    # Multi-file skill: should have enum with SKILL.md + sibling .md files
    file_param = tool.parameters["properties"]["file"]
    assert file_param["type"] == "string"
    assert "enum" in file_param
    assert "SKILL.md" in file_param["enum"]
    assert "topic-a.md" in file_param["enum"]
    assert "topic-b.md" in file_param["enum"]
    assert len(file_param["enum"]) == 3  # SKILL.md + 2 topics

    # Test loading SKILL.md (default)
    body = tool.call()
    assert "test-skill" in body.lower()
    assert "Navigation table" in body
    assert tool.viewed is True

    # Test loading a specific topic file
    topic_content = tool.call(file="topic-a.md")
    assert "topic-a.md" in topic_content
    assert "Topic A" in topic_content
    assert "Content for topic A" in topic_content


# ── loop ────────────────────────────────────────────────────────────────
def test_agent_loop_calls_tool_then_answers(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    steps = [
        _FakeMessage(tool_calls=[_FakeToolCall("c1", "search_documentation",
                                              '{"query": "reduce"}')]),
        _FakeMessage(content="Final answer using docs."),
    ]
    fake, calls = _scripted_completion(steps)
    monkeypatch.setattr(ar_mod, "chat_completion", fake)

    result = run_agent_loop("How to reduce?", [tool], model="m", provider="openai")
    assert result["answer"] == "Final answer using docs."
    assert result["stopped_reason"] == "answered"
    assert result["tool_call_count"] == 1
    assert result["transcript"][0]["tool"] == "search_documentation"
    assert result["transcript"][0]["iteration"] == 1
    assert result["transcript"][0]["arguments"] == {"query": "reduce"}
    assert result["tool_usage"]["used_any_tool"] is True
    assert result["tool_usage"]["tool_call_count_by_name"]["search_documentation"] == 1
    assert result["token_usage"]["total_tokens"] == 16  # two rounds × 8
    # Telemetry: usage record, per-turn detail, per-tool timing.
    assert result["usage"]["total_tokens"] == 16
    assert result["usage"]["n_calls"] == 2
    assert len(result["per_turn"]) == 2
    assert "latency_sec" in result["per_turn"][0]
    assert "tool_elapsed_sec" in result["transcript"][0]
    json.dumps(result)


def test_agent_loop_answers_without_tools(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    fake, _ = _scripted_completion([_FakeMessage(content="Direct answer.")])
    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    result = run_agent_loop("q", [tool], model="m")
    assert result["answer"] == "Direct answer."
    assert result["tool_call_count"] == 0
    assert result["tool_usage"]["used_any_tool"] is False
    assert result["tool_usage"]["called_tools"] == []


def test_agent_loop_reports_skill_discoverability(monkeypatch, demo_skill):
    skill = load_skill(demo_skill)
    tool = ViewSkillTool(skill)
    steps = [
        _FakeMessage(tool_calls=[_FakeToolCall("c1", tool.name, "{}")]),
        _FakeMessage(content="Used skill."),
    ]
    fake, _ = _scripted_completion(steps)
    monkeypatch.setattr(ar_mod, "chat_completion", fake)

    result = run_agent_loop("q", [tool], model="m")

    skill_usage = result["tool_usage"]["skill"]
    assert skill_usage["offered"] is True
    assert skill_usage["called"] is True
    assert skill_usage["call_count"] == 1
    assert skill_usage["first_call_iteration"] == 1
    assert skill_usage["offered_tools"][0]["skill_name"] == "demo-skill"


def test_agent_loop_hits_max_iterations(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    # Always asks for a tool → never answers until forced.
    loop_step = _FakeMessage(tool_calls=[_FakeToolCall("c", "search_documentation",
                                                       '{"query": "x"}')])
    final = _FakeMessage(content="Forced final answer.")
    steps = [loop_step, loop_step, final]
    fake, _ = _scripted_completion(steps)
    monkeypatch.setattr(ar_mod, "chat_completion", fake)

    result = run_agent_loop("q", [tool], model="m", max_iterations=2)
    assert result["stopped_reason"] == "max_iterations"
    assert result["iterations"] == 2
    assert result["answer"] == "Forced final answer."


def test_forced_final_turn_passes_litellm_bedrock_mapping(monkeypatch):
    # Every request goes through litellm's own Bedrock parameter mapping and
    # Converse request build, offline: `tool_choice="none"` is refused there, and a
    # tool-call history without `tools=` has to be accepted.
    import litellm
    from litellm.llms.bedrock.chat.converse_transformation import AmazonConverseConfig
    from litellm.utils import get_optional_params

    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    loop_step = _FakeMessage(tool_calls=[_FakeToolCall("c", "search_documentation", "{}")])
    sent = []

    def fake_completion(model, messages, api_key=None, **kw):
        bedrock_model = model.split("/", 1)[1]
        optional = get_optional_params(model=bedrock_model, custom_llm_provider="bedrock", **kw)
        AmazonConverseConfig().transform_request(
            model=bedrock_model, messages=messages, optional_params=optional,
            litellm_params={}, headers={},
        )
        sent.append(kw)
        return _FakeResp(loop_step if len(sent) < 2 else _FakeMessage(content="done"))

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = run_agent_loop("q", [tool], model="anthropic.claude-3-5-haiku-20241022-v1:0",
                            provider="amazon-bedrock", api_key="k", max_iterations=1)
    assert result["answer"] == "done" and len(sent) == 2
    assert "tool_choice" not in sent[-1]


def test_failed_call_keeps_the_tool_usage_so_far(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    loop_step = _FakeMessage(tool_calls=[_FakeToolCall("c", "search_documentation",
                                                       '{"query": "x"}')])
    calls = []

    def fake(messages, model, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("provider refused")
        return _FakeResp(loop_step)

    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    with pytest.raises(RuntimeError, match="provider refused") as exc:
        run_agent_loop("q", [tool], model="m", max_iterations=3)
    assert exc.value.tool_usage["called_tools"] == ["search_documentation"]


def test_agent_loop_unknown_tool_is_handled(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    steps = [
        _FakeMessage(tool_calls=[_FakeToolCall("c1", "nonexistent", "{}")]),
        _FakeMessage(content="ok"),
    ]
    fake, _ = _scripted_completion(steps)
    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    result = run_agent_loop("q", [tool], model="m")
    assert "unknown tool" in result["transcript"][0]["result_preview"]
    assert result["transcript"][0]["error"] == "unknown_tool"
    assert result["tool_usage"]["call_errors"]["unknown_tool"] == 1


def _run_with_one_call(monkeypatch, tool, name, arguments):
    """Run the loop on one scripted tool call, then an answer; return result and messages sent."""
    steps = [
        _FakeMessage(tool_calls=[_FakeToolCall("c1", name, arguments)]),
        _FakeMessage(content="ok"),
    ]
    sent = []

    def fake(messages, model, **kw):
        sent.append([dict(m) for m in messages])
        return _FakeResp(steps[min(len(sent) - 1, len(steps) - 1)])

    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    return run_agent_loop("q", [tool], model="m"), sent


def test_malformed_arguments_do_not_run_the_tool(monkeypatch):
    # On main the parse failure became {} and DocQueryTool searched for "".
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, sent = _run_with_one_call(monkeypatch, tool, "search_documentation",
                                      '{"query": "parallel_for"')
    assert tool.calls == []
    rec = result["transcript"][0]
    assert rec["error"] == "malformed_args"
    assert rec["arguments"] == {}
    assert rec["arguments_raw"] == '{"query": "parallel_for"'
    assert result["tool_usage"]["call_errors"] == {
        "unknown_tool": 0, "malformed_args": 1, "tool_error": 0}
    # The model is told, so it can retry with valid arguments.
    tool_msg = [m for m in sent[1] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith("(invalid tool arguments: ")


@pytest.mark.parametrize("arguments", ["{}", "", '{"other": 1}'])
def test_missing_required_argument_does_not_run_the_tool(monkeypatch, arguments):
    # `query` is required by the schema; `{}` parses but must not search for "".
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, sent = _run_with_one_call(monkeypatch, tool, "search_documentation", arguments)
    assert tool.calls == []
    assert result["transcript"][0]["error"] == "malformed_args"
    tool_msg = [m for m in sent[1] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "(invalid tool arguments: 'query' is a required property)"


@pytest.mark.parametrize("arguments, where", [
    ('{"query": 123}', "query: 123 is not of type 'string'"),
    ('{"query": ["a"]}', "query: ['a'] is not of type 'string'"),
])
def test_wrong_argument_type_does_not_run_the_tool(monkeypatch, arguments, where):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, sent = _run_with_one_call(monkeypatch, tool, "search_documentation", arguments)
    assert tool.calls == []
    assert result["transcript"][0]["error"] == "malformed_args"
    tool_msg = [m for m in sent[1] if m["role"] == "tool"][0]
    assert tool_msg["content"] == f"(invalid tool arguments: {where})"


def test_skill_file_outside_the_enum_is_malformed(monkeypatch, demo_skill):
    tool = ViewSkillTool(load_skill(demo_skill))
    tool.parameters = {"type": "object", "required": [],
                       "properties": {"file": {"type": "string", "enum": ["SKILL.md"]}}}
    result, _ = _run_with_one_call(monkeypatch, tool, tool.name, '{"file": "../secrets.md"}')
    assert tool.viewed is False
    assert result["transcript"][0]["error"] == "malformed_args"


@pytest.mark.parametrize("arguments", ['{"query": "x"', "{}", '{"query": 1}'])
def test_dropped_calls_do_not_count_as_used(monkeypatch, arguments):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", arguments)
    usage = result["tool_usage"]
    assert result["tool_call_count"] == 0
    assert usage["used_any_tool"] is False
    assert usage["called_tools"] == []
    assert usage["tool_call_count_by_name"] == {"search_documentation": 0}
    assert usage["first_call_iteration_by_name"] == {}
    assert usage["call_errors"]["malformed_args"] == 1


def test_failed_skill_call_is_not_loaded(monkeypatch, demo_skill):
    # arm_runner feeds skill.loaded to judge_reference_for_loaded: a skill the
    # agent never read must not reach the judge reference.
    class _Boom(ViewSkillTool):
        def call(self, **kw):
            raise OSError("skill dir gone")

    tool = _Boom(load_skill(demo_skill))
    steps = [
        _FakeMessage(tool_calls=[_FakeToolCall("c1", "nonexistent", "{}"),
                                 _FakeToolCall("c2", tool.name, "{}")]),
        _FakeMessage(content="ok"),
    ]
    fake, _ = _scripted_completion(steps)
    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    result = run_agent_loop("q", [tool], model="m")
    skill = result["tool_usage"]["skill"]
    assert skill["called"] is False and skill["call_count"] == 0
    assert skill["loaded"] == [] and skill["selected"] is None
    assert skill["first_turn_loaded"] == [] and skill["first_call_iteration"] is None
    assert "nonexistent" not in result["tool_usage"]["tool_call_count_by_name"]
    assert result["tool_usage"]["call_errors"] == {
        "unknown_tool": 1, "malformed_args": 0, "tool_error": 1}


def test_empty_arguments_still_run_a_tool_with_no_required_argument(monkeypatch):
    class _NoArgs(DocQueryTool):
        parameters = {"type": "object", "properties": {}}

    tool = _NoArgs(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", "{}")
    assert tool.calls == [""]
    assert "error" not in result["transcript"][0]


def test_non_object_arguments_are_malformed(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", '["parallel_for"]')
    assert tool.calls == []
    assert result["transcript"][0]["error"] == "malformed_args"
    assert "not list" in result["transcript"][0]["result_preview"]


def test_already_parsed_arguments_run_the_tool(monkeypatch):
    # Some providers hand back a dict, not a JSON string. That must run, not raise.
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, sent = _run_with_one_call(
        monkeypatch, tool, "search_documentation", {"query": "reduce"})
    assert tool.calls == ["reduce"]
    assert "error" not in result["transcript"][0]
    assert result["transcript"][0]["arguments"] == {"query": "reduce"}
    assistant = [m for m in sent[1] if m["role"] == "assistant"][0]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "reduce"}'


def test_a_non_string_non_object_does_not_crash_the_loop(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", ["reduce"])
    assert tool.calls == []
    assert result["transcript"][0]["error"] == "malformed_args"
    assert result["tool_call_count"] == 0


def test_a_null_function_on_a_dict_call_does_not_crash_the_loop(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    steps = [
        _FakeMessage(tool_calls=[{"id": "c1", "function": None}]),
        _FakeMessage(content="ok"),
    ]
    fake, _ = _scripted_completion(steps)
    monkeypatch.setattr(ar_mod, "chat_completion", fake)
    result = run_agent_loop("q", [tool], model="m")
    assert tool.calls == []
    assert result["transcript"][0]["error"] == "unknown_tool"


def test_tool_exception_is_recorded_as_tool_error(monkeypatch):
    class _Boom(DocQueryTool):
        def call(self, **kw):
            raise ValueError("index offline")

    tool = _Boom(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", '{"query": "x"}')
    rec = result["transcript"][0]
    assert rec["error"] == "tool_error"
    assert "arguments_raw" not in rec
    assert "index offline" in rec["result_preview"]
    assert result["tool_usage"]["call_errors"]["tool_error"] == 1


def test_successful_call_has_no_error_key(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", '{"query": "x"}')
    assert tool.calls == ["x"]
    assert "error" not in result["transcript"][0]
    assert sum(result["tool_usage"]["call_errors"].values()) == 0


# ── agentic arms ──────────────────────────────────────────────────────────
def test_mcp_agent_treatment_is_agentic():
    t = MCPAgentTreatment(_FakeDocClient())
    cfg = t.prepare("q", "oneTBB", "lib/x")
    assert cfg.is_agentic
    assert len(cfg.tools) == 1
    assert cfg.metadata["arm_kind"] == "mcp_agent"


def test_skill_agent_treatment_is_agentic(demo_skill):
    skill = load_skill(demo_skill)
    t = SkillAgentTreatment(skill)
    cfg = t.prepare("q", "demo")
    assert cfg.is_agentic
    assert cfg.tools[0].name.startswith("view_skill_")


def test_factory_builds_agentic_specs(demo_skill):
    assert isinstance(create_treatment(f"skill-agent:{demo_skill.as_posix()}"),
                      SkillAgentTreatment)
    # 'agent:<source>' with a nested colon must not be mis-parsed
    t = create_treatment("agent:context7")
    assert isinstance(t, MCPAgentTreatment)


# ── tool-count stamp ────────────────────────────────────────────────────
def _agentic_record(**tool_usage):
    return {"arms": {"agent": {"agentic": True, "tool_call_count": 1, "iterations": 1,
                               **({"tool_usage": tool_usage} if tool_usage else {})}}}


def test_tool_usage_is_stamped(monkeypatch):
    tool = DocQueryTool(_FakeDocClient(), "lib/x")
    result, _ = _run_with_one_call(monkeypatch, tool, "search_documentation", '{"query": "x"}')
    assert result["tool_usage"]["counts"] == TOOL_COUNTS


@pytest.mark.parametrize("records, stamp", [
    ([_agentic_record(counts=TOOL_COUNTS)] * 2, TOOL_COUNTS),
    ([_agentic_record(counts=TOOL_COUNTS), _agentic_record(skill={})], TOOL_COUNTS_MIXED),
    ([_agentic_record(skill={})], None),
    # An arm that failed to prepare has no tool_usage, under either protocol.
    ([_agentic_record(counts=TOOL_COUNTS), _agentic_record()], TOOL_COUNTS),
])
def test_agentic_summary_stamps_tool_counts(records, stamp):
    assert summarize_agentic_usage(records)["agent"].get("tool_counts") == stamp

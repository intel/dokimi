# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Agentic tool-calling loop for treatment arms that offer tools.

Given an :class:`~dokimi.treatments.base.AgentConfig` whose ``tools``
list is non-empty, this runs a bounded loop: send the conversation + tool
schemas to the model, execute any tool calls it makes, feed the results back,
and repeat until the model answers or the iteration budget is exhausted. It
returns the final answer plus a transcript of the tool calls made — the signal
for whether an agent actually *uses* an MCP doc server or a skill.

In-process tools are read-only (doc search, skill viewing). Faithful skill
*script execution* lives on the terminal-bench task track, which provides real
sandbox isolation.
"""

import json
import logging
import time
from typing import Any, Dict, List

import jsonschema

from dokimi.eval.arm_summary import TOOL_COUNTS
from dokimi.llm import chat_completion, _extract_usage
from dokimi.metrics.usage import UsageRecord
from dokimi.treatments.base import Tool

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 6


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content or ""


def _extract_tool_calls(message: Any) -> List[Any]:
    calls = getattr(message, "tool_calls", None)
    if calls is None and isinstance(message, dict):
        calls = message.get("tool_calls")
    return calls or []


def _tool_call_fields(tc: Any):
    """Return (id, name, arguments) from a tool-call object or dict.

    ``arguments`` is whatever the provider sent: a JSON string, an already
    parsed object, or missing. A null ``function`` is not a call.
    """
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        return tc.get("id"), fn.get("name"), fn.get("arguments")
    fn = getattr(tc, "function", None)
    if fn is None:
        return getattr(tc, "id", None), None, None
    return getattr(tc, "id", None), getattr(fn, "name", None), getattr(fn, "arguments", None)


#: Values of a transcript record's ``error``: the call never ran the tool
#: (``unknown_tool``, ``malformed_args``) or the tool raised (``tool_error``).
CALL_ERRORS = ("unknown_tool", "malformed_args", "tool_error")


def _arguments_text(raw: Any) -> str:
    """The arguments as a JSON string, for the message sent back to the model."""
    if raw is None or raw == "":
        return "{}"
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, sort_keys=True)
    except (TypeError, ValueError):
        return str(raw)


def _parse_arguments(raw: Any):
    """Return ``(args, problem)`` for a tool call's arguments.

    ``problem`` is None when ``args`` is a usable keyword dict. A provider may
    already have parsed the JSON into a dict; ``json.loads`` on that dict raises
    ``TypeError`` and used to abort the loop. Running the tool on ``{}`` after a
    parse failure would hand it its defaults (an empty search query) and record
    a call that looks like the model asked for that.
    """
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    if not isinstance(raw, str):
        return {}, f"arguments must be a JSON object, not {type(raw).__name__}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments are not valid JSON ({exc.msg})"
    if not isinstance(args, dict):
        return {}, f"arguments must be a JSON object, not {type(args).__name__}"
    return args, None


def _schema_problem(tool: Tool, args: Dict[str, Any]):
    """Return a problem string when ``args`` does not match the tool's schema.

    ``{}`` parses, but handing it to a tool whose ``query`` is required runs the
    same empty search the parse-failure path no longer does; ``{"query": 123}``
    reaches the tool with a type it does not take. Keys the schema does not
    list still pass, unless the schema sets ``additionalProperties``.
    """
    schema = tool.parameters
    error = jsonschema.exceptions.best_match(
        jsonschema.validators.validator_for(schema)(schema).iter_errors(args))
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path)
    return f"{where}: {error.message}" if where else error.message


def _completed_calls(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transcript records whose tool ran and returned (no ``error``)."""
    return [call for call in transcript if not call.get("error")]


def _summarize_tool_usage(
    tools: List[Tool], transcript: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Summarize offered vs called tools for discoverability reporting.

    A failed call counts only in ``call_errors``: every other field counts calls
    that returned a result, so a skill whose call was dropped or raised is not
    ``loaded`` (and does not reach the judge reference built from that list).
    ``counts`` stamps that protocol (:data:`~dokimi.eval.arm_summary.TOOL_COUNTS`).
    """
    offered = [t.telemetry_metadata() for t in tools]
    offered_by_name = {t["name"]: t for t in offered}
    calls_by_name: Dict[str, int] = {name: 0 for name in offered_by_name}
    first_iteration_by_name: Dict[str, int] = {}
    call_errors: Dict[str, int] = {kind: 0 for kind in CALL_ERRORS}

    for call in transcript:
        if call.get("error") in call_errors:
            call_errors[call["error"]] += 1
    ran = _completed_calls(transcript)

    for call in ran:
        name = call.get("tool")
        if not name:
            continue
        calls_by_name[name] = calls_by_name.get(name, 0) + 1
        if name not in first_iteration_by_name and call.get("iteration") is not None:
            first_iteration_by_name[name] = call["iteration"]

    called_tools = [name for name, count in calls_by_name.items() if count > 0]
    skill_tools = [t for t in offered if t.get("kind") == "skill"]
    skill_tool_names = {t["name"] for t in skill_tools}
    skill_calls = sum(calls_by_name.get(name, 0) for name in skill_tool_names)
    # Skill names in the order the model first loaded them; the head is what a
    # selection metric scores (a catalog arm offers many skill tools at once).
    skill_by_tool = {t["name"]: t.get("skill_name") for t in skill_tools}
    skills_loaded: List[str] = []
    # Every skill loaded in the first turn that loaded any: one turn can load
    # several in parallel, and their order within the turn is not a choice.
    first_turn_loaded: List[str] = []
    first_turn = None
    for call in ran:
        skill_name = skill_by_tool.get(call.get("tool"))
        if not skill_name:
            continue
        if first_turn is None:
            first_turn = call.get("iteration")
        if call.get("iteration") == first_turn and skill_name not in first_turn_loaded:
            first_turn_loaded.append(skill_name)
        if skill_name not in skills_loaded:
            skills_loaded.append(skill_name)

    return {
        "offered_tools": offered,
        "called_tools": called_tools,
        "tool_call_count_by_name": calls_by_name,
        "first_call_iteration_by_name": first_iteration_by_name,
        "used_any_tool": bool(called_tools),
        "call_errors": call_errors,
        "counts": TOOL_COUNTS,
        "skill": {
            "offered": bool(skill_tools),
            "offered_tools": skill_tools,
            "called": skill_calls > 0,
            "call_count": skill_calls,
            "offered_count": len(skill_tools),
            "loaded": skills_loaded,
            "selected": skills_loaded[0] if skills_loaded else None,
            "first_turn_loaded": first_turn_loaded,
            "first_call_iteration": min(
                (
                    first_iteration_by_name[name]
                    for name in skill_tool_names
                    if name in first_iteration_by_name
                ),
                default=None,
            ),
        },
    }


def run_agent_loop(
    question: str,
    tools: List[Tool],
    model: str,
    provider: str = "openai",
    system_prompt: str | None = None,
    api_key: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int | None = None,
    temperature: float | None = None,
    seed: int | None = None,
) -> Dict[str, Any]:
    """Drive a tool-calling conversation to a final answer.

    Returns a dict with: ``answer``, ``transcript`` (list of tool-call records,
    each with ``tool_elapsed_sec``, plus ``error`` — one of :data:`CALL_ERRORS` —
    on a call that failed), ``tool_call_count`` and ``tool_usage`` (both count
    only calls without ``error``; ``tool_usage["call_errors"]`` counts the
    rest), ``iterations``, ``stopped_reason``,
    ``usage`` (serialised metrics accumulated across turns), ``token_usage`` (the
    legacy 3-key alias), and ``per_turn`` (per-turn latency/token detail).
    """
    by_name = {t.name: t for t in tools}
    schemas = [t.schema() for t in tools]

    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})

    transcript: List[Dict[str, Any]] = []
    usage_total = UsageRecord(model=model, provider=provider, n_calls=0)
    per_turn: List[Dict[str, Any]] = []
    stopped_reason = "answered"
    answer = ""

    def _complete(**kwargs):
        # A failed call loses the answer, not what the loop already did: the
        # caller can still see which skill was loaded before the failure.
        try:
            return chat_completion(messages=messages, model=model, provider=provider,
                                   api_key=api_key, max_tokens=max_tokens,
                                   temperature=temperature, seed=seed, **kwargs)
        except Exception as exc:
            exc.tool_usage = _summarize_tool_usage(tools, transcript)  # type: ignore[attr-defined]
            raise

    iteration = 0
    while iteration < max_iterations:
        iteration += 1
        t_turn = time.time()
        resp = _complete(tools=schemas)
        turn_usage = _extract_usage(resp, model, provider, latency_sec=time.time() - t_turn)
        usage_total = usage_total + turn_usage
        per_turn.append({
            "latency_sec": round(turn_usage.latency_sec, 4),
            "prompt_tokens": turn_usage.prompt_tokens,
            "completion_tokens": turn_usage.completion_tokens,
            "cache_read_tokens": turn_usage.cache_read_tokens,
        })

        message = resp.choices[0].message
        tool_calls = _extract_tool_calls(message)

        if not tool_calls:
            answer = _message_text(message)
            stopped_reason = "answered"
            break

        # Record the assistant turn (with its tool calls) so the model has context.
        serialized_calls = []
        for tc in tool_calls:
            tc_id, name, args = _tool_call_fields(tc)
            serialized_calls.append({
                "id": tc_id,
                "type": "function",
                "function": {"name": name or "unknown", "arguments": _arguments_text(args)},
            })
        messages.append({
            "role": "assistant",
            "content": _message_text(message),
            "tool_calls": serialized_calls,
        })

        # Execute each tool call and append its result.
        for tc in tool_calls:
            tc_id, name, args_str = _tool_call_fields(tc)
            args, bad_args = _parse_arguments(args_str)
            tool = by_name.get(name)
            if tool is not None and not bad_args:
                bad_args = _schema_problem(tool, args)
            t_tool = time.time()
            error = None
            if tool is None:
                result = f"(unknown tool: {name})"
                error = "unknown_tool"
            elif bad_args:
                # Tell the model, so it can retry; do not run the tool on {}.
                result = f"(invalid tool arguments: {bad_args})"
                error = "malformed_args"
            else:
                try:
                    result = tool.call(**args)
                except Exception as exc:  # tool failures must not crash the run
                    logger.exception("Tool %s raised", name)
                    result = f"(tool error: {exc})"
                    error = "tool_error"
            record = {
                "iteration": iteration,
                "tool": name,
                "arguments": args,
                "result_preview": result[:200],
                "tool_elapsed_sec": round(time.time() - t_tool, 4),
            }
            if error:
                record["error"] = error
                if error == "malformed_args":
                    record["arguments_raw"] = _arguments_text(args_str)[:200]
            transcript.append(record)
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })
    else:
        stopped_reason = "max_iterations"
        # Make a final, tool-free attempt so we still return an answer.
        messages.append({
            "role": "user",
            "content": "Provide your final answer now without calling tools.",
        })
        t_turn = time.time()
        # No ``tools``: litellm refuses ``tool_choice="none"`` for Bedrock, and
        # turns the history's tool blocks into text when ``tools`` is absent.
        resp = _complete()
        turn_usage = _extract_usage(resp, model, provider, latency_sec=time.time() - t_turn)
        usage_total = usage_total + turn_usage
        per_turn.append({
            "latency_sec": round(turn_usage.latency_sec, 4),
            "prompt_tokens": turn_usage.prompt_tokens,
            "completion_tokens": turn_usage.completion_tokens,
            "cache_read_tokens": turn_usage.cache_read_tokens,
        })
        answer = _message_text(resp.choices[0].message)

    return {
        "answer": answer,
        "transcript": transcript,
        "iterations": iteration,
        "tool_call_count": len(_completed_calls(transcript)),
        "tool_usage": _summarize_tool_usage(tools, transcript),
        "stopped_reason": stopped_reason,
        "usage": usage_total.as_metrics_dict(answer_chars=len(answer or "")),
        "token_usage": usage_total.as_token_usage_dict(),
        "per_turn": per_turn,
    }

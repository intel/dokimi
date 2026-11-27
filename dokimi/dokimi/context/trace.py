# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Claude Code ``stream-json`` trace -> the size of the context window at every model call.

Claude Code writes a ``usage`` block on every assistant message. One model call can span
several messages (one per content block) that share the message id, so a call is counted
once, by id, and its fields merge by maximum, so a later block with a sparse ``usage`` cannot
erase the prompt an earlier one recorded. The prompt the model saw on that call is

    prompt_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens

and it is ``None`` when no block of the call reported any of the three (a call with only
``output_tokens`` has an unknown prompt, not a zero one). The prompt is summed from the merged
parts, so blocks that each carry only some of the counts still add up. The first call's prompt
is everything loaded before the agent did anything (system prompt, tool schemas, memory files, skill listing, the task), and the series after it is how the
history grew. ``new_tokens`` (input + cache creation) is what the call added beyond the cached
prefix. Each call also keeps its parts (``input_tokens``, ``cache_read_tokens``, ``cache_write_tokens``) and
its message timestamp (``at``): the per-turn events of ``events.jsonl`` read them (:mod:`dokimi.repos.turn_events`).

Calls a subagent made carry ``parent_tool_use_id``; they run in their own window, so the
summary reads the main thread and counts subagent calls apart. A ``compact_boundary`` system
event is a compaction: Claude Code summarised the history and dropped it.

A repeated tool call is one identical in name and input to an earlier call on the same thread
that did not fail, with no call that can change the workspace (``MUTATING``) in between. A
retry after an error, or a ``Read`` after an ``Edit`` of the same file, is not a repeat.

Tool results are measured in characters, the only unit the trace has for them. Nothing here
calls a model or a tokenizer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokimi.utils import InputError, refuse_device

# Tools whose call can change what a later identical call returns; one ends every earlier repeat.
MUTATING = frozenset({"Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"})
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_PROMPT_FIELDS = frozenset(("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
_PARTS = ("output_tokens", "input_tokens", "cache_read_tokens", "cache_write_tokens")  # the counts a call is built from


class TraceError(ValueError):
    """The file is not a trace this module can read."""


@dataclass
class ToolStats:
    calls: int = 0
    errors: int = 0
    result_chars: int = 0
    max_result_chars: int = 0


@dataclass
class ContextTrace:
    """The context-window record of one run."""

    harness_version: str | None = None
    model: str | None = None
    surface: dict[str, Any] = field(default_factory=dict)  # what start-up offered the agent
    calls: list[dict[str, Any]] = field(default_factory=list)
    compactions: list[dict[str, Any]] = field(default_factory=list)
    tools: dict[str, ToolStats] = field(default_factory=dict)
    repeated_calls: int = 0  # tool calls identical to an earlier successful one, nothing mutating between
    num_turns: int | None = None
    result_subtype: str | None = None
    is_error: bool = False
    errors: list[str] = field(default_factory=list)

    def main_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["agent"] is None]

    def summary(self, window: int | None = None) -> dict[str, Any]:
        """The numbers a comparison reads; ``window`` (tokens) adds how full the peak got."""
        main = self.main_calls()
        prompts = [c["prompt_tokens"] for c in main if c["prompt_tokens"] is not None]
        peak = max(prompts, default=None)
        first = main[0]["prompt_tokens"] if main else None
        last = main[-1]["prompt_tokens"] if main else None
        top = sorted(self.tools.items(), key=lambda kv: -kv[1].result_chars)
        return {
            "harness_version": self.harness_version,
            "model": self.model,
            "calls": len(main),
            "subagent_calls": len(self.calls) - len(main),
            "first_prompt_tokens": first,
            "peak_prompt_tokens": peak,
            "final_prompt_tokens": last,
            "growth_tokens": peak - first if first is not None and peak is not None else None,
            "output_tokens": sum(c["output_tokens"] for c in self.calls),
            "peak_fill": round(peak / window, 4) if window and peak is not None else None,
            "compactions": len(self.compactions),
            "tool_calls": sum(s.calls for s in self.tools.values()),
            "failed_tool_calls": sum(s.errors for s in self.tools.values()),
            "repeated_tool_calls": self.repeated_calls,
            "tool_result_chars": sum(s.result_chars for s in self.tools.values()),
            "largest_tool_results": [
                {"tool": name, "calls": s.calls, "chars": s.result_chars, "max_chars": s.max_result_chars}
                for name, s in top[:5] if s.result_chars
            ],
            "surface": self.surface,
            "num_turns": self.num_turns,
            "result_subtype": self.result_subtype,
            "is_error": self.is_error,
        }


def read_trace(path: Path) -> ContextTrace:
    try:
        refuse_device(path)
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except InputError as exc:
        raise TraceError(str(exc)) from exc
    except OSError as exc:
        raise TraceError(f"cannot read {path}: {exc}") from exc
    trace = parse_trace(raw)
    if not trace.calls and not trace.surface:
        raise TraceError(f"{path}: no assistant message and no init event; is it a "
                         "`claude -p --output-format stream-json --verbose` trace?")
    return trace


def parse_trace(raw: str) -> ContextTrace:
    out = ContextTrace()
    by_message: dict[str, dict[str, Any]] = {}  # message id -> its call
    names: dict[str, str] = {}  # tool_use id -> tool name
    seen: dict[tuple[Any, str, str], str] = {}  # signature -> tool_use id of its latest call
    dormant: dict[str, int] = {}  # message id -> line of its first block, while all its blocks were zero: no call yet
    for n, line in enumerate(raw.split(chr(10)), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):  # JSONDecodeError, an over-long integer, deep nesting
            out.errors.append(f"line {n}: not JSON")
            continue
        if not isinstance(rec, dict):
            continue
        try:
            _apply(out, rec, by_message, names, seen, dormant, n)
        except (AttributeError, TypeError, ValueError, OverflowError, RecursionError) as exc:  # valid JSON of an unexpected shape
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    for compaction in out.compactions:
        at = compaction.pop("line")
        compaction["after_call"] = sum(c["line"] < at for c in out.calls) - 1
    for i, call in enumerate(out.calls):
        call["i"] = i
        del call["line"]
    return out


def _apply(out: ContextTrace, rec: dict[str, Any], by_message: dict[str, dict[str, Any]],
           names: dict[str, str], seen: dict[tuple[Any, str, str], str], dormant: dict[str, int], line: int) -> None:
    kind, subtype = rec.get("type"), rec.get("subtype")
    agent = rec.get("parent_tool_use_id")
    if kind == "system" and subtype == "init":
        out.harness_version = rec.get("claude_code_version")
        out.model = rec.get("model")
        out.surface = {
            "tools": len(rec.get("tools") or []),
            "mcp_servers": sorted(str(m.get("name")) for m in rec.get("mcp_servers") or [] if isinstance(m, dict)),
            "skills": len(rec.get("skills") or []),
            "agents": len(rec.get("agents") or []),
            "plugins": sorted(str(p.get("name")) for p in rec.get("plugins") or [] if isinstance(p, dict)),
        }
    elif kind == "system" and subtype == "compact_boundary":
        meta = rec.get("compact_metadata") or rec.get("compactMetadata") or {}
        out.compactions.append({
            "trigger": meta.get("trigger"),
            "pre_tokens": meta.get("pre_tokens", meta.get("preTokens")),
            "post_tokens": meta.get("post_tokens", meta.get("postTokens")),  # snake_case in newer CLIs; camelCase in compact metadata
            "after_call": len(out.calls) - 1,  # settled from "line" once every call is placed
            "at": rec.get("timestamp"),
            "line": line,
        })
    elif kind == "assistant":
        message = rec.get("message") or {}
        usage = message.get("usage")
        if isinstance(usage, dict):
            _add_call(out, _call(usage, agent, rec.get("timestamp"), line), message.get("id"), by_message, dormant)
        for block in _content(rec):
            if block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            tool_id = str(block.get("id") or "")
            names[tool_id] = name
            out.tools.setdefault(name, ToolStats()).calls += 1
            signature = (agent, name, json.dumps(block.get("input"), sort_keys=True, default=str))
            if signature in seen:
                out.repeated_calls += 1
            if name in MUTATING:
                seen.clear()  # the workspace may differ now, on every thread
            seen[signature] = tool_id
    elif kind == "user":
        for block in _content(rec):
            if block.get("type") != "tool_result":
                continue
            stats = out.tools.setdefault(names.get(str(block.get("tool_use_id") or ""), "?"), ToolStats())
            size = _result_chars(block.get("content"))
            stats.result_chars += size
            stats.max_result_chars = max(stats.max_result_chars, size)
            if block.get("is_error"):
                stats.errors += 1
                failed = str(block.get("tool_use_id") or "")
                for signature in [s for s, tool_id in seen.items() if tool_id == failed]:
                    del seen[signature]  # a retry of a failed call is not a repeat
    elif kind == "result":
        out.num_turns = rec.get("num_turns")
        out.result_subtype = subtype
        out.is_error = bool(rec.get("is_error"))


def _add_call(out: ContextTrace, call: dict[str, Any], key: Any, by_message: dict[str, dict[str, Any]],
              dormant: dict[str, int]) -> None:
    """Add one block's call, or fold it into the call its message id already has."""
    if key and key in by_message:
        _merge(by_message[key], call)  # a later block of the same call: same prompt, fuller output
        if key in dormant and any(by_message[key][k] for k in _PARTS):
            first = dormant.pop(key)  # the call sits where its first block was, not where it became real
            at = sum(c["line"] < first for c in out.calls)
            out.calls.insert(at, by_message[key])
    elif any(call[k] for k in _PARTS):  # an all-zero block carries no call yet
        out.calls.append(call)
        if key:
            by_message[key] = call
    elif key:
        by_message[key] = call  # keep what it reported for a later block of the same id
        dormant[key] = call["line"]


def _merge(call: dict[str, Any], later: dict[str, Any]) -> None:
    """Fold a later block into its call; the call keeps its thread and its largest counts."""
    for k in _PARTS:
        call[k] = max(call[k], later[k])
    call["reported"] = sorted({*call["reported"], *later["reported"]})
    if _PROMPT_FIELDS.isdisjoint(call["reported"]):
        return
    call["new_tokens"] = call["input_tokens"] + call["cache_write_tokens"]
    call["prompt_tokens"] = call["new_tokens"] + call["cache_read_tokens"]


def _call(usage: dict[str, Any], agent: Any, at: Any, line: int) -> dict[str, Any]:
    fresh = int(usage.get("input_tokens") or 0)
    created = int(usage.get("cache_creation_input_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    reported = [k for k in _USAGE_FIELDS if usage.get(k) is not None]  # a zero above may be an absent count
    known = not _PROMPT_FIELDS.isdisjoint(reported)  # no prompt count reported: the prompt is unknown, not 0
    return {
        "agent": agent,
        "line": line,  # trace line of the first block; dropped once the calls are numbered
        "at": at,  # the message's timestamp, as the trace has it; the first block's
        "prompt_tokens": fresh + created + read if known else None,
        "new_tokens": fresh + created if known else None,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "input_tokens": fresh,
        "cache_read_tokens": read,
        "cache_write_tokens": created,
        "reported": reported,  # which usage fields the record carried; the counts above fill absent ones with 0
    }


def _result_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(b.get("text", ""))) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return 0


def _content(rec: dict[str, Any]) -> list[dict[str, Any]]:
    content = (rec.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []

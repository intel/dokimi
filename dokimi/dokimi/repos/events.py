# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Harness traces -> the normalized ``events.jsonl`` of the repository contract (§4.1).

Graders and probes read only these events, never a harness-specific trace:

    {"t": 12.4, "kind": "command", "text": "bazel test //x", "rc": 0}
    {"t": 3.1,  "kind": "read",    "path": "AGENTS.md"}
    {"t": 5.0,  "kind": "edit",    "path": "src/foo.cpp"}
    {"t": 20.2, "kind": "tool",    "name": "WebFetch", "denied": true}
    {"t": 2.0,  "kind": "tool",    "name": "Skill", "skill": "lint-fix"}

``t`` is seconds since the first timestamped trace line, or ``null`` when the
format has no timestamps. Paths inside the workspace are made relative to it.
A command event carries ``output`` when the harness returned one: over
:data:`OUTPUT_LIMIT` bytes it keeps the first and last :data:`OUTPUT_KEEP`
bytes, and ``output_truncated`` says whether that happened. An event a
subagent made carries ``agent``: the id of the parent's tool call that started it.

Token counts cover the same scope as ``cost_usd``: summed over ``modelUsage``
(parent, subagents and helper models) when the result has it, else the parent's
``usage``; ``usage_scope`` says which (``tree`` or ``main``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from dokimi.context.trace import parse_trace

from .transcript import Transcript, _parse_time, clip_output, relative_path

TRACE_FORMATS = ("claude-stream-json", "codex-json", "copilot-json", "text")

_COMMAND_TOOLS = {"Bash", "PowerShell"}
_READ_TOOLS = {"Read"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
_AGENT_TOOLS = {"Agent", "Task"}
_HOOK_DENIAL = re.compile(r"\s*PreToolUse:\w+ hook error")


def convert(fmt: str, raw: str, workdir: str | None = None, cwd: str = "") -> Transcript:
    """Convert a raw harness stdout in format ``fmt`` to a :class:`Transcript`.

    ``cwd`` is the directory the harness started in, relative to ``workdir``: a shell command's relative
    paths (Codex, Copilot) are read from there."""
    if fmt == "text":
        return Transcript(answer=raw.strip())
    if fmt == "claude-stream-json":
        return _claude_stream_json(raw, workdir)
    if fmt in ("codex-json", "copilot-json"):
        from .cli_traces import codex_json, copilot_json

        return (codex_json if fmt == "codex-json" else copilot_json)(raw, workdir, cwd)
    raise ValueError(f"unknown trace format {fmt!r} (known: {', '.join(TRACE_FORMATS)})")


def _claude_stream_json(raw: str, workdir: str | None) -> Transcript:
    out = Transcript()
    start: datetime | None = None
    by_id: dict[str, dict[str, Any]] = {}  # tool_use id -> its event
    for n, line in enumerate(raw.split("\n"), 1):  # not splitlines: a JSON string may hold U+2028 or U+0085
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.dropped_lines += 1
            out.errors.append(f"line {n}: not JSON")
            continue
        if not isinstance(rec, dict):
            out.dropped_lines += 1
            continue
        t = None
        stamp = _parse_time(rec.get("timestamp"))
        if stamp is not None:
            start = start or stamp
            t = round((stamp - start).total_seconds(), 3)
        try:
            _apply(out, rec, t, by_id, workdir)
        except (AttributeError, TypeError) as exc:  # valid JSON of an unexpected shape
            out.dropped_lines += 1
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    main = parse_trace(raw).main_calls()
    out.first_prompt_tokens = main[0]["prompt_tokens"] if main else None
    return out


def _apply_result(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    """Attach one tool_result to the event of its tool_use."""
    result_of = by_id.get(block.get("tool_use_id", ""))
    if result_of is None:
        return
    output = _result_text(block.get("content"))
    # A PreToolUse hook that refuses the call (exit 2) comes back as an error result
    # naming the hook; the command never ran, so it has no rc.
    denied = bool(block.get("is_error")) and bool(_HOOK_DENIAL.match(output or ""))
    if denied:
        result_of["denied"] = True
    if result_of["kind"] == "read" and not denied:
        # A read that failed (the file does not exist) shows nothing of the file.
        result_of["rc"] = 1 if block.get("is_error") else 0
    if result_of["kind"] == "edit" and not denied:
        # A failed edit (old_string not found) left the file as it was: rc 1, so it is no useful result.
        result_of["rc"] = 1 if block.get("is_error") else 0
    if result_of["kind"] == "command":
        # Claude Code reports success or failure only, with stdout and stderr merged into one text.
        if not denied:
            result_of["rc"] = 1 if block.get("is_error") else 0
        if output is not None:
            result_of["output"], result_of["output_truncated"] = clip_output(output)


def _set_num_turns(out: Transcript, turns: Any) -> None:
    out.num_turns = turns if isinstance(turns, int) and not isinstance(turns, bool) else None
    if out.num_turns is None and turns is not None:
        out.errors.append(f"result num_turns {str(turns)[:50]!r} is not an integer; recorded as null")


def _apply(out: Transcript, rec: dict[str, Any], t: float | None,
           by_id: dict[str, dict[str, Any]], workdir: str | None) -> None:
    kind = rec.get("type")
    if kind == "system" and rec.get("subtype") == "init":
        out.harness_version = rec.get("claude_code_version")
        out.init_model = str(rec["model"]) if rec.get("model") else None
        # Claude Code loads AGENTS.md through a built-in plugin (cc-plugin-agents-md) on some versions
        out.plugins = sorted(str(p.get("name")) for p in rec.get("plugins") or []
                             if isinstance(p, dict) and p.get("name"))
    elif kind == "assistant":
        for block in _content(rec, out):
            if block.get("type") == "tool_use":
                event = _tool_event(block, t, workdir)
                if rec.get("parent_tool_use_id"):
                    event["agent"] = str(rec["parent_tool_use_id"])
                if block.get("name") in _AGENT_TOOLS:
                    out.subagent_calls = (out.subagent_calls or 0) + 1
                out.events.append(event)
                if block.get("id"):
                    by_id[block["id"]] = event
        model = (rec.get("message") or {}).get("model")
        # A subagent's model is its definition's, not the run's; "<synthetic>" marks a reply Claude Code wrote itself.
        if model and model != "<synthetic>" and rec.get("parent_tool_use_id") is None:
            out.answered_by.add(str(model))
    elif kind == "user":
        for block in _content(rec, out):
            if block.get("type") == "tool_result":
                _apply_result(block, by_id)
    elif kind == "result":
        out.answer = str(rec.get("result") or "").strip()
        out.cost_usd = rec.get("total_cost_usd")
        out.result_seen = True
        _set_num_turns(out, rec.get("num_turns"))
        out.is_error = bool(rec.get("is_error"))
        out.result_subtype = rec.get("subtype")
        out.subagent_calls = out.subagent_calls or 0
        if not isinstance(rec.get("usage") or {}, dict):
            raise TypeError("usage is not an object")
        from dokimi.harnesses.claude_json import token_counts
        counts = token_counts(rec)
        if counts is not None:
            out.usage_scope, (out.input_tokens, out.output_tokens, out.cache_read_tokens,
                              out.cache_write_tokens) = counts
        for denial in rec.get("permission_denials") or []:
            if isinstance(denial, dict) and denial.get("tool_use_id") in by_id:
                by_id[denial["tool_use_id"]]["denied"] = True


def _tool_event(block: dict[str, Any], t: float | None, workdir: str | None) -> dict[str, Any]:
    name = str(block.get("name") or "")
    raw_args = block.get("input")
    args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
    event: dict[str, Any] = {"t": t}
    if name in _COMMAND_TOOLS:
        event.update(kind="command", text=str(args.get("command", "")), rc=None)
    elif name in _READ_TOOLS | _EDIT_TOOLS:
        path = args.get("file_path") or args.get("notebook_path") or ""
        event.update(kind="read" if name in _READ_TOOLS else "edit", path=relative_path(str(path), workdir))
    else:
        event.update(kind="tool", name=name)
        if name == "Skill":  # Claude Code names the skill in `skill`; older versions in `command`
            event["skill"] = str(args.get("skill") or args.get("command") or "")
    return event


def _result_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def _content(rec: dict[str, Any], out: Transcript) -> list[dict[str, Any]]:
    """The object blocks of a message; a record with a block or a content of another shape counts as dropped."""
    content = (rec.get("message") or {}).get("content")
    if content is None or isinstance(content, str):  # a user's prompt is a plain string: no block, nothing lost
        return []
    if not isinstance(content, list):
        out.dropped_lines += 1
        return []
    blocks = [b for b in content if isinstance(b, dict)]
    if len(blocks) != len(content):
        out.dropped_lines += 1
    return blocks

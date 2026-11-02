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

A shell command that only prints files (:func:`~dokimi.repos.cli_traces.shell_reads`) also gives a
``read`` event per file, before its ``command`` event: a Codex or Copilot shell command, and
Claude Code's ``Bash`` tool. Claude Code keeps the shell's directory from one ``Bash`` call to the
next, so once any call ran a ``cd`` in that shell (not in a subshell, a pipeline or a background
job), later relative reads are not recorded, the parent's and every subagent's alike: subagents may
share the parent's shell, and dropping a read is the safe side either way. A refused call ran nothing:
it moves no shell, and its reads stay, ``denied``. The reads are settled when the trace ends,
since a ``permission_denials`` entry comes only in the final result.

Token counts cover the same scope as ``cost_usd``: summed over ``modelUsage``
(parent, subagents and helper models) when the result has it, else the parent's
``usage``; ``usage_scope`` says which (``tree`` or ``main``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from dokimi.context.trace import parse_trace

from .cli_traces import _failed_read, codex_json, copilot_json, shell_reads_moved
from .transcript import Transcript, _parse_time, clip_output, relative_path

TRACE_FORMATS = ("claude-stream-json", "codex-json", "copilot-json", "text")

_SHELL_TOOL = "Bash"  # a POSIX shell: its command is parsed for the files it prints (not PowerShell's)
_COMMAND_TOOLS = {_SHELL_TOOL, "PowerShell"}
_READ_TOOLS = {"Read"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
_AGENT_TOOLS = {"Agent", "Task"}
_HOOK_DENIAL = re.compile(r"\s*PreToolUse:\w+ hook error")


def convert(fmt: str, raw: str, workdir: str | None = None, cwd: str = "") -> Transcript:
    """Convert a raw harness stdout in format ``fmt`` to a :class:`Transcript`.

    ``cwd`` is the directory the harness started in, relative to ``workdir``: a shell command's relative
    paths are read from there (in Claude Code, the first ``Bash`` call starts there)."""
    if fmt == "text":
        return Transcript(answer=raw.strip())
    if fmt == "claude-stream-json":
        return _claude_stream_json(raw, workdir, cwd)
    if fmt in ("codex-json", "copilot-json"):
        return (codex_json if fmt == "codex-json" else copilot_json)(raw, workdir, cwd)
    raise ValueError(f"unknown trace format {fmt!r} (known: {', '.join(TRACE_FORMATS)})")


@dataclass
class _BashCall:
    """A Bash call, settled when the trace ends: what came back for it decides its reads."""

    text: str
    named: list[str]  # the files its command prints, from the start directory
    reads: list[dict[str, Any]]  # their read events, in place before the command event
    moves: bool  # the command may leave its shell in another directory
    result: str | None = None  # "ok", "error" or "denied"; None while nothing came back


@dataclass
class _Stream:
    """What a Claude stream-json trace carries from line to line."""

    workdir: str | None
    cwd: str  # where the Bash shell starts, relative to workdir
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)  # tool_use id -> its event
    bash: dict[int, _BashCall] = field(default_factory=dict)  # id() of a Bash call's command event -> the call


def _claude_stream_json(raw: str, workdir: str | None, cwd: str = "") -> Transcript:
    out = Transcript()
    start: datetime | None = None
    stream = _Stream(workdir, cwd)
    for n, line in enumerate(raw.split("\n"), 1):  # not splitlines: a JSON string may hold U+2028 or U+0085
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.errors.append(f"line {n}: not JSON")
            continue
        if not isinstance(rec, dict):
            continue
        t = None
        stamp = _parse_time(rec.get("timestamp"))
        if stamp is not None:
            start = start or stamp
            t = round((stamp - start).total_seconds(), 3)
        try:
            _apply(out, rec, t, stream)
        except (AttributeError, TypeError) as exc:  # valid JSON of an unexpected shape
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    _settle_bash(out, stream.bash.values())
    main = parse_trace(raw).main_calls()
    out.first_prompt_tokens = main[0]["prompt_tokens"] if main else None
    return out


def _apply_result(out: Transcript, block: dict[str, Any], stream: _Stream) -> None:
    """Attach one tool_result to the event of its tool_use."""
    tool_id = block.get("tool_use_id", "")
    result_of = stream.by_id.get(tool_id)
    if result_of is None:
        return
    output = _result_text(block.get("content"))
    # A PreToolUse hook that refuses the call (exit 2) comes back as an error result
    # naming the hook; the command never ran, so it has no rc.
    denied = bool(block.get("is_error")) and bool(_HOOK_DENIAL.match(output or ""))
    if denied:
        _deny(stream, tool_id)
    elif id(result_of) in stream.bash:
        stream.bash[id(result_of)].result = "error" if block.get("is_error") else "ok"
    if result_of["kind"] == "read" and not denied:
        # A read that failed (the file does not exist) shows nothing of the file.
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


def _deny(stream: _Stream, tool_id: Any) -> None:
    """A refused call (a hook's result or a final ``permission_denials`` entry): it never ran."""
    event = stream.by_id[tool_id]
    event["denied"] = True
    if id(event) in stream.bash:
        stream.bash[id(event)].result = "denied"


def _settle_bash(out: Transcript, calls: Iterable[_BashCall]) -> None:
    """Each Bash call's reads, once every result and denial is in. In the trace's order:

    - a denied call ran nothing: it moves no shell, and its reads stay, ``denied``;
    - a call that failed and was one read of one file (``cat AGENTS.md`` of a missing file) drops its reads;
    - after a call that was not denied and may have moved its shell, only absolute reads are known, in
      every agent: a subagent may share the parent's shell;
    - else the reads get ``rc`` 0, since a failure does not say which file failed (``cat a missing``).
    """
    moved = False  # the shell may have left its start directory
    dropped: set[int] = set()
    for call in calls:
        failed = call.result == "error" and _failed_read(call.text, call.named, 1)
        for read, path in zip(call.reads, call.named, strict=True):
            if failed or (moved and not path.startswith("/")):
                dropped.add(id(read))
            elif call.result == "denied":
                read["denied"] = True
            elif call.result:
                read["rc"] = 0
        moved |= call.moves and call.result != "denied"
    if dropped:
        out.events[:] = [e for e in out.events if id(e) not in dropped]


def _tool_use(out: Transcript, block: dict[str, Any], t: float | None, agent: str, stream: _Stream) -> None:
    """The events of one tool_use block: a Bash call's derived reads, then its own event."""
    event = _tool_event(block, t, stream.workdir)
    reads = _bash_call(event, stream).reads if block.get("name") == _SHELL_TOOL else []
    if agent:
        for e in (*reads, event):
            e["agent"] = agent
    if block.get("name") in _AGENT_TOOLS:
        out.subagent_calls = (out.subagent_calls or 0) + 1
    out.events.extend([*reads, event])
    if block.get("id"):
        stream.by_id[block["id"]] = event


def _bash_call(event: dict[str, Any], stream: _Stream) -> _BashCall:
    """A Bash command's call, with a read event for each file it names; :func:`_settle_bash` keeps or drops them."""
    named, moves = shell_reads_moved(str(event["text"]), stream.cwd)
    reads = [{"t": event["t"], "kind": "read", "path": relative_path(p, stream.workdir)} for p in named]
    stream.bash[id(event)] = call = _BashCall(str(event["text"]), named, reads, moves)
    return call


def _apply(out: Transcript, rec: dict[str, Any], t: float | None, stream: _Stream) -> None:
    kind = rec.get("type")
    if kind == "system" and rec.get("subtype") == "init":
        out.harness_version = rec.get("claude_code_version")
        out.init_model = str(rec["model"]) if rec.get("model") else None
        # Claude Code loads AGENTS.md through a built-in plugin (cc-plugin-agents-md) on some versions
        out.plugins = sorted(str(p.get("name")) for p in rec.get("plugins") or []
                             if isinstance(p, dict) and p.get("name"))
    elif kind == "assistant":
        for block in _content(rec):
            if block.get("type") == "tool_use":
                _tool_use(out, block, t, str(rec.get("parent_tool_use_id") or ""), stream)
        model = (rec.get("message") or {}).get("model")
        # A subagent's model is its definition's, not the run's; "<synthetic>" marks a reply Claude Code wrote itself.
        if model and model != "<synthetic>" and rec.get("parent_tool_use_id") is None:
            out.answered_by.add(str(model))
    elif kind == "user":
        for block in _content(rec):
            if block.get("type") == "tool_result":
                _apply_result(out, block, stream)
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
            if isinstance(denial, dict) and denial.get("tool_use_id") in stream.by_id:
                _deny(stream, denial["tool_use_id"])


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


def _content(rec: dict[str, Any]) -> list[dict[str, Any]]:
    content = (rec.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []

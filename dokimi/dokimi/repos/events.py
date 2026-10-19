# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Harness traces -> the normalized ``events.jsonl`` of the repository contract (§4.1).

Graders and probes read only these events, never a harness-specific trace:

    {"t": 12.4, "kind": "command", "text": "bazel test //x", "rc": 0}
    {"t": 3.1,  "kind": "read",    "path": "AGENTS.md"}
    {"t": 5.0,  "kind": "edit",    "path": "src/foo.cpp"}
    {"t": 20.2, "kind": "tool",    "name": "WebFetch", "denied": true}

``t`` is seconds since the first timestamped trace line, or ``null`` when the
format has no timestamps. Paths inside the workspace are made relative to it.
A command event carries ``output`` when the harness returned one: over
:data:`OUTPUT_LIMIT` bytes it keeps the first and last :data:`OUTPUT_KEEP`
bytes, and ``output_truncated`` says whether that happened.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

TRACE_FORMATS = ("claude-stream-json", "codex-json", "copilot-json", "text")
OUTPUT_LIMIT = 8192
OUTPUT_KEEP = 4096

_COMMAND_TOOLS = {"Bash", "PowerShell"}
_READ_TOOLS = {"Read"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
_HOOK_DENIAL = re.compile(r"\s*PreToolUse:\w+ hook error")


@dataclass
class Transcript:
    """What one harness run produced, in harness-independent form."""

    answer: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    is_error: bool = False
    harness_version: str | None = None
    result_subtype: str | None = None
    plugins: list[str] = field(default_factory=list)  # loaders the harness reported at start-up
    errors: list[str] = field(default_factory=list)

    def reads(self) -> set[str]:
        return {e["path"] for e in self.events if e["kind"] == "read" and e.get("path")}

    def events_jsonl(self) -> str:
        return "".join(json.dumps(e, sort_keys=True) + "\n" for e in self.events)


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
    for n, line in enumerate(raw.splitlines(), 1):
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
            _apply(out, rec, t, by_id, workdir)
        except (AttributeError, TypeError) as exc:  # valid JSON of an unexpected shape
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    return out


def _apply(out: Transcript, rec: dict[str, Any], t: float | None,
           by_id: dict[str, dict[str, Any]], workdir: str | None) -> None:
    kind = rec.get("type")
    if kind == "system" and rec.get("subtype") == "init":
        out.harness_version = rec.get("claude_code_version")
        # Claude Code loads AGENTS.md through a built-in plugin (cc-plugin-agents-md) on some versions
        out.plugins = sorted(str(p.get("name")) for p in rec.get("plugins") or []
                             if isinstance(p, dict) and p.get("name"))
    elif kind == "assistant":
        for block in _content(rec):
            if block.get("type") == "tool_use":
                event = _tool_event(block, t, workdir)
                out.events.append(event)
                if block.get("id"):
                    by_id[block["id"]] = event
    elif kind == "user":
        for block in _content(rec):
            if block.get("type") == "tool_result":
                result_of = by_id.get(block.get("tool_use_id", ""))
                if result_of is None:
                    continue
                output = _result_text(block.get("content"))
                # A PreToolUse hook that refuses the call (exit 2) comes back as an error result
                # naming the hook; the command never ran, so it has no rc.
                denied = bool(block.get("is_error")) and bool(_HOOK_DENIAL.match(output or ""))
                if denied:
                    result_of["denied"] = True
                if result_of["kind"] == "command":
                    # Claude Code reports success or failure only, with stdout
                    # and stderr merged into one text.
                    if not denied:
                        result_of["rc"] = 1 if block.get("is_error") else 0
                    if output is not None:
                        result_of["output"], result_of["output_truncated"] = clip_output(output)
    elif kind == "result":
        out.answer = str(rec.get("result") or "").strip()
        out.cost_usd = rec.get("total_cost_usd")
        out.num_turns = rec.get("num_turns")
        out.is_error = bool(rec.get("is_error"))
        out.result_subtype = rec.get("subtype")
        usage = rec.get("usage") or {}
        out.input_tokens = usage.get("input_tokens")
        out.output_tokens = usage.get("output_tokens")
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
    return event


def clip_output(text: str) -> tuple[str, bool]:
    """``text`` kept whole up to :data:`OUTPUT_LIMIT` UTF-8 bytes, else its head and tail."""
    data = text.encode("utf-8")
    if len(data) <= OUTPUT_LIMIT:
        return text, False
    head = data[:OUTPUT_KEEP].decode("utf-8", "ignore")
    tail = data[-OUTPUT_KEEP:].decode("utf-8", "ignore")
    # A character cut at either end is dropped whole, so count what was really left out.
    omitted = len(data) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n[... {omitted} bytes omitted ...]\n{tail}", True


def _result_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def relative_path(path: str, workdir: str | None) -> str:
    """``path`` relative to ``workdir`` in POSIX form; POSIX and Windows paths both work."""
    if not workdir:
        return path
    for flavour in (PurePosixPath, PureWindowsPath):
        target, base = flavour(path), flavour(workdir)
        if target.is_absolute() and target.is_relative_to(base):
            return target.relative_to(base).as_posix()
    return path


def _content(rec: dict[str, Any]) -> list[dict[str, Any]]:
    content = (rec.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

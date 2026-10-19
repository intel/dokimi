# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI and Copilot CLI JSON traces -> :class:`~dokimi.repos.events.Transcript` (contract §4.1).

``codex-json`` is the stdout of ``codex exec --json`` (codex-cli 0.160): one
``item.*`` event per agent message, shell command, file change, MCP call or web
search, and ``turn.completed`` with token usage. Codex has no read tool, so a
``read`` event is derived from a shell command that only prints files
(``cat``, ``head``, ``tail``, ``sed -n``, ``nl``) and names them.

``copilot-json`` is the stdout of ``copilot -p --output-format json`` (Copilot
CLI 1.0.91): ``tool.execution_start`` / ``tool.execution_complete`` pairs,
``assistant.message`` and a final ``result``. It reports no tokens and no cost.

Neither CLI reports a cost, so ``cost_usd`` stays ``None`` and a batch budget
counts the cell's cap.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from .events import Transcript, _parse_time, _relative, clip_output

_BASH_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -lc (.+)$", re.S)
_PRINTERS = {"cat", "head", "tail", "nl", "sed"}
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n)\s*")
_COPILOT_SHELL = {"bash", "powershell"}
_COPILOT_READ = {"view"}
_COPILOT_EDIT = {"edit", "create", "str_replace_editor", "write"}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)


def unwrap_shell(command: str) -> str:
    """The script of ``/usr/bin/bash -lc '<script>'`` (how Codex reports a command), else ``command``."""
    m = _BASH_WRAP.match(command.strip())
    if not m:
        return command
    try:
        parts = shlex.split(m.group(1))
    except ValueError:
        return command
    return parts[0] if len(parts) == 1 else command


def shell_reads(script: str) -> list[str]:
    """Files a command only prints: ``cat a b``, ``head -n 5 a``, ``sed -n '1,80p' a``, ``nl -ba a``.

    A pipeline or a redirect is not a read of its arguments (``cat a > b`` writes b), so it gives nothing;
    each ``&&`` / ``;`` part is read on its own. Working directory changes via ``cd`` are tracked
    to resolve subsequent reads to the correct path.
    """
    out: list[str] = []
    # Split only on unquoted shell operators to preserve quoted arguments.
    parts = _split_at_unquoted_operators(script)
    cwd = ""  # current working directory relative to script root
    for part in parts:
        if not part or re.search(r"[|<>`$]", part):
            continue
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        if not words:
            continue
        # Track directory changes from cd commands
        if words[0] == "cd" and len(words) > 1:
            cwd = words[1]
            continue
        if words[0] not in _PRINTERS:
            continue
        args = words[1:]
        if words[0] == "sed":
            if "-n" not in args or "-i" in args:
                continue
            args = [a for a in args if a != "-n"][1:]  # the first non-flag is the script
        files, skip = [], False
        for a in args:
            if skip:
                skip = False
            elif a in ("-n", "-c") and words[0] in ("head", "tail"):
                skip = True
            elif not a.startswith("-"):
                files.append(a)
        # Apply cwd prefix to each file, if cwd is known
        if cwd:
            files = [f"{cwd}/{f}" if not f.startswith("/") else f for f in files]
        out.extend(files)
    return out


def _split_at_unquoted_operators(script: str) -> list[str]:
    """Split script on unquoted &&, ||, ;, and newlines only."""
    parts = []
    current = []
    i = 0
    in_single = False
    in_double = False
    while i < len(script):
        c = script[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
        elif c == "\\" and (in_single or in_double):
            current.append(c)
            if i + 1 < len(script):
                i += 1
                current.append(script[i])
            i += 1
        elif not in_single and not in_double and i + 1 < len(script) and script[i:i+2] == "&&":
            if current:
                parts.append("".join(current).strip())
            current = []
            i += 2
        elif not in_single and not in_double and i + 1 < len(script) and script[i:i+2] == "||":
            if current:
                parts.append("".join(current).strip())
            current = []
            i += 2
        elif not in_single and not in_double and c == ";":
            if current:
                parts.append("".join(current).strip())
            current = []
            i += 1
        elif not in_single and not in_double and c == "\n":
            if current:
                parts.append("".join(current).strip())
            current = []
            i += 1
        else:
            current.append(c)
            i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def codex_json(raw: str, workdir: str | None) -> Transcript:
    out = Transcript()
    messages: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}  # Track started items by ID to update on completion
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
        try:
            _codex_apply(out, rec, messages, workdir, by_id)
        except (AttributeError, TypeError, ValueError) as exc:
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    out.answer = messages[-1].strip() if messages else ""
    return out


def _codex_apply(out: Transcript, rec: dict[str, Any], messages: list[str], workdir: str | None,
                  by_id: dict[str, dict[str, Any]] | None = None) -> None:
    if by_id is None:
        by_id = {}
    kind = rec.get("type")
    if kind == "turn.completed":
        usage = rec.get("usage") or {}
        out.num_turns = (out.num_turns or 0) + 1
        out.input_tokens = (out.input_tokens or 0) + int(usage.get("input_tokens") or 0)
        out.output_tokens = (out.output_tokens or 0) + int(usage.get("output_tokens") or 0)
    elif kind == "turn.failed" or (kind == "error" and not str(rec.get("message", "")).startswith("Reconnecting")):
        out.is_error = kind == "turn.failed"
        msg = rec.get("message") or (rec.get("error") or {}).get("message")
        out.errors.append(f"{kind}: {str(msg)[:200]}")
    elif kind == "item.started":
        # Record started items to preserve them even if completion is missing (timeout case)
        item = rec.get("item") or {}
        itype = item.get("type")
        if itype == "command_execution":
            script = unwrap_shell(str(item.get("command") or ""))
            # Use command text as key to correlate with item.completed
            item_key = ("command", script)
            for path in shell_reads(script):
                out.events.append({"t": None, "kind": "read", "path": _relative(path, workdir)})
            event: dict[str, Any] = {"t": None, "kind": "command", "text": script, "rc": None}
            by_id[item_key] = event
            out.events.append(event)
    elif kind == "item.completed":
        item = rec.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            messages.append(str(item.get("text") or ""))
        elif itype == "command_execution":
            script = unwrap_shell(str(item.get("command") or ""))
            # Use command text as key to correlate with item.started
            item_key = ("command", script)
            if item_key in by_id:
                # Update the event from item.started with completion data
                event = by_id[item_key]
                event["rc"] = item.get("exit_code")
                if item.get("aggregated_output"):
                    event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
                del by_id[item_key]
            else:
                # Fallback for item.completed without item.started
                for path in shell_reads(script):
                    out.events.append({"t": None, "kind": "read", "path": _relative(path, workdir)})
                event: dict[str, Any] = {"t": None, "kind": "command", "text": script, "rc": item.get("exit_code")}
                if item.get("aggregated_output"):
                    event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
                out.events.append(event)
        elif itype == "file_change":
            for change in item.get("changes") or []:
                if isinstance(change, dict) and change.get("path"):
                    out.events.append({"t": None, "kind": "edit", "path": _relative(str(change["path"]), workdir)})
        elif itype in ("mcp_tool_call", "web_search"):
            name = f"{item.get('server')}.{item.get('tool')}" if itype == "mcp_tool_call" else "web_search"
            out.events.append({"t": None, "kind": "tool", "name": name})


def copilot_json(raw: str, workdir: str | None) -> Transcript:
    out = Transcript()
    start = None
    by_id: dict[str, list[dict[str, Any]]] = {}
    final, last = "", ""
    turns = 0
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
        kind, data = rec.get("type"), rec.get("data") or {}
        try:
            if kind == "assistant.turn_start":
                turns += 1
            elif kind == "assistant.message":
                text = str(data.get("content") or "")
                if text.strip():
                    last = text
                    if data.get("phase") == "final_answer":
                        final = text
            elif kind == "tool.execution_start":
                events = _copilot_tool(data, t, workdir)
                out.events.extend(events)
                by_id[str(data.get("toolCallId"))] = events
            elif kind == "tool.execution_complete":
                _copilot_result(by_id.get(str(data.get("toolCallId")), []), data)
            elif kind == "result":
                out.is_error = rec.get("exitCode") not in (0, None)
        except (AttributeError, TypeError, ValueError) as exc:
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    out.answer = (final or last).strip()
    out.num_turns = turns or None
    return out


def _copilot_tool(data: dict[str, Any], t: float | None, workdir: str | None) -> list[dict[str, Any]]:
    name = str(data.get("toolName") or "")
    args = data.get("arguments")
    if name in _COPILOT_SHELL:
        command = str((args or {}).get("command", "")) if isinstance(args, dict) else ""
        reads = [{"t": t, "kind": "read", "path": _relative(p, workdir)} for p in shell_reads(command)]
        return [*reads, {"t": t, "kind": "command", "text": command, "rc": None}]
    if name in _COPILOT_READ | _COPILOT_EDIT and isinstance(args, dict):
        path = str(args.get("path") or args.get("file_path") or "")
        return [{"t": t, "kind": "read" if name in _COPILOT_READ else "edit", "path": _relative(path, workdir)}]
    if name == "apply_patch":
        text = args if isinstance(args, str) else str((args or {}).get("input", ""))
        return [{"t": t, "kind": "edit", "path": _relative(p.strip(), workdir)} for p in _PATCH_FILE.findall(text)]
    return [{"t": t, "kind": "tool", "name": name}]


def _copilot_result(events: list[dict[str, Any]], data: dict[str, Any]) -> None:
    for event in events:
        if event["kind"] != "command":
            continue
        shell = data.get("shellExecution") or {}
        code = shell.get("exitCode") if isinstance(shell, dict) else None
        event["rc"] = code if isinstance(code, int) else (None if data.get("success") is None
                                                          else 0 if data.get("success") else 1)
        result = data.get("result") or {}
        output = result.get("content") if isinstance(result, dict) else None
        if isinstance(output, str) and output:
            event["output"], event["output_truncated"] = clip_output(output)

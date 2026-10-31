# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Claude Code session log (``~/.claude/projects/<project>/<session>.jsonl``) -> prompt and tool events.

One JSON object per line. A prompt the user typed is a ``user`` record with text content.
A tool call is a ``tool_use`` block of an ``assistant`` record; its ``tool_result`` comes back
in a ``user`` record this reader skips. Records Claude Code injects itself (``isMeta``, such as
CLAUDE.md echoed into the context) are not prompts. A subagent's brief (a ``user`` record with
``isSidechain``) is the parent agent's words, not the user's, so it is not a prompt either; the
subagent's own tool calls still count as the agent acting.

Tool inputs are kept as text for matching, except the bodies of an edit or a write, which would
echo file content into the match. Nothing here calls a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Input keys whose value is file content, not the agent's intent: left out of an event's text.
_CONTENT_KEYS = frozenset({"old_string", "new_string", "content", "new_source", "edits"})
_PATH_KEYS = ("file_path", "path", "notebook_path")
# Markup a harness wraps around text it injects into a user turn; not the user's words.
_INJECTED = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)
_ERROR_LIMIT = 20


class SessionError(ValueError):
    """The file is not a session log this module can read."""


@dataclass(frozen=True)
class Event:
    """One thing that happened in a session, in file order."""

    kind: str  # "prompt": the user typed it; "tool": the agent called a tool
    text: str  # a prompt's text, or a tool call's string inputs (no file bodies)
    command: str = ""  # a Bash call's command line
    paths: tuple[str, ...] = ()  # file paths a tool call named


@dataclass
class Session:
    """The events of one session log, plus the lines that could not be read."""

    name: str
    events: list[Event] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def read_session(path: Path) -> Session:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise SessionError(f"cannot read {path}: {exc}") from exc
    session = parse_session(raw, str(path))
    if not session.events:
        raise SessionError(f"{path}: no prompt and no tool call; is it a Claude Code session log?")
    return session


def parse_session(raw: str, name: str = "") -> Session:
    session = Session(name=name)
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            _error(session, f"line {n}: not JSON")
            continue
        if isinstance(rec, dict):
            session.events.extend(_events_of(rec))
    return session


def _error(session: Session, message: str) -> None:
    if len(session.errors) < _ERROR_LIMIT:
        session.errors.append(message)


def _events_of(rec: dict[str, Any]) -> list[Event]:
    if rec.get("isMeta"):
        return []
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if rec.get("type") == "user":
        if rec.get("isSidechain"):
            return []
        text = _INJECTED.sub(" ", _text_of(content)).strip()
        return [Event("prompt", text)] if text else []
    if rec.get("type") == "assistant" and isinstance(content, list):
        return [_tool_event(b.get("input")) for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"]
    return []


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def _tool_event(tool_input: Any) -> Event:
    args = tool_input if isinstance(tool_input, dict) else {}
    strings = [v for k, v in args.items() if k not in _CONTENT_KEYS and isinstance(v, str)]
    command = args.get("command") if isinstance(args.get("command"), str) else ""
    paths = tuple(args[k] for k in _PATH_KEYS if isinstance(args.get(k), str))
    return Event("tool", " ".join(strings), command=command, paths=paths)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Claude Code session log (``~/.claude/projects/<project>/<session>.jsonl``) -> prompt and tool events.

One JSON object per line. A prompt the user typed is a ``user`` record with text content.
A tool call is a ``tool_use`` block of an ``assistant`` record; its ``tool_result`` comes back
in a ``user`` record this reader skips. Records Claude Code injects itself (``isMeta``, such as
CLAUDE.md echoed into the context) are not prompts, nor is the summary Claude Code writes after a
compaction (``isCompactSummary``). A subagent's brief (a ``user`` record with
``isSidechain``) is the parent agent's words, not the user's, so it is not a prompt either; the
subagent's own tool calls still count as the agent acting. Claude Code writes a subagent's records to
``<session>/subagents/*.jsonl`` next to ``<session>.jsonl``; ``read_session_tree`` folds them into that session.

Tool inputs are kept as text for matching, except the bodies of an edit or a write, which would
echo file content into the match, and a ``prompt`` input (the brief a Task call sends a subagent),
which is the parent agent's words too. Nothing here calls a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Input keys whose value is file content or a model-facing brief, not the agent's intent: left out of an event's text.
_CONTENT_KEYS = frozenset({"old_string", "new_string", "content", "new_source", "edits",
                           "file_text", "old_str", "new_str"})  # the last three: Goose's editor tools
# a subagent call's ``prompt`` is the brief the subagent gets, not the agent's intent; other tools' ``prompt`` is input
_SUBAGENT_TOOLS = frozenset({"Task", "Agent"})
_PATH_KEYS = ("file_path", "path", "notebook_path")
# Markup a harness wraps around text it injects into a user turn; not the user's words.
# ``bash-stdout`` and ``bash-stderr`` hold what a ``!`` shell turn printed: output, not the user's words.
_INJECTED = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|bash-stdout|bash-stderr)>.*?</\1>",
                       re.S)
# the record Claude Code writes when the user stops a turn or a tool call: the harness's words, not a prompt
_INTERRUPTED = re.compile(r"\[Request interrupted by user(?: for tool use)?\]")
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
    timestamp: str = field(default="", compare=False)  # the record's ISO time, when it has one
    uuid: str = field(default="", compare=False)  # the record's id: a resumed log repeats it


@dataclass
class Session:
    """The events of one session log, plus the lines that could not be read."""

    name: str
    events: list[Event] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sidechain_only: bool = False  # every prompt and tool record is a subagent's: a transcript without its session


def read_session(path: Path) -> Session:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise SessionError(f"cannot read {path}: {exc}") from exc
    session = parse_session(raw, str(path))
    if not session.events:
        raise SessionError(f"{path}: no prompt and no tool call; is it a Claude Code session log?")
    return session


def subagent_files(path: Path) -> list[Path]:
    """The subagent transcripts Claude Code wrote for the session log ``path``."""
    return sorted((path.parent / path.stem / "subagents").glob("*.jsonl")) if path.suffix == ".jsonl" else []


def read_session_tree(path: Path) -> Session:
    """A session log with its subagents' transcripts folded in, in time order: their tool calls are the agent acting."""
    session = read_session(path)
    for sub in subagent_files(path):
        try:
            session.events.extend(read_session(sub).events)
        except SessionError as exc:
            add_error(session, str(exc))
    session.events[:] = in_time_order(session.events)
    return session


def in_time_order(events: list[Event]) -> list[Event]:
    """The events sorted by time when every one has a timestamp, else in the order given."""
    return sorted(events, key=lambda e: e.timestamp) if all(e.timestamp for e in events) else events


def merge_repeats(sessions: list[Session]) -> tuple[list[Session], list[str]]:
    """Logs that share a record ``uuid`` as one session, each record once: a resumed or forked log repeats the records
    of the session it continues. Also a note per log merged into an earlier one."""
    root = list(range(len(sessions)))
    owner: dict[str, int] = {}
    for i, session in enumerate(sessions):
        for uuid in {e.uuid for e in session.events if e.uuid}:
            a, b = _root(root, owner.setdefault(uuid, i)), _root(root, i)
            root[max(a, b)] = min(a, b)
    groups: dict[int, list[Session]] = {}
    for i, session in enumerate(sessions):
        groups.setdefault(_root(root, i), []).append(session)
    merged, notes = [], []
    for first, *rest in groups.values():
        events, seen = list(first.events), {e.uuid for e in first.events}
        for session in rest:
            events += [e for e in session.events if not e.uuid or e.uuid not in seen]
            seen |= {e.uuid for e in session.events}
            notes.append(f"{session.name}: repeats records of {first.name} (a resumed or forked session); read as one")
        merged.append(Session(first.name, in_time_order(events)) if rest else first)
    return merged, notes


def _root(root: list[int], i: int) -> int:
    while root[i] != i:
        i = root[i]
    return i


def parse_session(raw: str, name: str = "") -> Session:
    session = Session(name=name)
    turns = sidechain = 0
    for n, line in enumerate(raw.split("\n"), 1):  # not splitlines(): it breaks at a raw U+2028
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):  # JSONDecodeError, an over-long integer, deep nesting
            add_error(session, f"line {n}: not JSON")
            continue
        if not isinstance(rec, dict):
            add_error(session, f"line {n}: not a JSON object")
            continue
        problem = _shape_problem(rec)
        if problem:
            add_error(session, f"line {n}: {problem}")
            continue
        if rec.get("type") in ("user", "assistant"):
            turns += 1
            sidechain += bool(rec.get("isSidechain"))
        stamp, uuid = (v if isinstance(v, str) else "" for v in (rec.get("timestamp"), rec.get("uuid")))
        session.events.extend(replace(e, timestamp=stamp, uuid=uuid) if stamp or uuid else e for e in _events_of(rec))
    session.sidechain_only = turns > 0 and sidechain == turns
    return session


def prompt_text(raw: str) -> str:
    """The user's own words: harness markup removed, edges trimmed. An interruption marker alone is no words."""
    text = _INJECTED.sub(" ", raw).strip()
    return "" if _INTERRUPTED.fullmatch(text) else text


def add_error(session: Session, message: str) -> None:
    if len(session.errors) < _ERROR_LIMIT:
        session.errors.append(message)


def _shape_problem(rec: dict[str, Any]) -> str | None:
    """What is wrong with a prompt or tool record's message, if anything. Other record types (summaries, and
    tool results, which are user records of tool_result blocks) are recognized and not checked here."""
    if rec.get("type") not in ("user", "assistant"):
        return None
    message = rec.get("message")
    if message is None:
        return None
    if not isinstance(message, dict):
        return "message is not an object"
    content = message.get("content")
    if content is not None and not isinstance(content, (str, list)):
        return "message content is not text or a list of blocks"
    return None


def _events_of(rec: dict[str, Any]) -> list[Event]:
    if rec.get("isMeta") or rec.get("isCompactSummary"):  # the summary after a compaction retells the session
        return []
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if rec.get("type") == "user":
        if rec.get("isSidechain"):
            return []
        text = prompt_text(_text_of(content))
        return [Event("prompt", text)] if text else []
    if rec.get("type") == "assistant" and isinstance(content, list):
        return [tool_event(b.get("input"), b.get("name")) for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"]
    return []


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def tool_event(tool_input: Any, name: Any = None) -> Event:
    args = tool_input if isinstance(tool_input, dict) else {}
    subagent = isinstance(name, str) and name in _SUBAGENT_TOOLS
    dropped = _CONTENT_KEYS | {"prompt"} if subagent else _CONTENT_KEYS
    strings = [v for k, v in args.items() if k not in dropped and isinstance(v, str)]
    raw_command = args.get("command")
    command = raw_command if isinstance(raw_command, str) else ""
    paths = tuple(args[k] for k in _PATH_KEYS if isinstance(args.get(k), str))
    return Event("tool", " ".join(strings), command=command, paths=paths)

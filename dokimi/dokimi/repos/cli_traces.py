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
from datetime import datetime
from typing import Any

from .shell_search import Searched, searched_field
from .shell_split import split_script
from .shell_walk import _HEREDOC as _HEREDOC  # re-exported: the walker is defined in shell_walk
from .shell_walk import _LONG_VALUE as _LONG_VALUE
from .shell_walk import Walked as Walked
from .shell_walk import _ungrouped, _walk_script, unwrap_shell
from .shell_walk import shell_reads as shell_reads
from .shell_walk import shell_reads_moved as shell_reads_moved
from .shell_walk import shell_walk as shell_walk
from .shell_walk import shell_walked
from .shell_words import _is_activate, _is_cd
from .transcript import Transcript, _from_cwd, _parse_time, clip_output, relative_path
from .turn_events import codex_turn

_COPILOT_SHELL = {"bash", "powershell"}
_COPILOT_READ = {"view"}
_COPILOT_EDIT = {"edit", "create", "str_replace_editor", "write"}
_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.M)


def codex_json(raw: str, workdir: str | None, cwd: str = "") -> Transcript:
    out = Transcript()
    messages: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}  # Track started items by ID to update on completion
    event_reads: dict[int, _Derived] = {}  # command event -> the read events derived from it
    for n, line in enumerate(raw.split("\n"), 1):  # a JSON string may hold U+2028: splitlines would cut it
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
            out.errors.append(f"line {n}: not a JSON object")
            continue
        try:
            _codex_apply(out, rec, messages, workdir, by_id, cwd, event_reads)
        except (AttributeError, TypeError, ValueError) as exc:
            out.dropped_lines += 1
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    out.answer = messages[-1].strip() if messages else ""
    out.agent_messages = messages
    drop_failed_searches(out.events)
    return out


def _codex_apply(out: Transcript, rec: dict[str, Any], messages: list[str], workdir: str | None,
                  by_id: dict[str, dict[str, Any]] | None = None, cwd: str = "",
                  event_reads: dict[int, _Derived] | None = None) -> None:
    if by_id is None:
        by_id = {}
    if event_reads is None:
        event_reads = {}
    kind = rec.get("type")
    _codex_keep_record(out, kind, rec)
    if kind == "turn.completed":
        _codex_usage(out, rec.get("usage") or {})
    elif kind == "turn.failed" or (kind == "error" and not str(rec.get("message", "")).startswith("Reconnecting")):
        # Only set is_error=True for turn.failed; a bare error after it should not override
        if kind == "turn.failed":
            out.is_error = True
        msg = rec.get("message") or (rec.get("error") or {}).get("message")
        out.errors.append(f"{kind}: {str(msg)[:200]}")
    elif kind == "item.started":
        # Record started items to preserve them even if completion is missing (timeout case)
        item = rec.get("item") or {}
        if item.get("type") == "command_execution":
            _codex_command_started(out, item, workdir, by_id, cwd, event_reads)
    elif kind == "item.completed":
        _codex_item_completed(out, rec.get("item") or {}, messages, workdir, by_id, cwd, event_reads)


def _codex_keep_record(out: Transcript, kind: Any, rec: dict[str, Any]) -> None:
    """The start-up and closing records as printed; run_context reads them, events do not change."""
    if kind == "thread.started":
        out.start_record = rec
    elif kind in ("turn.completed", "turn.failed"):
        out.final_record = rec  # the last turn's, as the turn's own record


def _codex_usage(out: Transcript, usage: dict[str, Any]) -> None:
    turn = codex_turn(out.num_turns or 0, usage)  # its turn event, and the totals: both from one reading
    out.num_turns = (out.num_turns or 0) + 1
    out.result_seen = True  # a turn ended: the agent started, whatever else it printed
    out.events.append(turn)
    # A missing or all-zero usage is not a measured zero, and a count it leaves out stays None on its own.
    # OpenAI counts the cached part inside input_tokens; Claude cells keep cache reads out of input_tokens
    if turn["input"] is not None:
        out.input_tokens = (out.input_tokens or 0) + turn["input"]
    if turn["output"] is not None:
        out.output_tokens = (out.output_tokens or 0) + turn["output"]
    if turn["cache_read"] is not None:  # reported, so a zero is measured; absent, it stays None
        out.cache_read_tokens = (out.cache_read_tokens or 0) + turn["cache_read"]
    if any(turn[k] is not None for k in ("input", "output", "cache_read")):
        out.usage_scope = "main"  # the turns are the parent's; Codex reports no subagent tokens here


def _codex_key(item: dict[str, Any], script: str) -> str:
    # Use item.id if available (handles overlapping commands with same text), fallback to text
    item_id = item.get("id")
    return str(item_id) if item_id else f"text:{script}"


def _codex_command_started(out: Transcript, item: dict[str, Any], workdir: str | None,
                           by_id: dict[str, dict[str, Any]], cwd: str,
                           event_reads: dict[int, _Derived]) -> None:
    script = unwrap_shell(str(item.get("command") or ""))
    paths, unknown, searched = _counted_reads(out, script, cwd)
    read_events = [{"t": None, "kind": "read", "path": relative_path(path, workdir)} for path in paths]
    out.events.extend(read_events)
    event: dict[str, Any] = {"t": None, "kind": "command", "text": script, "rc": None,
                             **searched_field(searched, workdir)}
    event_reads[id(event)] = (read_events, unknown)
    by_id[_codex_key(item, script)] = event
    out.events.append(event)


def _codex_item_completed(out: Transcript, item: dict[str, Any], messages: list[str], workdir: str | None,
                          by_id: dict[str, dict[str, Any]], cwd: str,
                          event_reads: dict[int, _Derived]) -> None:
    itype = item.get("type")
    if itype == "agent_message":
        messages.append(str(item.get("text") or ""))
    elif itype == "command_execution":
        script = unwrap_shell(str(item.get("command") or ""))
        item_key = _codex_key(item, script)
        if item_key in by_id:
            _codex_command_finished(out, item, by_id.pop(item_key), event_reads)
        else:
            _codex_command_unstarted(out, item, script, workdir, cwd)
    elif itype == "file_change":
        status = item.get("status")  # a failed patch changed nothing
        rc = {"completed": 0, "failed": 1}.get(status) if isinstance(status, str) else None
        changes = [] if item.get("changes") is None else item["changes"]
        # an entry that is not a change makes the record malformed: it is dropped whole (counted), not read in part
        if not isinstance(changes, list) or not all(
                isinstance(c, dict) and isinstance(c.get("path"), str) and c["path"] for c in changes):
            raise ValueError("file_change with a malformed changes entry")
        for change in changes:
            event: dict[str, Any] = {"t": None, "kind": "edit", "path": relative_path(str(change["path"]), workdir)}
            if change.get("kind") == "delete":  # a deleted file is no useful edit (§4.1)
                event["deleted"] = True
            if rc is not None:
                event["rc"] = rc
            out.events.append(event)
    elif itype in ("mcp_tool_call", "web_search"):
        name = f"{item.get('server')}.{item.get('tool')}" if itype == "mcp_tool_call" else "web_search"
        out.events.append({"t": None, "kind": "tool", "name": name})


def _codex_command_finished(out: Transcript, item: dict[str, Any], event: dict[str, Any],
                            event_reads: dict[int, _Derived]) -> None:
    """Update the event from item.started with completion data."""
    event["rc"] = item.get("exit_code")
    if item.get("aggregated_output"):
        event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
    elif item.get("aggregated_output") == "":
        event.pop("searched", None)  # it printed nothing, so no search of it showed a line
    derived, unknown = event_reads.get(id(event), ([], 0))
    if _failed_read(str(event["text"]), event["rc"]):  # drop the read events derived from it, and its unknown ones
        out.unknown_reads -= unknown
        dropped = {id(e) for e in derived}
        out.events[:] = [e for e in out.events if id(e) not in dropped]


def _codex_command_unstarted(out: Transcript, item: dict[str, Any], script: str, workdir: str | None,
                             cwd: str) -> None:
    """Fallback for item.completed without item.started."""
    walked = shell_walked(script, cwd)
    if not _failed_read(script, item.get("exit_code")):
        out.unknown_reads += walked.unknown
        for path in walked.reads:
            out.events.append({"t": None, "kind": "read", "path": relative_path(path, workdir)})
    printed = item.get("aggregated_output") != ""  # an empty output: no search of it showed a line
    event = {"t": None, "kind": "command", "text": script, "rc": item.get("exit_code"),
             **searched_field(walked.searched if printed else [], workdir)}
    if item.get("aggregated_output"):
        event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
    out.events.append(event)


_Derived = tuple[list[dict[str, Any]], int]  # a command's read events, and how many files it named at a directory not known


def _counted_reads(out: Transcript, script: str, cwd: str) -> tuple[list[str], int, list[str]]:
    """:func:`shell_reads`, the files it named at a directory not known, which ``out.unknown_reads`` counts
    until the command's result says it failed (:func:`_failed_read`), and the files its searches show lines of."""
    walked = shell_walked(script, cwd)
    out.unknown_reads += walked.unknown
    return walked.reads, walked.unknown, walked.searched


def drop_failed_searches(events: list[dict[str, Any]]) -> None:
    """A command that failed and was one search of one file (:func:`_failed_read`), or whose last part is its only search
    and exited 1 (:func:`failed_search`), showed no line: it has no ``searched``. A search piped into ``head`` or ``tail``
    is judged by :func:`failed_search` alone: its exit 141 is ``grep`` killed by the closed pipe after it showed lines."""
    for event in events:
        if event.get("searched") and event.get("kind") == "command":
            text, rc = str(event["text"]), event.get("rc")
            if failed_search(text, rc) or (not _walk_script(text, "")[1].cut and _failed_read(text, rc)):
                del event["searched"]


def failed_search(script: str, rc: Any, exit_known: bool = True) -> bool:
    """Whether ``script`` exited 1 and every search in it is in its last part (``grep x A.md sub/A.md``, ``echo hi; grep x
    A.md``, ``cd sub && grep x A.md | head``): the exit status is that part's, and 1 is a search that matched no line in
    any file. A search before the last part (``grep x A.md; grep y B.md``) may be the one that showed lines. Without a
    known exit (``exit_known`` false: Claude Code says only that the call failed), a last part that pipes the search into
    ``head`` or ``tail`` is no failed search: under ``pipefail`` the failure may be ``grep`` killed by the closed pipe, after
    it showed lines."""
    if rc != 1:
        return False
    found, walk = _walk_script(script, "")
    if walk.cut and not exit_known:
        return False
    # the same walk as the whole script's, so a ``cd`` that loses the directory hides no earlier search; ``rg --passthru``
    # prints every line, so its exit 1 says nothing
    earlier = any(isinstance(p, Searched) for p in found[:walk.last_start])
    return not earlier and not walk.passthru and any(isinstance(p, Searched) for p in found[walk.last_start:])


def _failed_read(script: str, rc: Any) -> bool:
    """A non-zero exit of a command that is one part naming one file, alone or after ``cd`` parts or a virtualenv's
    activate (``cd sub && cat AGENTS.md``, ``source .venv/bin/activate && cat AGENTS.md``: it or the cat failed): that
    file was not read, known or not (``cd -; cat missing``: no read, and no ``unknown_reads``).

    Otherwise the exit code does not say which read failed (``cat a missing`` prints ``a`` and
    exits 1), so the reads are kept: also when only one of the files is known (``cd -; cat missing /ws/a``). A part
    before it that is not surely a ``cd`` (``time -p cd sub``) keeps them too: its exit may be the one that failed,
    and a read is dropped only when that file surely was not printed. A part is split as the walker splits it
    (``X="a b" cd sub`` and ``source 'my env/bin/activate'`` are surely a ``cd`` and an activate). A brace group's
    braces are not parts: ``{ cat AGENTS.md; }`` and ``cd sub && { cat AGENTS.md; }`` are one read.
    """
    parts = [p for p in (_ungrouped(part.text) for part in split_script(script)) if p]
    if rc in (None, 0) or not all(_is_cd(p) or _is_activate(p) for p in parts[:-1]):
        return False
    walk = _walk_script(script, "")[1]
    return walk.operands == 1 and not walk.passthru  # the printer's own operands, not the reads kept of them


class _CopilotRun:
    """What ``copilot_json`` collects across the lines: the first timestamp, tool events by call id, the
    answer (the final one, else the last message) and the turn count."""

    def __init__(self) -> None:
        self.start: datetime | None = None
        self.by_id: dict[str, _Derived] = {}  # a call's events, and the files it named at a directory not known
        self.final = self.last = ""
        self.turns = 0
        self.result = False  # the final ``result`` record came: the agent started even with no turn

    def offset(self, rec: dict[str, Any]) -> float | None:
        """Seconds from the first timestamp to this record's."""
        stamp = _parse_time(rec.get("timestamp"))
        if stamp is None:
            return None
        if self.start is None:
            self.start = stamp
        return round((stamp - self.start).total_seconds(), 3)

    def message(self, data: dict[str, Any]) -> None:
        text = str(data.get("content") or "")
        if text.strip():
            self.last = text
            if data.get("phase") == "final_answer":
                self.final = text

    def apply(self, out: Transcript, rec: dict[str, Any], t: float | None, workdir: str | None, cwd: str) -> None:
        kind, data = rec.get("type"), rec.get("data") or {}
        if kind == "session.start":
            out.start_record = rec
        elif kind == "assistant.turn_start":
            self.turns += 1
        elif kind == "assistant.message":
            self.message(data)
        elif kind == "tool.execution_start":
            counted = out.unknown_reads
            events = _copilot_tool(data, t, workdir, cwd, out)
            out.events.extend(events)
            self.by_id[str(data.get("toolCallId"))] = (events, out.unknown_reads - counted)
        elif kind == "tool.execution_complete":
            _copilot_result(out, self.by_id.get(str(data.get("toolCallId")), ([], 0)), data)
        elif kind == "result":
            self.result = True
            out.final_record = rec
            out.is_error = rec.get("exitCode") not in (0, None)


def copilot_json(raw: str, workdir: str | None, cwd: str = "") -> Transcript:
    out = Transcript()
    run = _CopilotRun()
    for n, line in enumerate(raw.split("\n"), 1):
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
            out.errors.append(f"line {n}: not a JSON object")
            continue
        t = run.offset(rec)
        try:
            run.apply(out, rec, t, workdir, cwd)
        except (AttributeError, TypeError, ValueError) as exc:
            out.dropped_lines += 1
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    out.answer = (run.final or run.last).strip()
    out.num_turns = run.turns or None
    out.result_seen = bool(run.turns) or run.result
    drop_failed_searches(out.events)  # not in _copilot_result, which only the reads' failure belongs to
    return out


def _copilot_tool(data: dict[str, Any], t: float | None, workdir: str | None, cwd: str,
                  out: Transcript) -> list[dict[str, Any]]:
    name = str(data.get("toolName") or "")
    args = data.get("arguments")
    if name in _COPILOT_SHELL | _COPILOT_READ | _COPILOT_EDIT and not isinstance(args, dict):
        raise TypeError(f"{name} arguments are not an object")  # counted as a dropped line by the caller
    obj: dict[str, Any] = args if isinstance(args, dict) else {}
    if name in _COPILOT_SHELL:
        command = str(obj.get("command", ""))
        # only bash is a POSIX script; a PowerShell command is not parsed for the files it reads
        scripts, _, searched = _counted_reads(out, command, cwd) if name == "bash" else ([], 0, [])
        reads = [{"t": t, "kind": "read", "path": relative_path(p, workdir)} for p in scripts]
        return [*reads, {"t": t, "kind": "command", "text": command, "rc": None, **searched_field(searched, workdir)}]
    if name in _COPILOT_READ | _COPILOT_EDIT:
        path = str(obj.get("path") or obj.get("file_path") or "")
        if not path:  # a call without a path names no file
            return []
        kind = "read" if name in _COPILOT_READ else "edit"
        return [{"t": t, "kind": kind, "path": relative_path(_from_cwd(path, cwd), workdir)}]
    if name == "apply_patch":
        text = args if isinstance(args, str) else str((args or {}).get("input", ""))
        return [{"t": t, "kind": "edit", "path": relative_path(_from_cwd(p.strip(), cwd), workdir),
                 **({"deleted": True} if verb == "Delete" else {})}
                for verb, p in _PATCH_FILE.findall(text)]
    return [{"t": t, "kind": "tool", "name": name}]


def _copilot_result(out: Transcript, call: _Derived, data: dict[str, Any]) -> None:
    """Fill in the completion; drop the reads of a failed ``view`` and of a failed one-file command, and take back
    the files that command named at a directory not known."""
    events, unknown = call
    reads = [e for e in events if e["kind"] == "read"]
    failed = data.get("success") is False
    for event in events:
        if event["kind"] == "edit" and data.get("success") is not None:
            # a failed edit changed nothing: rc 1, so it is no useful result
            event["rc"] = 0 if data.get("success") else 1
        if event["kind"] != "command":
            continue
        shell = data.get("shellExecution") or {}
        code = shell.get("exitCode") if isinstance(shell, dict) else None
        event["rc"] = code if isinstance(code, int) else (None if data.get("success") is None
                                                          else 0 if data.get("success") else 1)
        failed = _failed_read(str(event["text"]), event["rc"])
        result = data.get("result") or {}
        output = result.get("content") if isinstance(result, dict) else None
        if isinstance(output, str) and output:
            event["output"], event["output_truncated"] = clip_output(output)
        elif output == "":
            event.pop("searched", None)  # it printed nothing, so no search of it showed a line
    if failed:
        out.unknown_reads -= unknown
        dropped = {id(e) for e in reads}
        out.events[:] = [e for e in out.events if id(e) not in dropped]

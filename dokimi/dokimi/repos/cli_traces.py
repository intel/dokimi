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
import posixpath
import re
import shlex
from typing import Any

from .transcript import Transcript, _parse_time, clip_output, relative_path

_BASH_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -lc (.+)$", re.S)
_PRINTERS = {"cat", "head", "tail", "nl", "sed"}
_COPILOT_SHELL = {"bash", "powershell"}
_COPILOT_READ = {"view"}
_COPILOT_EDIT = {"edit", "create", "str_replace_editor", "write"}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
_CONTINUATION = re.compile(r"\\\n")
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
# a redirect that writes nowhere a read could be confused with: 2>&1, 2>/dev/null, &>/dev/null
_HARMLESS_REDIRECT = re.compile(r"(?:\d*>&\d+|&>>?\s*/dev/null|\d*>>?\s*/dev/null)(?=\s|$|[;&|])")
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}
_DIR_STACK = {"pushd", "popd"}
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")  # a pipe, not ||
_GROUP_OPEN = re.compile(r"(?:^|[\s;&|(])\{(?=\s|$)")  # a brace group opens: ``{`` as a word
_GROUP_CLOSE = re.compile(r"(?:^|\s)\}(?=\s|$|[;&|])")
# the values of long options that take a separate word (``head --lines 5``), by command
_LONG_VALUE = {
    "head": {"--lines", "--bytes"},
    "tail": {"--lines", "--bytes", "--sleep-interval", "--pid", "--max-unchanged-stats"},
    "nl": {"--body-numbering", "--section-delimiter", "--footer-numbering", "--header-numbering",
           "--number-format", "--number-separator", "--number-width", "--starting-line-number",
           "--line-increment", "--join-blank-lines"},
}


def unwrap_shell(command: str) -> str:
    """The script of ``/usr/bin/bash -lc '<script>'`` (how Codex reports a command), else ``command``."""
    m = _BASH_WRAP.match(command.strip())
    if not m:
        return command
    try:
        parts = shlex.split(m.group(1))
    except ValueError:
        return command
    return parts[0] if len(parts) == 1 else m.group(1)


def shell_reads(script: str, cwd: str = "") -> list[str]:
    """Files a command only prints: ``cat a b``, ``head -n 5 a``, ``sed -n '1,80p' a``, ``nl -ba a``.

    A pipeline or a redirect is not a read of its arguments (``cat a > b`` writes b), so it gives nothing;
    each ``&&`` / ``;`` / ``||`` / ``&`` / newline part is read on its own. ``cwd`` is the directory the
    script starts in, relative to the workspace root. ``cd`` moves it for the parts that follow: a
    relative path is joined to it and normalised (no ``.`` or ``..``). The directory is unknown (a
    relative read is not recorded) after ``cd -``, a bare ``cd``, a ``cd`` to a computed path, a
    ``pushd`` / ``popd``, a ``cd`` after ``if`` / ``then`` / ``do`` / ``else`` (it may not run), or a
    ``||`` that follows a ``cd``. A subshell ``( ... )`` is not tracked. A brace group that is
    backgrounded or piped runs in a subshell, so the directory is restored after it. Absolute paths
    are kept.
    """
    out: list[str] = []
    here: str | None = posixpath.normpath(cwd) if cwd else ""
    moved = False  # a cd was seen: after ||, the directory is not known
    previous = ""
    groups: list[tuple[str | None, bool]] = []  # open brace groups: (directory before it, in a pipeline)
    script = _HEREDOC.sub(lambda m: m.group(3), _CONTINUATION.sub(" ", script))  # a heredoc body is input, not commands
    for part, after, in_subshell in _split_script(script):
        op, previous = previous, after
        if op == "||" and moved:
            here = None
        if in_subshell:  # a subshell: its reads are not tracked
            continue
        bare = _bare(part)
        opened = _GROUP_OPEN.search(bare)
        if opened:
            piped = bool(_PIPE.search(bare[:opened.start()]))
            groups.append((here, piped))
            if piped:  # the group runs in a pipeline's subshell: where it ends up is not tracked
                here = None
        closed = _GROUP_CLOSE.search(bare)
        if closed and groups:
            before, piped = groups.pop()
            if piped or after == "&" or _PIPE.search(bare[closed.start():]):  # the group ran in a subshell
                here = before
        part = _HARMLESS_REDIRECT.sub(" ", part)
        try:
            words = shlex.split(part) if not _computed(part) else None
        except ValueError:
            continue
        if words is None:
            if part.split(None, 1)[:1] == ["cd"]:
                here, moved = None, True
            continue
        if words[:1] == ["{"]:  # a brace group runs in this shell: its cd and reads count
            words = words[1:]
        conditional = False
        while words and words[0] in _KEYWORDS:  # ``then cd x``: the cd may not run
            words, conditional = words[1:], True
        if not words:
            continue
        if words[0] in _DIR_STACK or (conditional and words[0] == "cd"):
            here, moved = None, True
            continue
        if words[0] == "cd":
            moved = True
            if after == "&":  # runs in a subshell: this shell stays where it was
                continue
            here = _cd(here, words[1:])
            continue
        if words[0] not in _PRINTERS:
            continue
        files = _sed_files(words[1:]) if words[0] == "sed" else _print_files(words[0], words[1:])
        for f in files:
            if f.startswith("/"):
                out.append(posixpath.normpath(f))
            elif here is not None:
                out.append(posixpath.normpath(posixpath.join(here, f)))
    return out


def _cd(here: str | None, args: list[str]) -> str | None:
    """The directory after ``cd <args>`` from ``here`` (``None``: unknown)."""
    rest = list(args)
    while rest and rest[0] in ("-L", "-P", "-e", "-@"):
        rest.pop(0)
    if rest and rest[0] == "--":
        rest.pop(0)
    if len(rest) != 1 or rest[0] == "-" or rest[0].startswith("~"):
        return None  # home, the previous directory, or something we do not track
    target = rest[0]
    if target.startswith("/"):
        return posixpath.normpath(target)
    return None if here is None else posixpath.normpath(posixpath.join(here, target))


def _computed(part: str) -> bool:
    """A shell expansion outside single quotes (``$x``, backticks) or a pipe or redirect outside any quote."""
    bare = re.sub(r"'[^']*'", "''", part)
    if re.search(r"[$`]", bare):
        return True
    return bool(re.search(r"[|<>]", re.sub(r'"[^"]*"', '""', bare)))


def _print_files(cmd: str, args: list[str]) -> list[str]:
    """Operands of ``cat`` / ``head`` / ``tail`` / ``nl``, not the values of their options (``-n 5``, ``-ba``, ``-w 3``)."""
    takes = {"head": "nc", "tail": "ncs", "nl": "bdfhilnsvw"}.get(cmd, "")  # short options that take a value
    files, skip, ended = [], False, False
    for a in args:
        if skip:
            skip = False
        elif ended or not a.startswith("-"):
            files.append(a)
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            skip = a in _LONG_VALUE.get(cmd, ())  # the value is the next word
        else:
            for i, c in enumerate(a[1:], 1):
                if c in takes:  # the value is the next word unless it is attached (-n5, -ba)
                    skip = i == len(a) - 1
                    break
    return files


def _sed_files(args: list[str]) -> list[str]:
    """Files of ``sed -n <script> files``; nothing for an in-place edit or a run without ``-n``."""
    quiet = scripted = ended = skip = False
    positional: list[str] = []
    for a in args:
        if skip:
            skip = False
        elif ended or not a.startswith("-") or a == "-":
            positional.append(a)
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            name = a.split("=", 1)[0]
            if name == "--in-place":
                return []
            quiet |= name in ("--quiet", "--silent")
            if name in ("--expression", "--file"):
                scripted = True
                skip = "=" not in a
        else:  # a cluster of short options: -n, -ne, -nE, -i.bak
            for i, c in enumerate(a[1:], 1):
                if c == "i":
                    return []
                quiet |= c == "n"
                if c in "ef":
                    scripted = True
                    skip = i == len(a) - 1  # the value is the next word unless it is attached
                    break
                if c in "l":
                    skip = i == len(a) - 1
                    break
    if not quiet:
        return []
    return positional if scripted else positional[1:]


def _bare(part: str) -> str:
    """``part`` with the text inside quotes emptied, so only shell syntax is left to match."""
    return re.sub(r"'[^']*'", "''", re.sub(r'"[^"]*"', '""', part))


def _split_script(script: str) -> list[tuple[str, str, bool]]:
    """``(part, operator after it, in a subshell)`` for each part of ``script`` split at unquoted ``&&``,
    ``||``, ``;``, ``&`` and newlines (the operator is ``""`` after the last part). A ``#`` that starts a
    word begins a comment, which runs to the end of the line and is dropped. A part inside ``( ... )``,
    or holding a parenthesis, is in a subshell."""
    parts: list[tuple[str, str, bool]] = []
    cur: list[str] = []
    quote = ""
    depth = 0  # open parentheses
    paren = False  # the part being built holds a parenthesis
    i = 0
    while i < len(script):
        c = script[i]
        two = script[i:i + 2]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(script):
                i += 1
                cur.append(script[i])
            elif c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < len(script):
            cur.append(c + script[i + 1])
            i += 1
        elif c == "#" and (i == 0 or script[i - 1] in " \t\n;&|()"):
            end = script.find("\n", i)  # the comment ends at the newline, which still splits
            i = len(script) if end < 0 else end
            continue
        elif c in "()":
            paren = True
            depth = max(0, depth + (1 if c == "(" else -1))
            cur.append(c)
        else:
            op = two if two in ("&&", "||") else c if c in ";\n" else ""
            if c == "&" and not op and script[i - 1:i] not in (">", "<") and script[i + 1:i + 2] != ">":
                op = "&"
            if not op:
                cur.append(c)
            else:
                text = "".join(cur).strip()
                if text:
                    parts.append((text, op if op != "\n" else ";", paren or depth > 0))
                cur = []
                paren = depth > 0
                i += len(op) - 1
        i += 1
    text = "".join(cur).strip()
    if text:
        parts.append((text, "", paren or depth > 0))
    return parts


def codex_json(raw: str, workdir: str | None, cwd: str = "") -> Transcript:
    out = Transcript()
    messages: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}  # Track started items by ID to update on completion
    event_reads: dict[int, list[dict[str, Any]]] = {}  # command event -> the read events derived from it
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
            _codex_apply(out, rec, messages, workdir, by_id, cwd, event_reads)
        except (AttributeError, TypeError, ValueError) as exc:
            out.errors.append(f"line {n}: unexpected shape ({exc})")
    out.answer = messages[-1].strip() if messages else ""
    return out


def _codex_apply(out: Transcript, rec: dict[str, Any], messages: list[str], workdir: str | None,
                  by_id: dict[str, dict[str, Any]] | None = None, cwd: str = "",
                  event_reads: dict[int, list[dict[str, Any]]] | None = None) -> None:
    if by_id is None:
        by_id = {}
    if event_reads is None:
        event_reads = {}
    kind = rec.get("type")
    if kind == "turn.completed":
        usage = rec.get("usage") or {}
        out.num_turns = (out.num_turns or 0) + 1
        fresh, produced = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        if fresh or produced:  # a missing or all-zero usage is not a measured zero: the counts stay None
            out.input_tokens = (out.input_tokens or 0) + fresh
            out.output_tokens = (out.output_tokens or 0) + produced
    elif kind == "turn.failed" or (kind == "error" and not str(rec.get("message", "")).startswith("Reconnecting")):
        # Only set is_error=True for turn.failed; a bare error after it should not override
        if kind == "turn.failed":
            out.is_error = True
        msg = rec.get("message") or (rec.get("error") or {}).get("message")
        out.errors.append(f"{kind}: {str(msg)[:200]}")
    elif kind == "item.started":
        # Record started items to preserve them even if completion is missing (timeout case)
        item = rec.get("item") or {}
        itype = item.get("type")
        if itype == "command_execution":
            script = unwrap_shell(str(item.get("command") or ""))
            # Use item.id if available (handles overlapping commands with same text), fallback to text
            item_id = item.get("id")
            item_key = str(item_id) if item_id else f"text:{script}"
            read_events = [{"t": None, "kind": "read", "path": relative_path(path, workdir)}
                           for path in shell_reads(script, cwd)]
            out.events.extend(read_events)
            event: dict[str, Any] = {"t": None, "kind": "command", "text": script, "rc": None}
            event_reads[id(event)] = read_events
            by_id[item_key] = event
            out.events.append(event)
    elif kind == "item.completed":
        item = rec.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            messages.append(str(item.get("text") or ""))
        elif itype == "command_execution":
            script = unwrap_shell(str(item.get("command") or ""))
            # Use item.id if available (handles overlapping commands with same text), fallback to text
            item_id = item.get("id")
            item_key = str(item_id) if item_id else f"text:{script}"
            if item_key in by_id:
                # Update the event from item.started with completion data
                event = by_id[item_key]
                event["rc"] = item.get("exit_code")
                if item.get("aggregated_output"):
                    event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
                del by_id[item_key]
                derived = event_reads.get(id(event), [])
                if _failed_read(str(event["text"]), derived, event["rc"]):  # drop the read events derived from it
                    dropped = {id(e) for e in derived}
                    out.events[:] = [e for e in out.events if id(e) not in dropped]
            else:
                # Fallback for item.completed without item.started
                paths = shell_reads(script, cwd)
                if not _failed_read(script, paths, item.get("exit_code")):
                    for path in paths:
                        out.events.append({"t": None, "kind": "read", "path": relative_path(path, workdir)})
                event = {"t": None, "kind": "command", "text": script, "rc": item.get("exit_code")}
                if item.get("aggregated_output"):
                    event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
                out.events.append(event)
        elif itype == "file_change":
            for change in item.get("changes") or []:
                if isinstance(change, dict) and change.get("path"):
                    out.events.append({"t": None, "kind": "edit", "path": relative_path(str(change["path"]), workdir)})
        elif itype in ("mcp_tool_call", "web_search"):
            name = f"{item.get('server')}.{item.get('tool')}" if itype == "mcp_tool_call" else "web_search"
            out.events.append({"t": None, "kind": "tool", "name": name})


def _failed_read(script: str, reads: list[Any], rc: Any) -> bool:
    """A non-zero exit of a command that is one part naming one file: that file was not read.

    Otherwise the exit code does not say which read failed (``cat a missing`` prints ``a`` and
    exits 1), so the reads are kept.
    """
    return rc not in (None, 0) and len(reads) == 1 and len(_split_script(script)) == 1


def copilot_json(raw: str, workdir: str | None, cwd: str = "") -> Transcript:
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
                events = _copilot_tool(data, t, workdir, cwd)
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


def _copilot_tool(data: dict[str, Any], t: float | None, workdir: str | None, cwd: str = "") -> list[dict[str, Any]]:
    name = str(data.get("toolName") or "")
    args = data.get("arguments")
    if name in _COPILOT_SHELL:
        command = str((args or {}).get("command", "")) if isinstance(args, dict) else ""
        reads = [{"t": t, "kind": "read", "path": relative_path(p, workdir)} for p in shell_reads(command, cwd)]
        return [*reads, {"t": t, "kind": "command", "text": command, "rc": None}]
    if name in _COPILOT_READ | _COPILOT_EDIT and isinstance(args, dict):
        path = str(args.get("path") or args.get("file_path") or "")
        return [{"t": t, "kind": "read" if name in _COPILOT_READ else "edit", "path": relative_path(path, workdir)}]
    if name == "apply_patch":
        text = args if isinstance(args, str) else str((args or {}).get("input", ""))
        return [{"t": t, "kind": "edit", "path": relative_path(p.strip(), workdir)} for p in _PATCH_FILE.findall(text)]
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

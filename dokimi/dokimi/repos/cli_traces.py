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
from datetime import datetime
from typing import Any

from .shell_split import split_script
from .shell_words import (_DIR_LOST, _DIRS, _KEYWORDS, _cd, _command_words, _is_cd, _may_run_dir, _run_name, _runs_dir,
                          _time_keyword, _unquoted)
from .transcript import Transcript, _from_cwd, _parse_time, clip_output, relative_path

_BASH_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -lc (.+)$", re.S)
_PRINTERS = {"cat", "head", "tail", "nl", "sed"}
_COPILOT_SHELL = {"bash", "powershell"}
_COPILOT_READ = {"view"}
_COPILOT_EDIT = {"edit", "create", "str_replace_editor", "write"}
_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.M)
_CONTINUATION = re.compile(r"\\\n")
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
# a redirect that leaves what a printing command shows on stdout: 2>&1, >&2, 2>/dev/null. A stdout discard
# (>/dev/null, 1>/dev/null, &>/dev/null) is not one of them: no content reaches the agent, so no read.
_HARMLESS_REDIRECT = re.compile(r"(?:(?<![^\s;&|(])\d+)?>&\d+(?=\s|$|[;&|])|(?<![^\s;&|(])(?!1(?![0-9]))\d+>>?\s*/dev/null(?=\s|$|[;&|])")
_GLOB = re.compile(r"[*?\[]|\{[^\s{}]*(?:,|\.\.)[^\s{}]*\}")  # a pathname or brace expansion, unquoted
_PRINT_ONLY = re.compile(r"\s*(?:(?:\d+|\$)(?:\s*,\s*(?:\d+|\$))?)?\s*p\s*")  # ``p``, ``5p``, ``1,80p``, ``$p``: prints, writes nothing
# stderr sent to a file: stdout, what a printing command shows, is unchanged
# (matched on the part with quoted text masked, so a quote glued to ``2`` or to the target is seen)
_STDERR_FILE = re.compile(r"(?<![^\s;&|(])2>>?[ \t]*[^\s;&|<>()]+")
# redirects apply in order: after ``2>/dev/null`` or ``2>file``, a ``1>&2`` sends stdout there too, away from the agent
_STDOUT_AFTER_STDERR = re.compile(r"(?<![^\s;&|(])2>>?(?!&)[ \t]*[^\s;&|<>()]+.*?(?<![^\s;&|(])1?>&2(?!\d)")
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")  # a pipe, not ||
_GROUP_OPEN = re.compile(r"(?:^|[\s;&|(])\{(?=\s|$)")  # a brace group opens: ``{`` as a word
_GROUP_CLOSE = re.compile(r"(?:^|\s)\}(?=\s|$|[;&|])")
_FUNCTION_DEF = re.compile(r"\s*(?:function\s+[^\s(){}]+(?:\s*\(\s*\))?|[^\s(){}=$`]+\s*\(\s*\))\s*\{(?=\s|$)")
_STDOUT_FILE = re.compile(r"&>>?|(?<![\w&>])1?>>?(?!&)|(?<![\w&>])1?>&(?!\d)")  # a group's stdout to a file: not the agent's
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
    ``pushd`` / ``popd``, an ``eval``, ``source`` or ``.`` (it may run a ``cd``; none of these when backgrounded or
    piped, as it then ran in a subshell: ``source env.sh & false || cat a`` reads a), a part the walker does not
    follow whose command word is one of these (``time -p cd sub``, ``X="a b" cd "$D"``: :func:`_may_run_dir`), a ``case``
    arm that names one (``case`` is not followed: its reads are not kept either), ``env -C`` / ``--chdir`` (the
    command's reads, but absolute ones), a ``cd`` after ``if`` / ``then`` / ``do`` / ``else`` (it may not run), a
    ``cd`` after ``&&`` / ``||`` once the chain holding it ends with ``;``, ``&`` or a newline (it may have been
    skipped), or a glob or brace expansion (the files are not known). A subshell ``( ... )`` or `` `...` `` records
    no reads, and a ``cd`` in it moves nothing here. A brace group (``{ cd $(pwd); }``, ``{ { cd x; }; }`` too) or an
    ``&&`` / ``||`` list that is backgrounded, or a piped
    group, runs in a subshell, so the directory is restored after it; the reads of a
    group whose output feeds a pipe after it is dropped, as a pipeline is not a read here; a group whose stdout is
    redirected to a file or /dev/null is dropped, as that output is not shown to the agent. ``--help`` and
    ``--version`` print and open no file. A function definition is skipped whole. Absolute paths are kept.
    """
    return _walk_script(script, cwd)[0]


def shell_reads_moved(script: str, cwd: str = "") -> tuple[list[str], bool]:
    """:func:`shell_reads`, and whether the script may leave its shell in another directory: it ran a ``cd``,
    ``pushd``, ``popd``, ``eval``, ``source`` or ``.`` in that shell, not in a subshell (``( ... )``, a pipeline
    or a backgrounded command, list or group: ``cd sub &``, ``{ cd sub; } | cat``, ``cd sub && make &``,
    ``cd "$X" &``, ``cd sub | cat``, ``{ cd $(pwd); } &``)."""
    return shell_walk(script, cwd)[:2]


def shell_walk(script: str, cwd: str = "") -> tuple[list[str], bool, int]:
    """:func:`shell_reads_moved`, and how many relative files its printing commands named at a directory not known."""
    reads, walk = _walk_script(script, cwd)
    return reads, walk.shell_moved, walk.unknown


def _walk_script(script: str, cwd: str) -> tuple[list[str], _Walk]:
    out: list[str] = []
    walk = _Walk(posixpath.normpath(cwd) if cwd else "")
    previous = ""
    # a line continuation is removed, not made a space (``cat AGENTS\<newline>.md`` reads AGENTS.md); a heredoc body
    # is input, not commands
    script = _HEREDOC.sub(lambda m: m.group(3), _CONTINUATION.sub("", script))
    for part, after, in_subshell, outer in split_script(script):
        op, previous = previous, after
        chained = op in ("&&", "||")
        if not chained and walk.gated:  # a cd after && or || may not have run: the next chain starts in no known directory
            walk.here, walk.gated = None, False
        walk.chain(op, chained)
        if walk.in_function(_bare(part)) or walk.in_case(outer, after, out):  # a definition runs nothing; a case is not followed
            continue
        if op == "||" and walk.moved:
            walk.here = None
        walk.track_groups(_bare(outer), after, out)  # ``{ cd $(pwd); } | cat``: the group is in this shell
        if in_subshell:  # a subshell: its reads are not tracked
            if _runs_dir(outer):  # ``cd $(dirname f)``: the cd is outside the parentheses
                walk.lose(_in_own_shell(outer, after))
            continue
        words = walk.words(part, after)
        if words:
            out.extend(walk.command(words, after, chained, _time_keyword(part)))
    walk.chain(previous, False)  # a list backgrounded at the end (``cd sub && make &``) ran in a subshell too
    return out, walk


_Spot = tuple[str | None, bool, bool]  # (here, shell_moved, moved): what the end of a subshell restores


class _Walk:
    """Where ``shell_reads`` is in the script: the directory (``None``: unknown), whether a ``cd`` was seen, whether
    the script's own shell may have moved, and the open brace groups."""

    def __init__(self, here: str | None) -> None:
        self.here = here
        self.moved = False  # a cd was seen in this shell: after ||, the directory is not known
        self.shell_moved = False  # a directory builtin ran in the script's shell, not in a subshell
        self.gated = False  # a cd after && or || was seen in this chain: it may not have run for the next chain
        # the open brace groups: the spot before it, in a pipeline, (index in the reads, ``unknown``) where it began,
        # the enclosing && / || list's (list_start, listed), which the group's own separators do not end
        self.groups: list[tuple[_Spot, bool, tuple[int, int], tuple[_Spot, bool]]] = []
        self.list_start: _Spot = (here, False, False)  # the spot where the && / || list began
        self.listed = False  # that list has more than one command
        self.function = 0  # the brace depth inside a function definition
        self.case = 0  # the depth inside ``case ... esac``
        self.operands: int | None = None  # how many files the last printing command named
        self.unknown = 0  # the relative files printing commands named at a directory not known

    def spot(self) -> _Spot:
        return self.here, self.shell_moved, self.moved

    def back(self, spot: _Spot) -> None:
        """Back to ``spot`` after a subshell: what ran in it moved neither this shell nor its ``||`` fallbacks."""
        self.here, self.shell_moved, self.moved = spot

    def chain(self, op: str, chained: bool) -> None:
        """A list of more than one command ended by ``&`` ran in a subshell, so this shell did not move: ``cd sub &&
        cat f & cat g`` reads sub/f and g (a lone ``cd x &`` and a ``{ ...; } &`` group are handled where they end)."""
        if op == "&" and self.listed:
            self.back(self.list_start)
        if chained:
            self.listed = True
        else:
            self.list_start, self.listed = self.spot(), False

    def in_function(self, bare: str) -> bool:
        """Whether ``bare`` is part of a function definition (``f() { cd x; cat a; }``): its body is skipped, as
        defining it runs nothing (a later call is not followed)."""
        if not self.function and not _FUNCTION_DEF.match(bare):
            return False
        self.function = max(self.function + len(_GROUP_OPEN.findall(bare)) - len(_GROUP_CLOSE.findall(bare)), 0)
        return True

    def in_case(self, outer: str, after: str, out: list[str]) -> bool:
        """Whether ``outer`` is part of a ``case ... esac``, which is not followed: an arm may run or not, so one naming a
        directory builtin (as any word: ``case x in a) cd sub;; esac``) may move the shell, and its reads are not kept.
        Inside one, each ``case`` word opens another, so a miscount stays inside; its braces still open and close groups."""
        words = _unquoted(outer.split())
        while words[:1] and (words[0] in _KEYWORDS or words[0] == "{"):
            words = words[1:]
        if not self.case and words[:1] != ["case"]:
            return False
        self.case += words.count("case")
        self.track_groups(_bare(outer), after, out)  # before lose: a backgrounded group around the case restores it
        if any(w in _DIRS for w in words):
            self.lose()
        self.case -= words[:1] == ["esac"]
        return True

    def lose(self, own_shell: bool = True) -> None:
        """A directory builtin went where it is not known. Not ``own_shell`` (backgrounded, or a pipeline stage):
        it ran in a subshell, so this shell's directory stays known (``source env.sh & false || cat a`` reads a)."""
        if own_shell:
            self.here, self.shell_moved, self.moved = None, True, True

    def track_groups(self, bare: str, after: str, out: list[str]) -> None:
        """A brace group opening or closing in ``bare``; one that ran in a subshell restores the directory. A group is
        one command of the list around it: ``cd sub && { a; b; } &`` backgrounds the whole list. The reads of a group
        whose output feeds a pipe after it, or a file, are dropped from ``out``, and so are those it counted in
        ``unknown``: its output is not the agent's. Each brace of ``bare`` counts, in order (``{ { cd x``,
        ``} | { cat a``)."""
        braces = [(m.end() - 1, m) for m in _GROUP_OPEN.finditer(bare)] + [(m.end(), m) for m in _GROUP_CLOSE.finditer(bare)]
        for _, m in sorted(braces, key=lambda b: b[0]):
            if m.re is _GROUP_OPEN:
                self.open_group(bare[:m.end() - 1], len(out))
            elif self.groups:
                self.close_group(bare[m.start():], bare[m.end():], after, out)

    def open_group(self, before_brace: str, mark: int) -> None:
        piped = bool(_PIPE.search(before_brace))
        self.groups.append((self.spot(), piped, (mark, self.unknown), (self.list_start, self.listed)))
        if piped:  # the group runs in a pipeline's subshell: where it ends up is not tracked
            self.here = None

    def close_group(self, after_close: str, redirects: str, after: str, out: list[str]) -> None:
        before, piped, (mark, unknown), (self.list_start, self.listed) = self.groups.pop()
        if _PIPE.search(after_close) or _STDOUT_FILE.search(redirects) or _STDOUT_AFTER_STDERR.search(redirects):
            del out[mark:]
            self.unknown = unknown
        if piped or _PIPE.search(after_close) or after == "&":  # the group ran in a pipeline's or a job's subshell
            self.back(before)

    def words(self, part: str, after: str = "") -> list[str]:
        """The words of ``part``; none when it cannot be split or is computed (a computed ``cd`` loses the directory,
        and moves this shell unless ``part`` is a pipeline or is backgrounded by ``after``: then it ran in a subshell)."""
        if not _STDOUT_AFTER_STDERR.search(_bare(part)):  # else ``>&2`` stays: a redirect, so no read
            part = _outside_quotes(part, _HARMLESS_REDIRECT)
        part = _outside_quotes(part, _STDERR_FILE)
        try:
            words = shlex.split(part) if not _computed(part) else None
        except ValueError:
            return []
        if words is None:
            if _runs_dir(part):  # ``builtin cd "$D"``: where it went is not known
                self.lose(_in_own_shell(part, after))
            return []
        while words[:1] == ["{"]:  # a brace group runs in this shell: its cd and reads count
            words = words[1:]
        return words

    def command(self, words: list[str], after: str, chained: bool = False, timed: bool = False) -> list[str]:
        """Move the directory for a ``cd``; the files a printing command reads. ``chained``: the command follows
        ``&&`` or ``||``, so a ``cd`` in it runs only if the command before it succeeded; ``timed``: it starts with
        bash's ``time`` keyword (:func:`~dokimi.repos.shell_words._time_keyword`)."""
        conditional = negated = False
        while words and words[0] in _KEYWORDS:  # ``then cd x``: the cd may not run
            words, conditional, negated = words[1:], True, negated or words[0] == "!"
        words, via_env, chdir = _command_words(words, timed)
        if not words:
            return []
        name = _run_name(words[0], via_env)
        if name is None:
            return []
        if name in _DIR_LOST or (conditional and name == "cd") or _may_run_dir(words, timed):
            self.lose(after != "&")
        elif name == "cd":
            self.gated |= chained
            if after != "&":  # a backgrounded cd runs in a subshell: this shell stays where it was
                self.here, self.shell_moved, self.moved = _cd(self.here, words[1:]), True, True
        elif name in _PRINTERS and not negated:  # ``! cat f`` inverts the exit, which no longer says f was printed
            return self.printed(name, words[1:], chdir)
        return []

    def printed(self, name: str, args: list[str], chdir: bool) -> list[str]:
        """The files a printing command reads; ``unknown`` counts those it names at a directory not known."""
        if _asks_for_help(args):  # ``cat --help AGENTS.md`` prints the help and opens no file
            return []
        files = _sed_files(args) if name == "sed" else _print_files(name, args)
        self.operands = len(files)  # all it names, those at an unknown directory too
        # ``env -C dir cat f`` reads dir/f, which is not known: only an absolute path is the same
        known = [f for f in files if f.startswith("/") or (self.here is not None and not chdir)]
        self.unknown += len(files) - len(known)
        return [self.resolve(f) for f in known]

    def resolve(self, f: str) -> str:
        return posixpath.normpath(f if f.startswith("/") else posixpath.join(self.here or "", f))


def _in_own_shell(outer: str, after: str) -> bool:
    """Whether the part with this outer text runs in the script's shell: not backgrounded by ``after``, nor a
    pipeline stage (a pipe in quotes or escaped, ``\\|``, is not one)."""
    return after != "&" and not _PIPE.search(re.sub(r"\\.", "", _bare(outer)))


def _computed(part: str) -> bool:
    """A shell expansion outside single quotes (``$x``, backticks), a pipe or redirect outside any quote, or a
    glob or brace expansion (``sub*``, ``AGENTS.*``, ``{a,b}``) outside quotes, whose files are not known here."""
    bare = re.sub(r"'[^']*'", "''", part)
    if re.search(r"[$`]", bare):
        return True
    unquoted = re.sub(r'"[^"]*"', '""', bare)
    if re.search(r"[|<>]", unquoted):
        return True
    return bool(_GLOB.search(re.sub(r"\\.", "", unquoted)))  # an escaped ``\*`` is a literal


def _asks_for_help(args: list[str]) -> bool:
    """Whether ``--help`` or ``--version`` is an option of the command (before ``--``): it prints and opens no file."""
    for a in args:
        if a == "--":
            return False
        if a in ("--help", "--version"):
            return True
    return False


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


class _SedScan:
    """What the options of a ``sed`` command line say: ``-n`` (quiet), the scripts given by ``-e`` / ``--expression``,
    a script file (``-f`` / ``--file``, not read here), an in-place edit, and which option takes the next word."""

    def __init__(self) -> None:
        self.quiet = self.in_place = self.script_file = False
        self.scripts: list[str] = []
        self.takes = ""  # the option whose value is the next word: "e", "f", "l" (line length), or "" for none

    def add(self, kind: str, value: str) -> None:
        if kind == "e":
            self.scripts.append(value)
        elif kind == "f":
            self.script_file = True

    def long(self, a: str) -> None:
        name, eq, value = a.partition("=")
        if name == "--in-place":
            self.in_place = True
        self.quiet |= name in ("--quiet", "--silent")
        kind = {"--expression": "e", "--file": "f", "--line-length": "l"}.get(name, "")
        if kind and eq:
            self.add(kind, value)
        elif kind:
            self.takes = kind  # the value is the next word

    def short(self, a: str) -> None:
        """A cluster of short options: -n, -ne, -nE, -i.bak, -e1p."""
        for i, c in enumerate(a[1:], 1):
            if c == "i":
                self.in_place = True
                return
            self.quiet |= c == "n"
            if c in "efl":
                if i < len(a) - 1:  # attached: -e1p
                    self.add(c, a[i + 1:])
                else:
                    self.takes = c  # the value is the next word
                return


def _sed_files(args: list[str]) -> list[str]:
    """Files of ``sed -n <script> files``, for a script that only prints (``p`` with line addresses). Nothing for an
    in-place edit, a run without ``-n``, a script file (not read), or any other script (``d``, ``w``, ``s``...)."""
    scan = _SedScan()
    positional = _sed_positional(scan, args)
    if positional is None or not scan.quiet or scan.script_file:
        return []
    if scan.scripts:
        scripts, files = scan.scripts, positional
    elif positional:
        scripts, files = positional[:1], positional[1:]
    else:
        return []
    if not all(_print_only(script) for script in scripts):
        return []  # not a print-only script: its files are not read as printed
    return [f for f in files if f != "-"]  # `-` is stdin: the script still counts it as a position


def _sed_positional(scan: _SedScan, args: list[str]) -> list[str] | None:
    """The operands of a sed command line, its options read into ``scan``; None for an in-place edit."""
    ended = False
    positional: list[str] = []
    for a in args:
        if scan.takes:
            scan.add(scan.takes, a)
            scan.takes = ""
        elif ended or not a.startswith("-") or a == "-":
            positional.append(a)
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            scan.long(a)
        else:
            scan.short(a)
        if scan.in_place:
            return None
    return positional


def _print_only(script: str) -> bool:
    return all(not piece.strip() or _PRINT_ONLY.fullmatch(piece) for piece in re.split(r"[;\n]", script))


def _bare(part: str) -> str:
    """``part`` with the text inside quotes emptied, so only shell syntax is left to match."""
    return re.sub(r"'[^']*'", "''", re.sub(r'"[^"]*"', '""', part))


def _outside_quotes(part: str, rx: re.Pattern[str]) -> str:
    """``part`` with each match of ``rx`` removed, matched with the text inside quotes masked: a match
    starts outside quotes, and a quote glued to it (``'a'2>x``, ``2>x'y'``) is part of its word."""
    masked = re.sub(r"\\.|'[^']*'|\"(?:\\.|[^\"\\])*\"",  # a backslash escape is masked too: ``\"`` opens no quote
                    lambda m: "qq" if m.group()[0] == "\\" else m.group()[0] + "q" * (len(m.group()) - 2) + m.group()[-1],
                    part, flags=re.DOTALL)
    out, last = [], 0
    for m in rx.finditer(masked):
        out += [part[last:m.start()], " "]
        last = m.end()
    return "".join(out) + part[last:]


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
    return out


def _codex_apply(out: Transcript, rec: dict[str, Any], messages: list[str], workdir: str | None,
                  by_id: dict[str, dict[str, Any]] | None = None, cwd: str = "",
                  event_reads: dict[int, _Derived] | None = None) -> None:
    if by_id is None:
        by_id = {}
    if event_reads is None:
        event_reads = {}
    kind = rec.get("type")
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


def _codex_usage(out: Transcript, usage: dict[str, Any]) -> None:
    out.num_turns = (out.num_turns or 0) + 1
    out.result_seen = True  # a turn ended: the agent started, whatever else it printed
    fresh, produced = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    cached = max(int(usage.get("cached_input_tokens") or 0), 0)  # a negative count is malformed, not a credit
    if fresh or produced:  # a missing or all-zero usage is not a measured zero: the counts stay None
        # OpenAI counts the cached part inside input_tokens; Claude cells keep cache reads out of input_tokens.
        # A malformed record with more cached than input never goes negative
        out.input_tokens = (out.input_tokens or 0) + max(fresh - cached, 0)
        out.output_tokens = (out.output_tokens or 0) + produced
        if usage.get("cached_input_tokens") is not None:  # reported, so a zero is measured; absent, it stays None
            out.cache_read_tokens = (out.cache_read_tokens or 0) + cached
        out.usage_scope = "main"  # the turns are the parent's; Codex reports no subagent tokens here


def _codex_key(item: dict[str, Any], script: str) -> str:
    # Use item.id if available (handles overlapping commands with same text), fallback to text
    item_id = item.get("id")
    return str(item_id) if item_id else f"text:{script}"


def _codex_command_started(out: Transcript, item: dict[str, Any], workdir: str | None,
                           by_id: dict[str, dict[str, Any]], cwd: str,
                           event_reads: dict[int, _Derived]) -> None:
    script = unwrap_shell(str(item.get("command") or ""))
    paths, unknown = _counted_reads(out, script, cwd)
    read_events = [{"t": None, "kind": "read", "path": relative_path(path, workdir)} for path in paths]
    out.events.extend(read_events)
    event: dict[str, Any] = {"t": None, "kind": "command", "text": script, "rc": None}
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
    derived, unknown = event_reads.get(id(event), ([], 0))
    if _failed_read(str(event["text"]), event["rc"]):  # drop the read events derived from it, and its unknown ones
        out.unknown_reads -= unknown
        dropped = {id(e) for e in derived}
        out.events[:] = [e for e in out.events if id(e) not in dropped]


def _codex_command_unstarted(out: Transcript, item: dict[str, Any], script: str, workdir: str | None,
                             cwd: str) -> None:
    """Fallback for item.completed without item.started."""
    paths, _, unknown = shell_walk(script, cwd)
    if not _failed_read(script, item.get("exit_code")):
        out.unknown_reads += unknown
        for path in paths:
            out.events.append({"t": None, "kind": "read", "path": relative_path(path, workdir)})
    event = {"t": None, "kind": "command", "text": script, "rc": item.get("exit_code")}
    if item.get("aggregated_output"):
        event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
    out.events.append(event)


_Derived = tuple[list[dict[str, Any]], int]  # a command's read events, and how many files it named at a directory not known


def _counted_reads(out: Transcript, script: str, cwd: str) -> tuple[list[str], int]:
    """:func:`shell_reads`, and the files it named at a directory not known, which ``out.unknown_reads`` counts
    until the command's result says it failed (:func:`_failed_read`)."""
    reads, _, unknown = shell_walk(script, cwd)
    out.unknown_reads += unknown
    return reads, unknown


def _failed_read(script: str, rc: Any) -> bool:
    """A non-zero exit of a command that is one part naming one file, alone or after ``cd`` parts
    (``cd sub && cat AGENTS.md``: the cd or the cat failed): that file was not read, known or not
    (``cd -; cat missing``: no read, and no ``unknown_reads``).

    Otherwise the exit code does not say which read failed (``cat a missing`` prints ``a`` and
    exits 1), so the reads are kept: also when only one of the files is known (``cd -; cat missing /ws/a``). A part
    before it that is not surely a ``cd`` (``X="a b" cd sub``, ``time -p cd sub``) keeps them too: its exit may be
    the one that failed, and a read is dropped only when that file surely was not printed. A brace group's braces are
    not parts: ``{ cat AGENTS.md; }`` and ``cd sub && { cat AGENTS.md; }`` are one read.
    """
    parts = [p for p in (_ungrouped(part.text) for part in split_script(script)) if p]
    if rc in (None, 0) or not all(_is_cd(p) for p in parts[:-1]):
        return False
    return _walk_script(script, "")[1].operands == 1  # the printer's own operands, not the reads kept of them


def _ungrouped(text: str) -> str:
    """A part's ``text`` past the ``{`` that open brace groups in it; ``""`` for the ``}`` that closes one."""
    return "" if _GROUP_CLOSE.match(text) else re.sub(r"^(?:\{\s+)+", "", text)


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
        if kind == "assistant.turn_start":
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
        scripts = _counted_reads(out, command, cwd)[0] if name == "bash" else []
        reads = [{"t": t, "kind": "read", "path": relative_path(p, workdir)} for p in scripts]
        return [*reads, {"t": t, "kind": "command", "text": command, "rc": None}]
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
    if failed:
        out.unknown_reads -= unknown
        dropped = {id(e) for e in reads}
        out.events[:] = [e for e in out.events if id(e) not in dropped]

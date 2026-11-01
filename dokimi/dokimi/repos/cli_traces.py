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

from .transcript import Transcript, _parse_time, clip_output, relative_path

_BASH_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -lc (.+)$", re.S)
_PRINTERS = {"cat", "head", "tail", "nl", "sed"}
_COPILOT_SHELL = {"bash", "powershell"}
_COPILOT_READ = {"view"}
_COPILOT_EDIT = {"edit", "create", "str_replace_editor", "write"}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
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
_SYSTEM_BIN = {"/bin", "/usr/bin", "/usr/local/bin"}
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")  # a NAME=value word: a variable set for the command that follows
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}
_DIR_STACK = {"pushd", "popd"}
_DIRS = {"cd"} | _DIR_STACK  # the builtins that change the directory
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
    ``pushd`` / ``popd``, ``env -C`` / ``--chdir`` (the command's reads, but absolute ones), a ``cd``
    after ``if`` / ``then`` / ``do`` / ``else`` (it may not run), a ``cd`` after ``&&`` / ``||`` once the chain
    holding it ends with ``;``, ``&`` or a newline (it may have been skipped), or a glob or brace expansion (the
    files are not known). A subshell ``( ... )`` or `` `...` `` records no reads: its directory is not kept. A brace
    group or an ``&&`` / ``||`` list that is backgrounded, or a piped group, runs in a subshell, so the directory is
    restored after it; the reads of a
    group whose output feeds a pipe after it is dropped, as a pipeline is not a read here; a group whose stdout is
    redirected to a file or /dev/null is dropped, as that output is not shown to the agent. ``--help`` and
    ``--version`` print and open no file. A function definition is skipped whole. Absolute paths are kept.
    """
    out: list[str] = []
    walk = _Walk(posixpath.normpath(cwd) if cwd else "")
    previous = ""
    # a line continuation is removed, not made a space (``cat AGENTS\<newline>.md`` reads AGENTS.md); a heredoc body
    # is input, not commands
    script = _HEREDOC.sub(lambda m: m.group(3), _CONTINUATION.sub("", script))
    for part, after, in_subshell in _split_script(script):
        op, previous = previous, after
        chained = op in ("&&", "||")
        if not chained and walk.gated:  # a cd after && or || may not have run: the next chain starts in no known directory
            walk.lose()
            walk.gated = False
        walk.chain(op, chained)
        if walk.in_function(_bare(part)):  # a definition runs nothing
            continue
        if op == "||" and walk.moved:
            walk.here = None
        if in_subshell:  # a subshell: its reads are not tracked
            if _runs_dir_before_parens(part):  # ``cd $(dirname f)``: the cd is outside the parentheses
                walk.lose()
            continue
        walk.track_groups(_bare(part), after, out)
        words = walk.words(part)
        if words:
            out.extend(walk.command(words, after, chained))
    return out


class _Walk:
    """Where ``shell_reads`` is in the script: the directory (``None``: unknown), whether a ``cd`` was seen, and
    the open brace groups as (directory before it, in a pipeline)."""

    def __init__(self, here: str | None) -> None:
        self.here = here
        self.moved = False  # a cd was seen: after ||, the directory is not known
        self.gated = False  # a cd after && or || was seen in this chain: it may not have run for the next chain
        # the open brace groups: (directory before it, in a pipeline, index in the reads where it began)
        self.groups: list[tuple[str | None, bool, int]] = []
        self.list_start: str | None = here  # the directory where the current && / || list began
        self.listed = False  # that list has more than one command
        self.function = 0  # the brace depth inside a function definition

    def chain(self, op: str, chained: bool) -> None:
        """A list of more than one command ended by ``&`` ran in a subshell, so this shell did not move: ``cd sub &&
        cat f & cat g`` reads sub/f and g (a lone ``cd x &`` and a ``{ ...; } &`` group are handled where they end)."""
        if op == "&" and self.listed:
            self.here = self.list_start
        if chained:
            self.listed = True
        else:
            self.list_start, self.listed = self.here, False

    def in_function(self, bare: str) -> bool:
        """Whether ``bare`` is part of a function definition (``f() { cd x; cat a; }``): its body is skipped, as
        defining it runs nothing (a later call is not followed)."""
        if not self.function and not _FUNCTION_DEF.match(bare):
            return False
        self.function = max(self.function + len(_GROUP_OPEN.findall(bare)) - len(_GROUP_CLOSE.findall(bare)), 0)
        return True

    def lose(self) -> None:
        self.here, self.moved = None, True

    def track_groups(self, bare: str, after: str, out: list[str]) -> None:
        """A brace group opening or closing in ``bare``; one that ran in a subshell restores the directory. The
        reads of a group whose output feeds a pipe after it, or a file, are dropped from ``out``: its output is not
        the agent's."""
        opened = _GROUP_OPEN.search(bare)
        if opened:
            piped = bool(_PIPE.search(bare[:opened.start()]))
            self.groups.append((self.here, piped, len(out)))
            if piped:  # the group runs in a pipeline's subshell: where it ends up is not tracked
                self.here = None
        closed = _GROUP_CLOSE.search(bare)
        if closed and self.groups:
            before, piped, mark = self.groups.pop()
            after_close = bare[closed.start():]
            redirects = bare[closed.end():]
            if _PIPE.search(after_close) or _STDOUT_FILE.search(redirects) or _STDOUT_AFTER_STDERR.search(redirects):
                del out[mark:]
            if piped or _PIPE.search(after_close):  # the group ran in a pipeline's subshell
                self.here = before
            elif after == "&":  # the group ran in a subshell
                self.here = before

    def words(self, part: str) -> list[str]:
        """The words of ``part``; none when it cannot be split or is computed (a computed ``cd`` loses the directory)."""
        if not _STDOUT_AFTER_STDERR.search(_bare(part)):  # else ``>&2`` stays: a redirect, so no read
            part = _outside_quotes(part, _HARMLESS_REDIRECT)
        part = _outside_quotes(part, _STDERR_FILE)
        try:
            words = shlex.split(part) if not _computed(part) else None
        except ValueError:
            return []
        if words is None:
            rough = part.split()
            while rough and (rough[0] in _KEYWORDS or rough[0] == "{"):
                rough = rough[1:]
            rough, via_env, _ = _command_words(rough)
            if rough[:1] and rough[0] in _DIRS and not via_env:  # ``builtin cd "$D"``: where it went is not known
                self.lose()
            return []
        return words[1:] if words[:1] == ["{"] else words  # a brace group runs in this shell: its cd and reads count

    def command(self, words: list[str], after: str, chained: bool = False) -> list[str]:
        """Move the directory for a ``cd``; the files a printing command reads. ``chained``: the command follows
        ``&&`` or ``||``, so a ``cd`` in it runs only if the command before it succeeded."""
        conditional = False
        while words and words[0] in _KEYWORDS:  # ``then cd x``: the cd may not run
            words, conditional = words[1:], True
        words, via_env, chdir = _command_words(words)
        if not words:
            return []
        name = _run_name(words[0], via_env)
        if name is None:
            return []
        if name in _DIR_STACK or (conditional and name == "cd"):
            self.lose()
        elif name == "cd":
            self.moved = True
            self.gated |= chained
            if after != "&":  # a backgrounded cd runs in a subshell: this shell stays where it was
                self.here = _cd(self.here, words[1:])
        elif name in _PRINTERS:
            if _asks_for_help(words[1:]):  # ``cat --help AGENTS.md`` prints the help and opens no file
                return []
            files = _sed_files(words[1:]) if name == "sed" else _print_files(name, words[1:])
            # ``env -C dir cat f`` reads dir/f, which is not known: only an absolute path is the same
            return [self.resolve(f) for f in files if f.startswith("/") or (self.here is not None and not chdir)]
        return []

    def resolve(self, f: str) -> str:
        return posixpath.normpath(f if f.startswith("/") else posixpath.join(self.here or "", f))


def _run_name(name: str, via_env: bool) -> str | None:
    """The name a command runs as, or None when it is a ``cd`` or directory-stack builtin that does not run."""
    if via_env and (name == "cd" or name in _DIR_STACK):  # ``env cd x`` runs no builtin: nothing moves
        return None
    if "/" in name:  # ``/bin/cat`` is cat; ``./cat`` or ``/bin/cd`` is some other program
        name = posixpath.basename(name) if posixpath.dirname(name) in _SYSTEM_BIN else ""
        if name == "cd" or name in _DIR_STACK:
            return None
    return name


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


def _runs_dir_before_parens(part: str) -> bool:
    """Whether the command text before its first parenthesis runs a directory builtin, as ``cd $(...)`` does
    (a subshell ``(cd x; ...)`` has no such text, so it changes nothing outside itself)."""
    words = re.split(r"[()`]", part, maxsplit=1)[0].split()
    while words and (words[0] in _KEYWORDS or words[0] == "{"):
        words = words[1:]
    words, via_env, _ = _command_words(words)
    return bool(words) and words[0] in _DIRS and not via_env


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


def _program(word: str) -> str:
    """The program a word names when it is a bare name or a system path (``env``, ``/usr/bin/env``); ``""`` for a
    path elsewhere (``./env``), which is some program of the repository, not the one the system has."""
    return posixpath.basename(word) if posixpath.dirname(word) in ("", *_SYSTEM_BIN) else ""


def _command_words(words: list[str]) -> tuple[list[str], bool, bool]:
    """The words from the command's name on; whether ``env`` runs it; and whether that ``env`` changes
    directory (``-C`` / ``--chdir``). ``NAME=value`` words, ``builtin`` or ``command`` before a directory
    builtin (which runs it), and ``env`` with its options and assignments come before the name and are skipped."""
    via_env = chdir = False
    while words:
        if _ASSIGN.match(words[0]):
            words = words[1:]
        elif words[0] in ("builtin", "command") and len(words) > 1 and words[1] in _DIRS:  # ``./command`` is a program
            words = words[1:]
        elif _program(words[0]) == "env":
            words, via_env = words[1:], True
            while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
                opt = words[0]
                chdir |= opt.startswith(("-C", "--chdir"))
                words = words[2:] if opt in ("-C", "-u", "--chdir", "--unset") else words[1:]  # these take the next word as a value
        else:
            break
    return words, via_env, chdir


def _split_script(script: str) -> list[tuple[str, str, bool]]:
    """``(part, operator after it, in a subshell)`` for each part of ``script`` split at unquoted ``&&``,
    ``||``, ``;``, ``&`` and newlines (the operator is ``""`` after the last part). A ``#`` that starts a
    word begins a comment, which runs to the end of the line and is dropped. A part inside ``( ... )``,
    or holding a parenthesis, is in a subshell."""
    splitter = _Splitter(script)
    i = 0
    while i < len(script):
        i = splitter.step(i)
    splitter.flush("")
    return splitter.parts


class _Splitter:
    """The state of ``_split_script``: the parts so far, the one being built, the open quote and parentheses."""

    def __init__(self, script: str) -> None:
        self.script = script
        self.parts: list[tuple[str, str, bool]] = []
        self.cur: list[str] = []
        self.quote = ""
        self.depth = 0  # open parentheses
        self.paren = False  # the part being built holds a parenthesis
        self.tick = False  # inside a backtick substitution, a subshell as ``$( )`` is

    def flush(self, op: str) -> None:
        """End the part being built at ``op``."""
        text = "".join(self.cur).strip()
        if text:
            self.parts.append((text, op if op != "\n" else ";", self.paren or self.depth > 0 or self.tick))
        self.cur = []
        self.paren = self.depth > 0 or self.tick

    def step(self, i: int) -> int:
        """Take the text at ``i``; the index to continue at."""
        script, c = self.script, self.script[i]
        if self.quote:
            return self._quoted(i)
        if c in "'\"":
            self.quote = c
            self.cur.append(c)
        elif c == "\\" and i + 1 < len(script):
            self.cur.append(c + script[i + 1])
            return i + 2
        elif c == "#" and (i == 0 or script[i - 1] in " \t\n;&|()"):
            end = script.find("\n", i)  # the comment ends at the newline, which still splits
            return len(script) if end < 0 else end
        elif c == "`":
            self.paren, self.tick = True, not self.tick
            self.cur.append(c)
        elif c in "()":
            self.paren = True
            self.depth = max(0, self.depth + (1 if c == "(" else -1))
            self.cur.append(c)
        else:
            return self._operator(i)
        return i + 1

    def _quoted(self, i: int) -> int:
        script, c = self.script, self.script[i]
        self.cur.append(c)
        if c == "\\" and self.quote == '"' and i + 1 < len(script):
            i += 1
            self.cur.append(script[i])
        elif c == self.quote:
            self.quote = ""
        return i + 1

    def _operator(self, i: int) -> int:
        script, c = self.script, self.script[i]
        two = script[i:i + 2]
        op = two if two in ("&&", "||") else c if c in ";\n" else ""
        if c == "&" and not op and script[i - 1:i] not in (">", "<") and script[i + 1:i + 2] != ">":
            op = "&"
        if not op:
            self.cur.append(c)
            return i + 1
        self.flush(op)
        return i + len(op)


def codex_json(raw: str, workdir: str | None, cwd: str = "") -> Transcript:
    out = Transcript()
    messages: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}  # Track started items by ID to update on completion
    event_reads: dict[int, list[dict[str, Any]]] = {}  # command event -> the read events derived from it
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
                  event_reads: dict[int, list[dict[str, Any]]] | None = None) -> None:
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
                           event_reads: dict[int, list[dict[str, Any]]]) -> None:
    script = unwrap_shell(str(item.get("command") or ""))
    read_events = [{"t": None, "kind": "read", "path": relative_path(path, workdir)}
                   for path in shell_reads(script, cwd)]
    out.events.extend(read_events)
    event: dict[str, Any] = {"t": None, "kind": "command", "text": script, "rc": None}
    event_reads[id(event)] = read_events
    by_id[_codex_key(item, script)] = event
    out.events.append(event)


def _codex_item_completed(out: Transcript, item: dict[str, Any], messages: list[str], workdir: str | None,
                          by_id: dict[str, dict[str, Any]], cwd: str,
                          event_reads: dict[int, list[dict[str, Any]]]) -> None:
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
        changes = item.get("changes") or []
        # an entry that is not a change makes the record malformed: it is dropped whole (counted), not read in part
        if not isinstance(changes, list) or not all(isinstance(c, dict) and c.get("path") for c in changes):
            raise ValueError("file_change with a malformed changes entry")
        for change in changes:
            event: dict[str, Any] = {"t": None, "kind": "edit", "path": relative_path(str(change["path"]), workdir)}
            if rc is not None:
                event["rc"] = rc
            out.events.append(event)
    elif itype in ("mcp_tool_call", "web_search"):
        name = f"{item.get('server')}.{item.get('tool')}" if itype == "mcp_tool_call" else "web_search"
        out.events.append({"t": None, "kind": "tool", "name": name})


def _codex_command_finished(out: Transcript, item: dict[str, Any], event: dict[str, Any],
                            event_reads: dict[int, list[dict[str, Any]]]) -> None:
    """Update the event from item.started with completion data."""
    event["rc"] = item.get("exit_code")
    if item.get("aggregated_output"):
        event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
    derived = event_reads.get(id(event), [])
    if _failed_read(str(event["text"]), derived, event["rc"]):  # drop the read events derived from it
        dropped = {id(e) for e in derived}
        out.events[:] = [e for e in out.events if id(e) not in dropped]


def _codex_command_unstarted(out: Transcript, item: dict[str, Any], script: str, workdir: str | None,
                             cwd: str) -> None:
    """Fallback for item.completed without item.started."""
    paths = shell_reads(script, cwd)
    if not _failed_read(script, paths, item.get("exit_code")):
        for path in paths:
            out.events.append({"t": None, "kind": "read", "path": relative_path(path, workdir)})
    event = {"t": None, "kind": "command", "text": script, "rc": item.get("exit_code")}
    if item.get("aggregated_output"):
        event["output"], event["output_truncated"] = clip_output(str(item["aggregated_output"]))
    out.events.append(event)


def _failed_read(script: str, reads: list[Any], rc: Any) -> bool:
    """A non-zero exit of a command that is one part naming one file: that file was not read.

    Otherwise the exit code does not say which read failed (``cat a missing`` prints ``a`` and
    exits 1), so the reads are kept.
    """
    return rc not in (None, 0) and len(reads) == 1 and len(_split_script(script)) == 1


class _CopilotRun:
    """What ``copilot_json`` collects across the lines: the first timestamp, tool events by call id, the
    answer (the final one, else the last message) and the turn count."""

    def __init__(self) -> None:
        self.start: datetime | None = None
        self.by_id: dict[str, list[dict[str, Any]]] = {}
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
            events = _copilot_tool(data, t, workdir, cwd)
            out.events.extend(events)
            self.by_id[str(data.get("toolCallId"))] = events
        elif kind == "tool.execution_complete":
            _copilot_result(out, self.by_id.get(str(data.get("toolCallId")), []), data)
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


def _copilot_tool(data: dict[str, Any], t: float | None, workdir: str | None, cwd: str = "") -> list[dict[str, Any]]:
    name = str(data.get("toolName") or "")
    args = data.get("arguments")
    if name in _COPILOT_SHELL:
        command = str((args or {}).get("command", "")) if isinstance(args, dict) else ""
        # only bash is a POSIX script; a PowerShell command is not parsed for the files it reads
        scripts = shell_reads(command, cwd) if name == "bash" else []
        reads = [{"t": t, "kind": "read", "path": relative_path(p, workdir)} for p in scripts]
        return [*reads, {"t": t, "kind": "command", "text": command, "rc": None}]
    if name in _COPILOT_READ | _COPILOT_EDIT and isinstance(args, dict):
        path = str(args.get("path") or args.get("file_path") or "")
        if not path:  # a call without a path names no file
            return []
        return [{"t": t, "kind": "read" if name in _COPILOT_READ else "edit", "path": relative_path(path, workdir)}]
    if name == "apply_patch":
        text = args if isinstance(args, str) else str((args or {}).get("input", ""))
        return [{"t": t, "kind": "edit", "path": relative_path(p.strip(), workdir)} for p in _PATCH_FILE.findall(text)]
    return [{"t": t, "kind": "tool", "name": name}]


def _copilot_result(out: Transcript, events: list[dict[str, Any]], data: dict[str, Any]) -> None:
    """Fill in the completion; drop the reads of a failed ``view`` and of a failed one-file command."""
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
        failed = _failed_read(str(event["text"]), reads, event["rc"])
        result = data.get("result") or {}
        output = result.get("content") if isinstance(result, dict) else None
        if isinstance(output, str) and output:
            event["output"], event["output_truncated"] = clip_output(output)
    if failed and reads:
        dropped = {id(e) for e in reads}
        out.events[:] = [e for e in out.events if id(e) not in dropped]

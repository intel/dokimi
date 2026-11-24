# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The shell walker of the CLI traces: what a command a trace shows reads and searches (contract §4.1).

:func:`shell_walked` walks a script the way a shell would, part by part, and gives the files its printing commands
(``cat``, ``head``, ``tail``, ``sed -n``, ``nl``) name, the files its searches show lines of, and whether it may have left
its shell in another directory. :func:`shell_reads`, :func:`shell_reads_moved` and :func:`shell_walk` are narrower views of
it, and :func:`unwrap_shell` takes the script out of ``bash -lc '<script>'``. It holds the heredoc patterns that cut a
heredoc body out of a script before the walk. The Codex and Copilot parsers of :mod:`~dokimi.repos.cli_traces` call it;
this module does not import them.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Sequence
from typing import NamedTuple

from dokimi.text_spans import HeredocPattern

from .event_format import plain_path
from .shell_functions import Functions, define
from .shell_redirects import bare, closed_fds, copied_fds, masked, may_fail_redirect, without_redirects, without_stderr
from .shell_search import Searched, cuts_lines, passes_through, prints_nothing, search_program, searched_files
from .shell_sed import sed_files
from .shell_split import split_script
from .shell_words import (_ASSIGN, _DIRS, _KEYWORDS, _case_closes, _case_opens, _cd, _command_words, _loses, _past_time, _run_name,
                          _runs_dir, _time_keyword, _unquoted, _stdout_after_stderr)

_BASH_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -lc (.+)$", re.S)
_PRINTERS = {"cat", "head", "tail", "nl", "sed"}
_CONTINUATION = re.compile(r"\\\n")
_HEREDOC_DELIMITER = r"<<-?[ \t]*(['\"]?)(\w+)\1"
_HEREDOC = HeredocPattern(re.compile(_HEREDOC_DELIMITER + r"([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M), re.compile(_HEREDOC_DELIMITER),
                          re.compile(r"^[ \t]*(\w+)[ \t]*$", re.M), word=2, quote=1)
_GLOB = re.compile(r"[*?\[]|\{[^\s{}]*(?:,|\.\.)[^\s{}]*\}")  # a pathname or brace expansion, unquoted
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")  # a pipe, not ||
_GROUP_OPEN = re.compile(r"(?:^|[\s;&|(])\{(?=\s|$)")  # a brace group opens: ``{`` as a word
_GROUP_CLOSE = re.compile(r"(?:^|\s)\}(?=\s|$|[;&|])")
_FUNCTION_HEAD = re.compile(r"\s*(?:function\s+[^\s(){}]+(?:\s*\(\s*\))?|[^\s(){}=$`]+\s*\(\s*\))\s*")  # ``f()``, ``function f``
_FUNCTION_DEF = re.compile(_FUNCTION_HEAD.pattern + r"\{(?=\s|$)")  # a definition opening its body: ``f() {``, ``function f {``
# ``f() (`` or ``function f (`` (no ``()``: bash takes the ``(`` as the body's), a definition whose body is a subshell
_SUBSHELL_DEF = re.compile(r"\s*(?:function\s+)?[^\s(){}=$`]+\s*\(\s*\)\s*\(|\s*function\s+[^\s(){}]+\s*\((?!\s*\))")
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
    ``pushd`` / ``popd``, an ``eval``, ``source`` or ``.`` (it may run a ``cd``; not ``source .venv/bin/activate``,
    a virtualenv's activate script alone, assumed to run none; none of these when backgrounded or piped, as it then ran
    in a subshell: ``source env.sh & false || cat a`` reads a), a call of a function the script defines whose body names
    one of these or that has the name of one (``f() { cd sub; }; f``, ``cd() { ...; }; cd x``), a part the walker does not
    follow whose command word is one of these (``time -p cd sub``, ``X="a b" cd "$D"``: :func:`~.shell_words._may_run_dir`), a ``case``
    arm that names one (``case`` is not followed: its reads are not kept either; a ``case`` opens one only as a command
    word, not in ``echo case``), ``env -C`` / ``--chdir`` (the
    command's reads, but absolute ones), a ``cd`` after ``if`` / ``then`` / ``do`` / ``else`` (it may not run), a
    ``cd`` after ``&&`` / ``||`` once the chain holding it ends with ``;``, ``&`` or a newline (it may have been
    skipped), or a glob or brace expansion (the files are not known). A subshell ``( ... )`` or `` `...` `` records
    no reads, and a ``cd`` in it moves nothing here; a ``$( )`` or backtick's own ``;`` does not end the part around it
    (``cd $(x; y) | cat`` is piped). ``time cat a``, ``X=1 time cat a`` and ``command cat a`` read a (``time -x cat a``
    runs ``-x``; ``env`` runs a program, never a builtin, so ``env command cat a`` reads no file and ``env command cd x``
    moves no shell: both run a program named ``command``); a call of a function the script defines is no read (``cat() { echo hi; }; cat a``, ``\\cat a``,
    ``cat() ( ... )`` and ``function cat ( ... )`` too), nor what follows a function named like a wrapper (``command() { ...; }; command cat a``),
    and ``command cd x`` or ``builtin cd x`` runs the builtin past a function ``cd``. A brace group (``{ cd $(pwd); }``,
    ``{ { cd x; }; }`` too) or an ``&&`` / ``||`` list that is backgrounded, or a piped
    group, runs in a subshell, so the directory is restored after it; the reads of a
    group whose output feeds a pipe after it is dropped, as a pipeline is not a read here; a group whose stdout is
    redirected to a file or /dev/null is dropped, as that output is not shown to the agent. ``--help`` and
    ``--version`` print and open no file. A function definition is skipped whole, its ``{`` on a later line too
    (``f()\\n{``). Absolute paths are kept.
    """
    return shell_walked(script, cwd).reads


def shell_reads_moved(script: str, cwd: str = "") -> tuple[list[str], bool]:
    """:func:`shell_reads`, and whether the script may leave its shell in another directory: it ran a ``cd``,
    ``pushd``, ``popd``, ``eval``, ``source`` or ``.`` in that shell, not in a subshell (``( ... )``, a pipeline
    or a backgrounded command, list or group: ``cd sub &``, ``{ cd sub; } | cat``, ``cd sub && make &``,
    ``cd "$X" &``, ``cd sub | cat``, ``{ cd $(pwd); } &``), or called a function whose body names one of these, or
    that has the name of one (``f() { cd sub; }; f``, ``cd() { ...; }; cd x``; not ``f &``, ``f | cat``, nor a body
    in parentheses, ``f() ( cd sub ); f``, which runs in a subshell)."""
    return shell_walk(script, cwd)[:2]


def shell_walk(script: str, cwd: str = "") -> tuple[list[str], bool, int]:
    """:func:`shell_reads_moved`, and how many relative files its printing commands named at a directory not known."""
    walked = shell_walked(script, cwd)
    return walked.reads, walked.moved, walked.unknown


class Walked(NamedTuple):
    """What a script does that a trace shows: see :func:`shell_walked`."""

    reads: list[str]
    moved: bool
    unknown: int
    searched: list[str]


def shell_walked(script: str, cwd: str = "") -> Walked:
    """:func:`shell_walk`, and the files the script's ``grep`` / ``rg`` / ``git grep`` commands search and show lines of
    (:mod:`~dokimi.repos.shell_search`), with the same ``cd`` rules as the reads, from ``cwd``. A relative one at a
    directory not known is dropped, and is not in ``unknown``. A search is no read: ``reads`` holds none."""
    found, walk = _walk_script(script, cwd)
    return Walked([p for p in found if not isinstance(p, Searched)], walk.shell_moved, walk.unknown,
                  [str(p) for p in found if isinstance(p, Searched)])


def _walk_script(script: str, cwd: str) -> tuple[list[str], _Walk]:
    out: list[str] = []
    previous = ""
    # a line continuation is removed, not made a space (``cat AGENTS\<newline>.md`` reads AGENTS.md); a heredoc body
    # is input, not commands
    script = _HEREDOC.sub(lambda m: m.group(3), _CONTINUATION.sub("", script))
    known: Functions = {}
    walk = _Walk(plain_path(cwd) if cwd else "", define(known, script), known)
    for part, after, in_subshell, outer in split_script(script):
        op, previous = previous, after
        if _ungrouped(part).strip():  # the ``}`` that closes a group has no exit of its own: the group's last command's
            walk.last_start, walk.cut = len(out), False
        chained = op in ("&&", "||")
        if not chained and walk.gated:  # a cd after && or || may not have run: the next chain starts in no known directory
            walk.here, walk.gated = None, False
        walk.chain(op, chained)
        walk.ran = walk.runs(op)
        if walk.in_function(bare(part)) or walk.in_case(outer, part, after, out):  # a definition runs nothing
            continue
        if op == "||" and walk.moved:
            walk.here = None
        walk.track_groups(bare(outer), after, out)  # ``{ cd $(pwd); } | cat``: the group is in this shell
        if in_subshell:  # a subshell: its reads are not tracked
            walk.last_exit = None
            if _runs_dir(outer, walk.dirs, walk.functions):  # ``cd $(dirname f)``: the cd is outside the parentheses
                walk.lose(_in_own_shell(outer, after))
            continue
        words = walk.words(part, after)
        walk.track_state(part, words, after)  # ``export POSIXLY_CORRECT=1; head f -n 0``
        if words:
            out.extend(walk.command(words, after, chained, _time_keyword(part)))
        if not words and (stage := _search_stage(part, walk.functions)) is not None:  # ``grep x AGENTS.md | head``: the search's lines reach the agent
            walk.cut = True
            out.extend(walk.piped(walk.words(stage, after, staged=True), _time_keyword(part)))
    walk.chain(previous, False)  # a list backgrounded at the end (``cd sub && make &``) ran in a subshell too
    return out, walk


_PosixStates = frozenset[tuple[bool, bool, bool, bool]]  # (set in this shell, exported, allexport is on, readonly)
_POSIX_OFF: _PosixStates = frozenset({(False, False, False, False)})
_LITERAL_EXIT = {"true": True, ":": True, "false": False}  # the commands whose exit the walker knows
_POSIX_ASSIGN = ("POSIXLY_CORRECT=", "POSIXLY_CORRECT+=")  # an assignment word of the variable, appending or not
_STATE_BUILTINS = ("export", "unset", "set", "declare", "typeset")  # the builtins that change whether it is set or exported
_DECLARE_FLAGS = "Aagilprtux"  # the options of declare and typeset that leave the variable a variable
_DECLARE_OFF = "gilntux"  # the attributes ``declare +x`` can remove (not readonly or array: those it refuses)
_Spot = tuple[str | None, bool, bool, _PosixStates]  # (here, shell_moved, moved, posix): what a subshell's end restores


class _Walk:
    """Where ``shell_reads`` is in the script: the directory (``None``: unknown), whether a ``cd`` was seen, whether
    the script's own shell may have moved, and the open brace groups."""

    def __init__(self, here: str | None, movers: frozenset[str] = frozenset(), functions: Functions | None = None) -> None:
        self.here = here
        self.functions = {} if functions is None else functions  # the shell functions the script defines
        self.dirs = _DIRS | movers  # and those whose call may move the shell (``shell_functions.define``)
        self.moved = False  # a cd was seen in this shell: after ||, the directory is not known
        self.shell_moved = False  # a directory builtin ran in the script's shell, not in a subshell
        self.gated = False  # a cd after && or || was seen in this chain: it may not have run for the next chain
        # the open brace groups: the spot before it, in a pipeline, (index in the reads, ``unknown``) where it began,
        # the enclosing && / || list's (list_start, listed), which the group's own separators do not end
        self.groups: list[tuple[_Spot, bool, tuple[int, int, bool], tuple[_Spot, bool]]] = []
        self.list_start: _Spot = (here, False, False, _POSIX_OFF)  # the spot where the && / || list began
        self.listed = False  # that list has more than one command
        self.function = 0  # the brace depth inside a function definition
        self.head = False  # the last part was a function's name alone (``f()``): its body's ``{`` comes next
        self.case = 0  # the depth inside ``case ... esac``
        self.operands: int | None = None  # how many files the last printing command named
        self.passthru = False  # a search printed every line (``rg --passthru``): its exit 1 is no sign it showed none
        self.last_start = 0  # where in the found paths the script's last non-empty part began
        self.unknown = 0  # the relative files printing commands named at a directory not known
        self.cut = False  # the last part is a search piped into head or tail: its exit may be the pipeline's, under pipefail
        # the states ``POSIXLY_CORRECT`` may be in here, each (set in this shell, exported: every later command is run
        # with it): more than one when a command that changes it ran only perhaps
        self.posix = _POSIX_OFF
        self.last_exit: bool | None = None  # the last command was ``true``/``:`` (True) or ``false`` (False): else not known
        self.ran: bool | None = True  # whether the part being walked runs (None: not known), see :meth:`runs`
        self.copies: list[set[str]] = []  # for each open brace group, the descriptors its commands copy (``2>&1``)
        self.passed: list[set[tuple[bool, bool, bool, bool]]] = []  # for each open brace group, every state it was in so far
        self.gates: list[bool | None] = []  # for each open brace group, whether it runs: the group is one command of its list
        self.bodies: list[bool] = []  # for each open ``if`` / loop, whether its ``then`` / ``do`` body began: its commands may not run

    def runs(self, op: str) -> bool | None:
        """Whether the command after the operator ``op`` runs: not when the list or brace group holding it was skipped
        (:attr:`gates`); after ``&&`` or ``||`` it runs when the one before it exited 0 (``true``, ``:``) or not
        (``false``), and is skipped for the other; None when that is not known."""
        own: bool | None = True
        if op in ("&&", "||"):
            own = None if self.last_exit is None else self.last_exit == (op == "&&")
        gate = self.gates[-1] if self.gates else True
        if own is False or gate is False:
            return False
        return None if own is None or gate is None or any(self.bodies) else True

    def track_bodies(self, part: str) -> None:
        """The ``if`` / ``while`` / ``until`` / ``for`` / ``select`` that ``part`` opens and the ``fi`` / ``done`` that closes one;
        after its ``then`` or ``do`` a construct's body is every command up to its end, each of which may not run (:attr:`bodies`)."""
        for word in part.split():
            if word in ("if", "while", "until", "for", "select"):
                self.bodies.append(False)
            elif word in ("then", "do") and self.bodies:
                self.bodies[-1] = True
            elif word in ("fi", "done") and self.bodies:
                self.bodies.pop()
            elif word not in ("else", "elif", "!", "{", "time"):
                break

    def track_state(self, part: str, words: list[str], after: str) -> None:
        """The state a command of ``part`` sets (:meth:`track_posix`), also when a redirect leaves it no ``words``
        (``export POSIXLY_CORRECT=1 >/dev/null``: the redirect changes no variable); no command: its exit is not known."""
        self.track_bodies(part)
        for copied in self.copies:
            copied |= copied_fds(part)
        unredirected = without_redirects(part)
        state_words = words or (self.words(unredirected, after) if unredirected != part else [])
        if state_words == ["}"]:  # the ``}`` that closes a group has no exit of its own: the group's last command's
            return
        body = state_words
        while body and body[0] in _KEYWORDS:  # ``then export P=1``: the branch may not run, so both states stay
            body = body[1:]
        if body:
            self.track_posix(body, after, _time_keyword(part), may_fail_redirect(part) or body != state_words)
        else:
            self.last_exit = None

    def track_posix(self, words: list[str], after: str, timed: bool = False, perhaps: bool = False) -> None:
        """``POSIXLY_CORRECT`` after a command of this shell (not a job, ``after`` is ``&``; a pipeline's parts have no
        ``words``), by :func:`_posix_after`. A command that is skipped changes nothing (:attr:`ran`); one that may or may
        not run keeps both states (:attr:`posix`), as one with a redirect that may fail to open (``perhaps``: bash then
        does not run it). ``timed``: the part starts with the ``time`` keyword."""
        if after == "&":
            return
        ran = None if perhaps and self.ran else self.ran
        if ran is not False:
            after_it = frozenset(s for state in self.posix for s in _posix_after(words, self.functions, state, timed))
            self.posix = after_it if ran else self.posix | after_it
            for seen in self.passed:
                seen |= self.posix
            shadowed = words[0] in self.functions  # ``true() { false; }``: the function runs, its exit is not known
            self.last_exit = None if shadowed or not ran else _LITERAL_EXIT.get(" ".join(words))

    def spot(self) -> _Spot:
        return self.here, self.shell_moved, self.moved, self.posix

    def back(self, spot: _Spot) -> None:
        """Back to ``spot`` after a subshell: what ran in it moved neither this shell nor its ``||`` fallbacks."""
        self.here, self.shell_moved, self.moved, self.posix = spot

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
        defining it runs nothing (a later call of it loses the directory when its body names a directory builtin:
        :func:`~dokimi.repos.shell_functions.define`). The ``{`` may come on a later line (``f()\\n{``): blank lines and
        comments are no parts, so the part after a name alone opens the body. A body in parentheses (``cd() ( ... )``,
        ``function cd ( ... )``) is a subshell's, so its parts record nothing; the head is skipped, so a name ``cd`` there
        moves nothing, and a ``{`` word in it (``f() ( echo { ); cd sub``) opens no body that would hide the ``cd``."""
        head, self.head = self.head, False
        if not self.function and _SUBSHELL_DEF.match(bare):
            return True
        if not self.function and not _FUNCTION_DEF.match(bare) and not (head and _GROUP_OPEN.match(bare)):
            self.head = bool(_FUNCTION_HEAD.fullmatch(bare))
            return self.head
        self.function = max(self.function + len(_GROUP_OPEN.findall(bare)) - len(_GROUP_CLOSE.findall(bare)), 0)
        return True

    def in_case(self, outer: str, part: str, after: str, out: list[str]) -> bool:
        """Whether ``part`` is part of a ``case ... esac``, which is not followed: an arm may run or not, so one naming a
        directory builtin or a function that does (as any word of ``outer``: ``case x in a) cd sub;; esac``) may move
        the shell, and its reads are not kept. A ``case`` command word opens one (:func:`_case_opens`), a first ``esac``
        closes it; its braces still open and close groups."""
        opens = _case_opens(part)
        if not self.case and not opens:
            return False
        self.case += opens
        self.track_groups(bare(outer), after, out)  # before lose: a backgrounded group around the case restores it
        if any(w in self.dirs for w in _unquoted(outer.split())):
            self.lose()
        self.case -= _case_closes(part)
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
        ``unknown`` and the ``passthru`` it set: its output is not the agent's. Each brace of ``bare`` counts, in order (``{ { cd x``,
        ``} | { cat a``)."""
        braces = [(m.end() - 1, m) for m in _GROUP_OPEN.finditer(bare)] + [(m.end(), m) for m in _GROUP_CLOSE.finditer(bare)]
        for _, m in sorted(braces, key=lambda b: b[0]):
            if m.re is _GROUP_OPEN:
                self.open_group(bare[:m.end() - 1], len(out))
            elif self.groups:
                self.close_group(bare[m.start():], bare[m.end():], after, out)

    def open_group(self, before_brace: str, mark: int) -> None:
        piped = bool(_PIPE.search(before_brace))
        self.groups.append((self.spot(), piped, (mark, self.unknown, self.passthru), (self.list_start, self.listed)))
        self.copies.append(set())  # the descriptors the commands of the group copy
        self.passed.append(set(self.posix))
        self.gates.append(self.ran)  # ``false && { :; unset X; }``: every command of the group is skipped
        if piped:  # the group runs in a pipeline's subshell: where it ends up is not tracked
            self.here = None

    def close_group(self, after_close: str, redirects: str, after: str, out: list[str]) -> None:
        before, piped, (mark, unknown, passthru), (self.list_start, self.listed) = self.groups.pop()
        self.gates.pop()
        copied, passed = self.copies.pop(), self.passed.pop()
        for outer in self.copies:  # ``{ { :; } 2>&1; } 1>&-``
            outer |= copied_fds(redirects)
        # ``{ unset X; } </missing``: bash opens it first, and runs no body when that fails; so when it closes a descriptor
        # a command of the body copies (``{ unset X 2>&1; } 1>&-``), that command fails and the ones before it ran
        if may_fail_redirect(redirects) or closed_fds(redirects) & copied:
            self.posix |= before[3] | (passed if closed_fds(redirects) & copied else set())
            self.last_exit = None
        if _PIPE.search(after_close) or _STDOUT_FILE.search(redirects) or _stdout_after_stderr(redirects):
            del out[mark:]
            self.unknown, self.passthru = unknown, passthru  # a passthru search dropped with the group shows the agent no line
        if piped or _PIPE.search(after_close) or after == "&":  # the group ran in a pipeline's or a job's subshell
            self.back(before)

    def words(self, part: str, after: str = "", staged: bool = False) -> list[str]:
        """The words of ``part``; none when it cannot be split or is computed (a computed ``cd`` loses the directory,
        and moves this shell unless ``part`` is a pipeline or is backgrounded by ``after``: then it ran in a subshell;
        ``staged``: ``part`` is the first stage of a pipeline, cut out of it, so it ran in a subshell)."""
        part = without_stderr(part)
        try:
            words = shlex.split(part) if not _computed(part) else None
        except ValueError:
            return []
        if words is None:
            if _runs_dir(part, self.dirs, self.functions):  # ``builtin cd "$D"``: where it went is not known
                self.lose(_in_own_shell(part, after) and not staged)
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
        before = words
        words, via_env, chdir, bypassed = _command_words(words, timed, self.functions)
        if not words:
            return []
        name = _run_name(words[0], via_env)
        if name is None:
            return []
        skipped = before[:len(before) - len(words)]  # ``POSIXLY_CORRECT=1 head``
        posix = frozenset(_posix_set(skipped, is_set and exported) for is_set, exported, _, _ in self.posix)
        if words[0] in self.functions and not bypassed:  # ``cat() { ...; }; \cat a`` runs the function: no read
            if words[0] in self.dirs:  # a ``cd() { ...; }``, or a body naming one: definitions are found script-wide
                self.lose(after != "&")
        elif _loses(name, words, conditional, timed, via_env):
            self.lose(after != "&")
        elif name == "cd":
            self.gated |= chained
            if after != "&":  # a backgrounded cd runs in a subshell: this shell stays where it was
                self.here, self.shell_moved, self.moved = _cd(self.here, words[1:]), True, True
        elif not negated:  # ``! cat f`` inverts the exit, which no longer says f was printed (nor ``! grep``, searched)
            return self.shown(name, words[1:], chdir, posix)
        return []

    def shown(self, name: str, args: list[str], chdir: bool, posix: frozenset[bool] = frozenset({False})) -> list[str]:
        """The files a printing command prints, or a search shows lines of, as a path list (:class:`Searched` for a search)."""
        if name in _PRINTERS:
            return self.printed(name, args, chdir, posix)
        search = search_program(name, args)
        return self.searching(*search, chdir) if search else []

    def piped(self, words: list[str], timed: bool) -> list[str]:
        """The files a search shows lines of when it is the first stage of ``grep x f | head`` (:func:`_search_stage`);
        no other command of a pipeline counts, and neither does a ``cd``. A keyword before the search (``if grep x f | head``)
        is dropped, but not ``!``, which inverts the exit."""
        while words and words[0] in _KEYWORDS:
            if words[0] == "!":
                return []
            words = words[1:]
        words, via_env, chdir, bypassed = _command_words(words, timed, self.functions)
        name = _run_name(words[0], via_env) if words else None
        search = search_program(name, words[1:]) if name and (bypassed or words[0] not in self.functions) else None
        return self.searching(*search, chdir) if search else []

    def printed(self, name: str, args: list[str], chdir: bool, posix: frozenset[bool] = frozenset({False})) -> list[str]:
        """The files a printing command reads; ``unknown`` counts those it names at a directory not known. ``posix``: whether
        the command may be run with ``POSIXLY_CORRECT`` set (True), unset (False), or either, when it is not known."""
        if _asks_for_help(args):  # ``cat --help AGENTS.md`` prints the help and opens no file
            return []
        if all(prints_nothing(name, args, p) for p in posix):  # ``head -n 0 AGENTS.md`` prints no line in each state
            return []
        files = sed_files(args) if name == "sed" else _print_files(name, args)
        known = self.named(files, chdir)
        self.unknown += len(files) - len(known)
        return known

    def searching(self, program: str, args: Sequence[str], chdir: bool) -> list[str]:
        """The files a search shows lines of (:func:`~dokimi.repos.shell_search.searched_files`), as :class:`Searched`
        paths among the reads. One at a directory not known is dropped, and not counted in ``unknown``, which counts reads."""
        files = searched_files(program, args)
        self.passthru |= bool(files) and passes_through(program, args)  # ``rg --passthru -q`` shows no line
        return [Searched(p) for p in self.named(files, chdir)]

    def named(self, files: list[str], chdir: bool) -> list[str]:
        """The files a printing or searching command names that are known, resolved; ``operands`` counts all of them."""
        self.operands = len(files)  # all it names, those at an unknown directory too
        # ``env -C dir cat f`` reads dir/f, which is not known: only an absolute path is the same
        return [self.resolve(f) for f in files if f.startswith("/") or (self.here is not None and not chdir)]

    def resolve(self, f: str) -> str:
        return plain_path(f if f.startswith("/") else posixpath.join(self.here or "", f))


def _posix_after(words: list[str], functions: Functions, state: tuple[bool, bool, bool, bool], timed: bool = False) -> _PosixStates:
    """The states ``state`` (``POSIXLY_CORRECT`` set in this shell, marked for export: the command gets it when both) may be
    in after the command ``words`` ran in it: a lone ``POSIXLY_CORRECT=1`` (or ``+=``) sets it, ``export NAME[=value]`` marks
    it for export (a bare ``export NAME`` too before it has a value), ``export -n NAME[=value]`` assigns the value and drops
    the mark, ``declare -x`` and ``typeset -x`` export, ``declare NAME=value`` sets, ``set -a`` exports what is assigned after it
    (until ``set +a``, also past an ``unset``), and ``unset NAME`` drops both (``unset NAME=1`` is refused).
    ``export -f`` and ``unset -f`` name functions, so they leave it. ``builtin`` and ``command``
    (with ``--`` or ``command -p``) before ``export`` or ``unset`` run the builtin; a function of that name shadows it otherwise.
    ``NAME=value`` words and the ``time`` keyword (``timed``) before the command are skipped (``X=1 export P=1``,
    ``time export P=1`` run it here); ``env`` or /usr/bin/time before it run a child, which changes nothing here."""
    name, is_set, exported, allexport, readonly = "POSIXLY_CORRECT", *state
    if all(_ASSIGN.match(w) for w in words):
        valued = any(w.startswith(_POSIX_ASSIGN) for w in words) and not readonly  # a readonly variable refuses a value
        return frozenset({(is_set or valued, exported or (allexport and valued), allexport, readonly)})
    words = _builtin_run(words, functions, timed)
    if not words:
        return frozenset({state})
    builtin = words[0]
    flags, plus, operands = _builtin_options(words[1:], builtin in ("declare", "typeset"))
    if builtin == "set":  # ``set -a``: what is assigned after it is exported, whether or not it was unset in between
        return frozenset({(is_set, exported, _allexport(words[1:], allexport), readonly)})
    if builtin in ("declare", "typeset") and "p" in flags:  # ``declare -p NAME`` shows it, whatever other options come with it
        return frozenset({state})
    if set(flags) - set({"export": "fnp", "unset": "fnv"}.get(builtin, _DECLARE_FLAGS)) or set(plus) - set(_DECLARE_OFF):  # an option it refuses: no change
        return frozenset({state})
    names = operands if builtin == "unset" else [w.partition("=")[0].removesuffix("+") for w in operands]  # ``unset NAME=1`` fails
    if "f" in flags or name not in names:
        return frozenset({state})
    if builtin == "unset":  # a readonly variable is not unset
        return frozenset({state if readonly else (False, False, allexport, False)})
    valued = any(w.startswith(_POSIX_ASSIGN) for w in operands) and not readonly  # a readonly variable refuses a value
    readonly = readonly or "r" in flags  # ``declare -r NAME``
    if "n" in flags or "x" in plus:  # ``declare +x NAME`` drops the mark, as ``export -n``
        return frozenset({(is_set or valued, False, allexport, readonly)})
    if builtin != "export" and "x" not in flags:  # ``declare NAME=1`` sets it in this shell, and exports it only under allexport
        return frozenset({(is_set or valued, exported or (allexport and valued), allexport, readonly)})
    return frozenset({(True, True, allexport, readonly) if valued else (is_set, True, allexport, readonly)})


def _allexport(args: list[str], current: bool) -> bool:
    """Whether the allexport option is on after ``set args``: ``-a`` and ``-o allexport`` turn it on, ``+a`` and
    ``+o allexport`` off, the last one wins; the options end at the first word that is no option, or at ``--``."""
    on, i = current, 0
    while i < len(args) and args[i][:1] in ("-", "+") and len(args[i]) > 1 and args[i] != "--":
        letters, sign, i = args[i][1:], args[i][0] == "-", i + 1
        on = sign if "a" in letters else on
        if "o" in letters:  # ``-o NAME`` takes the next word, whichever option it names
            on = sign if args[i:i + 1] == ["allexport"] else on
            i += 1
    return on


def _builtin_run(words: list[str], functions: Functions, timed: bool) -> list[str]:
    """``words`` from the ``export`` or ``unset`` the command runs in this shell, past ``NAME=value`` words, the ``time``
    keyword and ``builtin`` / ``command`` (which run the builtin here, also past a function of their own name once an outer
    one has bypassed the functions); empty when it runs no such builtin."""
    while _ASSIGN.match(words[0]):  # ``X=1 command export``: ``command`` runs the builtin here, so not _command_words
        words = words[1:]
    if timed and words[0] == "time":
        words = words[_past_time(words):]
    bypassed = False
    while words and words[0] in ("builtin", "command") and (bypassed or words[0] not in functions):
        words, bypassed = _wrapped(words) or [], True
    if not words or words[0] not in _STATE_BUILTINS or (words[0] in functions and not bypassed):
        return []
    return words


def _wrapped(words: list[str]) -> list[str] | None:
    """The command ``builtin`` or ``command`` at the head of ``words`` runs, past ``--`` and ``command -p``; None when it
    runs none (``command -v``, a bare ``command``)."""
    rest = words[1:]
    while rest and rest[0] != "--" and rest[0].startswith("-"):
        if words[0] == "builtin" or set(rest[0][1:]) != {"p"}:
            return None
        rest = rest[1:]
    return rest[1:] or None if rest[:1] == ["--"] else rest or None


def _builtin_options(args: list[str], signed: bool = False) -> tuple[str, str, list[str]]:
    """The option letters before the operands of ``export`` or ``unset`` (``-x``), those that ``signed`` options turn off
    (``declare +x``), and the operands: options end at the first word that is no option, or at ``--``."""
    flags, plus, i = "", "", 0
    while i < len(args) and (args[i].startswith("-") and args[i] != "-" or signed and args[i].startswith("+") and len(args[i]) > 1):
        i += 1
        if args[i - 1] == "--":
            break
        if args[i - 1][0] == "+":
            plus += args[i - 1][1:]
        else:
            flags += args[i - 1][1:]
    return flags, plus, args[i:]


def _posix_set(skipped: list[str], exported: bool = False) -> bool:
    """Whether ``POSIXLY_CORRECT`` is set for the command after the wrapper words ``skipped``, in order: ``NAME=value`` sets
    it, ``env -i`` (``--ignore-environment``) or ``env -u POSIXLY_CORRECT`` (``--unset``) drops what was set before, also
    what the shell ``exported``."""
    posix, i = exported, 0
    while i < len(skipped):
        word, i = skipped[i], i + 1
        if word.startswith(_POSIX_ASSIGN):
            posix = True
        else:
            reset, unset, taken = _env_option(word, skipped[i:i + 1])
            posix, i = posix and not reset and unset != "POSIXLY_CORRECT", i + taken
    return posix


def _env_option(word: str, following: list[str]) -> tuple[bool, str, int]:
    """What the ``env`` option ``word`` does to the environment: whether it drops all of it (``-i``, ``-``,
    ``--ignore-environment``), the name it unsets (``-u NAME``, ``-uNAME``, ``--unset=NAME``; else ``""``), and how many
    of the ``following`` word it takes as its value. A word that is no option does none of them."""
    if word == "-":  # GNU env: ``-`` is ``-i``
        return True, "", 0
    if not word.startswith("-"):
        return False, "", 0
    if word.startswith("--"):
        name, eq, value = word[2:].partition("=")
        if name and "unset".startswith(name):
            return False, value or (following[0] if following else ""), 0 if eq else 1
        return len(name) >= 8 and "ignore-environment".startswith(name), "", 0
    reset = False
    for at, c in enumerate(word[1:], 1):  # a cluster: ``-i0``; ``u``, ``C`` and ``S`` take the rest of the word or the next
        reset |= c == "i"
        if c in "uCS":
            given = word[at + 1:] or (following[0] if following else "")
            return reset, given if c == "u" else "", 0 if word[at + 1:] else 1
    return reset, "", 0


def _in_own_shell(outer: str, after: str) -> bool:
    """Whether the part with this outer text runs in the script's shell: not backgrounded by ``after``, nor a
    pipeline stage (a pipe in quotes or escaped, ``\\|``, is not one)."""
    return after != "&" and not _PIPE.search(re.sub(r"\\.", "", bare(outer)))


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


def _search_stage(part: str, functions: Functions) -> str | None:
    """The first stage of a pipeline whose other stages all only cut its lines (:func:`~dokimi.repos.shell_search.cuts_lines`:
    ``grep x AGENTS.md | head -n 20``), else None: the lines of a search that only ``head`` or ``tail`` cut still reach the agent."""
    cuts = [m.start() for m in _PIPE.finditer(masked(part))]
    stages = [part[a:b] for a, b in zip([0, *(c + 1 for c in cuts)], [*cuts, len(part)], strict=True)]
    return stages[0] if cuts and all(cuts_lines(without_stderr(s.removeprefix("&")), functions) for s in stages[1:]) else None  # the ``&`` of ``|&`` is no word


def _ungrouped(text: str) -> str:
    """A part's ``text`` past the ``{`` that open brace groups in it; ``""`` for the ``}`` that closes one."""
    return "" if _GROUP_CLOSE.match(text) else re.sub(r"^(?:\{\s+)+", "", text)

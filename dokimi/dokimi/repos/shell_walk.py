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

from .shell_functions import Functions, define
from .shell_redirects import bare, masked, without_stderr
from .shell_search import Searched, cuts_lines, passes_through, search_program, searched_files
from .shell_sed import sed_files
from .shell_split import split_script
from .shell_words import (_DIRS, _KEYWORDS, _case_closes, _case_opens, _cd, _command_words, _loses, _run_name, _runs_dir,
                          _time_keyword, _unquoted, _stdout_after_stderr)

_BASH_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -lc (.+)$", re.S)
_PRINTERS = {"cat", "head", "tail", "nl", "sed"}
_CONTINUATION = re.compile(r"\\\n")
_HEREDOC_DELIMITER = r"(?<!<)<<-?[ \t]*(['\"]?)([^\s;&|<>()'\"\\-][^\s;&|<>()'\"\\]*+)\1"  # a whole shell word; not ``<<<``, a here-string
_HEREDOC = HeredocPattern(re.compile(_HEREDOC_DELIMITER + r"([^\n]*)\n.*?^[ \t]*\2[ \t\r]*$", re.S | re.M), re.compile(_HEREDOC_DELIMITER),
                          re.compile(r"^[ \t]*([^\s;&|<>()'\"\\]+)[ \t\r]*$", re.M), re.compile(_HEREDOC_DELIMITER + r"([^\n]*)\n.*", re.S), word=2)
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
    walk = _Walk(posixpath.normpath(cwd) if cwd else "", define(known, script), known)
    for part, after, in_subshell, outer in split_script(script):
        op, previous = previous, after
        if _ungrouped(part).strip():  # the ``}`` that closes a group has no exit of its own: the group's last command's
            walk.last_start, walk.cut = len(out), False
        chained = op in ("&&", "||")
        if not chained and walk.gated:  # a cd after && or || may not have run: the next chain starts in no known directory
            walk.here, walk.gated = None, False
        walk.chain(op, chained)
        if walk.in_function(bare(part)) or walk.in_case(outer, part, after, out):  # a definition runs nothing
            continue
        if op == "||" and walk.moved:
            walk.here = None
        walk.track_groups(bare(outer), after, out)  # ``{ cd $(pwd); } | cat``: the group is in this shell
        if in_subshell:  # a subshell: its reads are not tracked
            if _runs_dir(outer, walk.dirs, walk.functions):  # ``cd $(dirname f)``: the cd is outside the parentheses
                walk.lose(_in_own_shell(outer, after))
            continue
        words = walk.words(part, after)
        if words:
            out.extend(walk.command(words, after, chained, _time_keyword(part)))
        elif (stage := _search_stage(part, walk.functions)) is not None:  # ``grep x AGENTS.md | head``: the search's lines reach the agent
            walk.cut = True
            out.extend(walk.piped(walk.words(stage, after, staged=True), _time_keyword(part)))
    walk.chain(previous, False)  # a list backgrounded at the end (``cd sub && make &``) ran in a subshell too
    return out, walk


_Spot = tuple[str | None, bool, bool]  # (here, shell_moved, moved): what the end of a subshell restores


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
        self.list_start: _Spot = (here, False, False)  # the spot where the && / || list began
        self.listed = False  # that list has more than one command
        self.function = 0  # the brace depth inside a function definition
        self.head = False  # the last part was a function's name alone (``f()``): its body's ``{`` comes next
        self.case = 0  # the depth inside ``case ... esac``
        self.operands: int | None = None  # how many files the last printing command named
        self.passthru = False  # a search printed every line (``rg --passthru``): its exit 1 is no sign it showed none
        self.last_start = 0  # where in the found paths the script's last non-empty part began
        self.unknown = 0  # the relative files printing commands named at a directory not known
        self.cut = False  # the last part is a search piped into head or tail: its exit may be the pipeline's, under pipefail

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
        if piped:  # the group runs in a pipeline's subshell: where it ends up is not tracked
            self.here = None

    def close_group(self, after_close: str, redirects: str, after: str, out: list[str]) -> None:
        before, piped, (mark, unknown, passthru), (self.list_start, self.listed) = self.groups.pop()
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
        words, via_env, chdir, bypassed = _command_words(words, timed, self.functions)
        if not words:
            return []
        name = _run_name(words[0], via_env)
        if name is None:
            return []
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
            return self.shown(name, words[1:], chdir)
        return []

    def shown(self, name: str, args: list[str], chdir: bool) -> list[str]:
        """The files a printing command prints, or a search shows lines of, as a path list (:class:`Searched` for a search)."""
        if name in _PRINTERS:
            return self.printed(name, args, chdir)
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

    def printed(self, name: str, args: list[str], chdir: bool) -> list[str]:
        """The files a printing command reads; ``unknown`` counts those it names at a directory not known."""
        if _asks_for_help(args):  # ``cat --help AGENTS.md`` prints the help and opens no file
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
        return posixpath.normpath(f if f.startswith("/") else posixpath.join(self.here or "", f))


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

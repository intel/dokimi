# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A shell command's words, for :func:`~dokimi.repos.cli_traces.shell_reads`: the command word past what comes before
it (``NAME=value``, a redirection, ``builtin``, ``command``, ``time``, ``env``), and whether it runs a directory builtin."""

from __future__ import annotations

import posixpath
import re
import shlex

_SYSTEM_BIN = {"/bin", "/usr/bin", "/usr/local/bin"}
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")  # a NAME=value word: a variable set for the command that follows
# a redirection word (``>``, ``2>err``, ``</dev/null``, ``&>>log``, ``{fd}>x``): group 1 is its attached target, if any
_REDIRECT = re.compile(r"(?:\d+|&|\{\w+\})?(?:>[>|&]?|<(?:<<?|[&>])?)(.*)", re.S)
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}
# after these builtins the directory is not known: a directory stack, or a script that may run a ``cd``
_DIR_LOST = {"pushd", "popd", "eval", "source", "."}
_DIRS = {"cd"} | _DIR_LOST  # the builtins that change, or may change, the directory
_PREFIXES = {"builtin", "command", "time"}  # each runs the directory builtin named after it in this shell
# ``time`` is bash's keyword only as the first word, unquoted; else (``X=1 time``, ``\time``) it is /usr/bin/time, a child
_TIME = re.compile(r"\s*(?:(?:[{!]|if|then|else|elif|do|while|until)\s+)*time(?=\s|$)")


def _time_keyword(text: str) -> bool:
    """Whether the command in ``text`` starts with bash's ``time`` keyword, which runs the rest in this shell."""
    return _TIME.match(text) is not None


def _run_name(name: str, via_env: bool) -> str | None:
    """The name a command runs as, or None when it is a directory builtin (``cd``, ``pushd``, ``eval``, ...) that does not run."""
    if via_env and (name == "cd" or name in _DIR_LOST):  # ``env cd x`` runs no builtin: nothing moves
        return None
    if "/" in name:  # ``/bin/cat`` is cat; ``./cat`` or ``/bin/cd`` is some other program
        name = posixpath.basename(name) if posixpath.dirname(name) in _SYSTEM_BIN else ""
        if name == "cd" or name in _DIR_LOST:
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


def _runs_dir(outer: str) -> bool:
    """Whether a part's text, or its outer text (:class:`~dokimi.repos.shell_split.Part`), runs a directory builtin, as
    ``cd $(...)`` and ``X="a b" cd "$D"`` do, or may (:func:`_may_run_dir`); a subshell ``(cd x; ...)`` has no such
    text, so it changes nothing outside itself. Text shlex cannot split is read word by word (:func:`_unquoted`): its
    command word counts, and when a quote up to it does not close (``X="a b c" cd``), any builtin's name in it counts."""
    try:
        words, told = shlex.split(outer), None
    except ValueError:
        raw = outer.split()
        words, told = _unquoted(raw), [_unquote(w) is not None for w in raw]
    rest, timed = words, _time_keyword(outer)
    while rest and (rest[0] in _KEYWORDS or rest[0] == "{"):
        rest = rest[1:]
    rest, via_env, _ = _command_words(rest, timed)
    at = _command_at(rest, timed)
    if at is None or at >= len(rest):
        return False
    if told is not None and not all(told[:len(words) - len(rest) + at + 1]):
        return any(w in _DIRS for w in words)
    return not via_env and rest[at] in _DIRS


def _may_run_dir(words: list[str], timed: bool) -> bool:
    """The rule for a part the walker does not fully follow: its unquoted ``words``, from the name on, start with a
    ``builtin``, ``command`` or ``time`` (``timed``: the keyword) that :func:`_command_words` did not skip, as an option
    comes first (``time -p cd sub``, ``command -p cd``, ``builtin -- cd``), and the command word after it names a
    directory builtin."""
    at = _command_at(words, timed)
    return at is not None and 0 < at < len(words) and words[at] in _DIRS


def _command_at(words: list[str], timed: bool) -> int | None:
    """The index of the command word in unquoted ``words``, past ``builtin``, ``command`` and, when ``timed``, a first
    ``time``, with their options (``time -p``, ``command -p``, ``--``), redirections and, after ``time``, ``NAME=value``
    words; None for ``command -v`` / ``-V``, which only describes the command."""
    at: int | None = 0
    while at is not None and at < len(words) and _prefix(words[at], timed and at == 0):
        at = _past_prefix(words, at)
    return at


def _past_prefix(words: list[str], at: int) -> int | None:
    """The index past the prefix ``words[at]`` and the options, redirections and, after ``time``, ``NAME=value`` words
    that follow it in any order (``command > log -p cd``, ``time -p X=1 cd``); None for ``command -v`` / ``-V``."""
    prefix, at, options = words[at], at + 1, True
    while at < len(words):
        word = words[at]
        if options and word.startswith("-") and word != "-":
            if prefix == "command" and set(word) & {"v", "V"}:
                return None
            at, options = at + 1, word != "--"  # past ``--`` a word is no option
        elif (past := _past_redirections(words, at, prefix == "time")) > at:
            at = past  # the keyword times a whole simple command, its assignments too
        else:
            break
    return at


def _past_redirections(words: list[str], at: int, assignments: bool = True) -> int:
    """The index of the first word of ``words`` from ``at`` on that is neither a redirection (``> log``, ``2>err``,
    which is no command word) nor, when ``assignments``, ``NAME=value``."""
    while at < len(words):
        if assignments and _ASSIGN.match(words[at]):
            at += 1
        elif m := _REDIRECT.fullmatch(words[at]):
            at += 1 if m.group(1) else 2  # its target is attached, or the next word
        else:
            break
    return min(at, len(words))


def _prefix_runs_dir(words: list[str], timed: bool) -> bool:
    """Whether ``words`` start with ``builtin``, ``command`` or bash's ``time`` (``timed``) and then a directory builtin,
    which the prefix runs in this shell; past ``time``, ``NAME=value`` words may come first (``time X=1 cd sub``), and
    redirections past any of them."""
    if not _prefix(words[0], timed):
        return False
    at = _past_redirections(words, 1, words[0] == "time")
    return at < len(words) and words[at] in _DIRS


def _program(word: str) -> str:
    """The program a word names when it is a bare name or a system path (``env``, ``/usr/bin/env``); ``""`` for a
    path elsewhere (``./env``), which is some program of the repository, not the one the system has."""
    return posixpath.basename(word) if posixpath.dirname(word) in ("", *_SYSTEM_BIN) else ""


def _unquoted(words: list[str]) -> list[str]:
    """Words split at blanks, each with its quotes and backslash escapes removed and nothing expanded (``"cd"``,
    ``'cd'``, ``\\cd`` and ``c"d"`` are ``cd``; ``"$D"`` is ``$D``), as ``shlex.split`` gives them; a word
    whose quotes do not close here (``"a b"`` split at its blank) stays as it is."""
    return [w if (u := _unquote(w)) is None else u for w in words]


def _unquote(word: str) -> str | None:
    """``word`` with its quotes and escapes removed, as :func:`_unquoted` does; None when its quote does not close."""
    try:
        split = shlex.split(word)
    except ValueError:
        return None
    return split[0] if len(split) == 1 else None


def _prefix(word: str, timed: bool) -> bool:
    """Whether ``word`` is ``builtin``, ``command``, or ``time`` as bash's keyword (``timed``), which run what follows."""
    return word in _PREFIXES and (word != "time" or timed)


def _command_words(words: list[str], timed: bool) -> tuple[list[str], bool, bool]:
    """The words from the command's name on; whether ``env`` runs it; and whether that ``env`` changes
    directory (``-C`` / ``--chdir``). ``NAME=value`` words, redirections (``> log cd sub``), ``builtin``, ``command`` or
    a first ``time`` (``timed``: the keyword) before a directory builtin (which runs it; ``time X=1 cd``), and ``env``
    with its options and assignments come before the name and are skipped. ``words`` are unquoted: ``shlex.split``'s,
    or :func:`_unquoted`'s for a part shlex does not split."""
    via_env = chdir = False
    while words:
        if _ASSIGN.match(words[0]) or _REDIRECT.fullmatch(words[0]):
            words = words[_past_redirections(words, 0):]
        elif _prefix_runs_dir(words, timed):  # ``./command`` is a program
            words = words[1:]
        elif _program(words[0]) == "env":
            words, via_env = words[1:], True
            while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
                opt = words[0]
                chdir |= opt.startswith(("-C", "--chdir"))
                words = words[2:] if opt in ("-C", "-u", "--chdir", "--unset") else words[1:]  # these take the next word as a value
        else:
            break
        timed = False  # past the first word, ``time`` is a program
    return words, via_env, chdir


def _is_cd(part: str) -> bool:
    """Whether ``part`` runs the ``cd`` builtin, past what the walker skips (``time cd``, ``X=1 cd``, ``"cd"``; not
    ``env cd``, nor ``X=1 time cd`` or ``\\time cd``, which run /usr/bin/time)."""
    words, via_env, _ = _command_words(_unquoted(part.split()), _time_keyword(part))
    return words[:1] == ["cd"] and not via_env

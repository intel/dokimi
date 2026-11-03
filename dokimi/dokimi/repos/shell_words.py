# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A shell command's words, for :func:`~dokimi.repos.cli_traces.shell_reads`: the command word past what comes before
it (``NAME=value``, ``builtin``, ``command``, ``time``, ``env``), and whether it runs a directory builtin."""

from __future__ import annotations

import posixpath
import re
import shlex

_SYSTEM_BIN = {"/bin", "/usr/bin", "/usr/local/bin"}
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")  # a NAME=value word: a variable set for the command that follows
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}
# after these builtins the directory is not known: a directory stack, or a script that may run a ``cd``
_DIR_LOST = {"pushd", "popd", "eval", "source", "."}
_DIRS = {"cd"} | _DIR_LOST  # the builtins that change, or may change, the directory
_PREFIXES = {"builtin", "command", "time"}  # each runs the directory builtin named after it in this shell


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
    rest = words
    while rest and (rest[0] in _KEYWORDS or rest[0] == "{"):
        rest = rest[1:]
    rest, via_env, _ = _command_words(rest)
    at = _command_at(rest)
    if at is None or at >= len(rest):
        return False
    if told is not None and not all(told[:len(words) - len(rest) + at + 1]):
        return any(w in _DIRS for w in words)
    return not via_env and rest[at] in _DIRS


def _may_run_dir(words: list[str]) -> bool:
    """The rule for a part the walker does not fully follow: its unquoted ``words``, from the name on, start with a
    ``builtin``, ``command`` or ``time`` that :func:`_command_words` did not skip, as an option comes first (``time -p
    cd sub``, ``command -p cd``, ``builtin -- cd``), and the command word after it names a directory builtin."""
    at = _command_at(words) if words[:1] and words[0] in _PREFIXES else None
    return at is not None and at < len(words) and words[at] in _DIRS


def _command_at(words: list[str]) -> int | None:
    """The index of the command word in unquoted ``words``, past ``builtin``, ``command`` and ``time`` with their options
    (``time -p``, ``command -p``, ``--``); None for ``command -v`` / ``-V``, which only describes the command."""
    at = 0
    while at < len(words) and words[at] in _PREFIXES:
        prefix, at = words[at], at + 1
        while at < len(words) and words[at].startswith("-") and words[at] != "-":
            if prefix == "command" and set(words[at]) & {"v", "V"}:
                return None
            at += 1
            if words[at - 1] == "--":
                break
    return at


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


def _command_words(words: list[str]) -> tuple[list[str], bool, bool]:
    """The words from the command's name on; whether ``env`` runs it; and whether that ``env`` changes
    directory (``-C`` / ``--chdir``). ``NAME=value`` words, ``builtin``, ``command`` or ``time`` before a
    directory builtin (which runs it), and ``env`` with its options and assignments come before the name and are skipped.
    ``words`` are unquoted: ``shlex.split``'s, or :func:`_unquoted`'s for a part shlex does not split."""
    via_env = chdir = False
    while words:
        if _ASSIGN.match(words[0]):
            words = words[1:]
        elif words[0] in _PREFIXES and len(words) > 1 and words[1] in _DIRS:  # ``./command`` is a program
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


def _is_cd(part: str) -> bool:
    """Whether ``part`` runs the ``cd`` builtin, past what the walker skips (``time cd``, ``X=1 cd``, ``"cd"``; not ``env cd``)."""
    words, via_env, _ = _command_words(_unquoted(part.split()))
    return words[:1] == ["cd"] and not via_env

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A shell command's words, for :func:`~dokimi.repos.cli_traces.shell_reads`: the command word past what comes before
it (``NAME=value``, a redirection, ``builtin``, ``command``, ``time``, ``env``), whether it runs a directory builtin, and
the ``case ... esac`` a part opens or closes."""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Collection

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
# a word after which a ``case`` is a command word; with ``)``, ``(`` and ``|`` always, else only at the start
_LEAD = _KEYWORDS | {"{", "time"}
# what a ``case`` count leaves out: quoted text and escapes (each one ``q``), then backtick and ``$( )`` substitutions
_QUOTED = re.compile(r"\\.|'[^']*'|\"(?:\\.|[^\"\\])*\"", re.S)
_SUBSTITUTION = re.compile(r"`[^`]*`|\$\([^()]*\)")


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


def _runs_dir(outer: str, dirs: Collection[str] = _DIRS, functions: Collection[str] = ()) -> bool:
    """Whether a part's text, or its outer text (:class:`~dokimi.repos.shell_split.Part`), runs a directory builtin, as
    ``cd $(...)`` and ``X="a b" cd "$D"`` do, or may (:func:`_may_run_dir`); a subshell ``(cd x; ...)`` has no such
    text, so it changes nothing outside itself. Text shlex cannot split is read word by word (:func:`_unquoted`): its
    command word counts, and when a quote up to it does not close (``X="a b c" cd``), any builtin's name in it counts.
    ``dirs`` are those names: ``_DIRS``, and the script's functions that name one (``f > log`` of ``f() { cd x; }``);
    ``functions`` are all the script's functions: one named like a wrapper runs, not the wrapper (``command "$D"``)."""
    try:
        words, told = shlex.split(outer), None
    except ValueError:
        raw = outer.split()
        words, told = _unquoted(raw), [_unquote(w) is not None for w in raw]
    rest, timed = words, _time_keyword(outer)
    while rest and (rest[0] in _KEYWORDS or rest[0] == "{"):
        rest = rest[1:]
    rest, via_env, _, bypassed = _command_words(rest, timed, functions)
    if rest and rest[0] in functions and not bypassed:  # a function's call: it moves the shell when it is in ``dirs``
        return rest[0] in dirs
    at = _command_at(rest, timed)
    if at is None or at >= len(rest):
        return False
    if told is not None and not all(told[:len(words) - len(rest) + at + 1]):
        return any(w in dirs for w in words)
    return not via_env and rest[at] in dirs


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
    words; None for ``command -v`` / ``-V``, which only describes the command. Any dash word is taken as an option
    here (``time -x cd``), as this only decides whether a directory may be lost."""
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


def _past_time_options(words: list[str], at: int) -> int:
    """The index past the options of bash's ``time`` keyword from ``at`` on: one ``-p``, then one ``--``, which its
    lexer reads right after the keyword; any other word, ``-x``, ``--help`` or a second ``-p``, is the command's name."""
    if words[at:at + 1] == ["-p"]:
        at += 1
    return at + 1 if words[at:at + 1] == ["--"] else at


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


def _command_words(words: list[str], timed: bool, functions: Collection[str] = ()) -> tuple[list[str], bool, bool, bool]:
    """The words from the command's name on; whether ``env``, /usr/bin/time or ``command`` runs it as a child (but for
    ``command``'s builtins); whether that ``env`` changes directory (``-C`` / ``--chdir``); and whether the name is not
    looked up as a shell function: after ``builtin`` or ``command`` (``command cd sub`` runs the builtin, also with a
    function ``cd``), as a child's, or the ``time`` keyword itself. ``NAME=value`` words, redirections (``> log cd
    sub``), ``builtin`` or ``command`` before a directory builtin (which runs it), ``command`` before another name
    (:func:`_runs_program`), a first ``time`` (``timed``: the keyword, which runs what it times here) with its options
    (:func:`_past_time_options`), assignments and redirections (``time X=1 cat a``; not before a directory builtin after
    an option, ``time -p cd``, which stays for :func:`_may_run_dir`), /usr/bin/time (``X=1 time cat a``, ``\\time cat
    a``; the word after it is the name, so an option or an assignment there names no printer), and ``env`` with its
    options and assignments come before the name and are skipped; not such a wrapper that is one of the script's
    ``functions`` (``command() { ...; }; command cat a`` runs the function), but for the keyword. Past ``env`` the
    next word is a program's name, never a shell builtin: ``env command cat a`` and ``env builtin cd x`` run the
    programs ``command`` and ``builtin``, which do not exist, so ``command`` and ``builtin`` stay as the name there
    (``env time cat a`` runs /usr/bin/time, which does). ``words`` are unquoted: ``shlex.split``'s, or
    :func:`_unquoted`'s for a part shlex does not split."""
    via_env = chdir = bypassed = child = False
    while words:
        if _ASSIGN.match(words[0]) or _REDIRECT.fullmatch(words[0]):
            words = words[_past_redirections(words, 0):]
        elif words[0] in functions and not bypassed and not (timed and words[0] == "time"):
            break  # the function runs, whatever its name wraps
        elif not child and _prefix_runs_dir(words, timed):  # ``./command`` is a program
            words, bypassed = words[1:], words[0] != "time"
        elif timed and words[0] == "time" and (at := _past_time(words)):
            words = words[at:]
        elif not timed and _program(words[0]) == "time":
            return words[1:], True, chdir, True  # a child runs the next word as a program (``X=1`` too): no builtin moves here
        elif not child and _runs_program(words):
            words, via_env, bypassed = words[1:], True, True
        elif _program(words[0]) == "env":
            words, moves = _past_env(words[1:])
            via_env, bypassed, chdir, child = True, True, chdir or moves, True
        else:
            bypassed |= timed and words[0] == "time"  # the keyword left for ``time -p cd``: no function of its name runs
            break
        timed = False  # past the first word, ``time`` is a program
    return words, via_env, chdir, bypassed


def _past_env(words: list[str]) -> tuple[list[str], bool]:
    """The words after ``env`` past its options and assignments, and whether an option changes directory (``-C``)."""
    chdir = False
    while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
        opt = words[0]
        chdir |= opt.startswith(("-C", "--chdir"))
        words = words[2:] if opt in ("-C", "-u", "--chdir", "--unset") else words[1:]  # these take the next word as a value
    return words, chdir


def _runs_program(words: list[str]) -> bool:
    """Whether ``words`` are ``command`` and a command's name (``command cat a``), which it runs as a builtin or a
    program, never as the shell function of that name; not before an option (``command -v``), ``NAME=value``, a
    redirection or another prefix, which stay as they are (a directory builtin after it: :func:`_prefix_runs_dir`)."""
    return words[0] == "command" and len(words) > 1 and not (
        words[1].startswith("-") or _ASSIGN.match(words[1]) or _REDIRECT.fullmatch(words[1]) or words[1] in _PREFIXES)


def _past_time(words: list[str]) -> int:
    """The index of the command bash's ``time`` keyword (``words[0]``) times, past its options
    (:func:`_past_time_options`; ``time -x cat a`` runs ``-x``), then ``NAME=value`` words and redirections; 0 when a
    directory builtin comes after an option, also one bash takes as the name (``time -p cd sub``, ``time -x cd sub``,
    ``time > log -p cd sub``), which stays for :func:`_may_run_dir`: the directory is lost."""
    at = _past_redirections(words, _past_time_options(words, 1))
    loose = _past_prefix(words, 0)  # every dash word an option, in any order
    return 0 if any(i is not None and i < len(words) and words[i] in _DIRS for i in (at, loose)) else at


def _activates(name: str, args: list[str]) -> bool:
    """Whether ``name`` and its unquoted ``args`` are ``source`` or ``.`` of a virtualenv's activate script and nothing
    more: one operand whose last two components are ``bin/activate`` (``.venv/bin/activate``, ``"/opt/v/bin/activate"``).
    Assumed to move no shell: the scripts venv, virtualenv and uv write set variables and ``PATH`` and run no ``cd``."""
    return name in ("source", ".") and len(args) == 1 and args[0].split("/")[-2:] == ["bin", "activate"]


def _is_cd(part: str) -> bool:
    """Whether ``part`` runs the ``cd`` builtin, past what the walker skips (``time cd``, ``X=1 cd``, ``"cd"``; not
    ``env cd``, nor ``X=1 time cd`` or ``\\time cd``, which run /usr/bin/time)."""
    words, via_env, _, _ = _command_words(_split(part), _time_keyword(part))
    return words[:1] == ["cd"] and not via_env


def _is_activate(part: str) -> bool:
    """Whether ``part`` sources a virtualenv's activate script (:func:`_activates`), past what the walker skips; a
    quoted operand is one word (``source 'my env/bin/activate'``), as the walker reads it."""
    words, via_env, _, _ = _command_words(_split(part), _time_keyword(part))
    return not via_env and bool(words) and _activates(words[0], words[1:])


def _split(part: str) -> list[str]:
    """``part``'s words as the walker splits them, with ``shlex.split``; :func:`_unquoted` words at its blanks when
    shlex cannot (a quote that does not close, a trailing backslash: ``cd sub\\``)."""
    try:
        return shlex.split(part)
    except ValueError:
        return _unquoted(part.split())


def _loses(name: str, words: list[str], conditional: bool, timed: bool, via_env: bool = False) -> bool:
    """Whether the followed command ``words``, run as ``name``, leaves the directory not known: a ``pushd``, ``popd``,
    ``eval``, ``source`` or ``.`` (not of an activate script, :func:`_activates`), a ``cd`` that may not run
    (``conditional``), or a part not fully followed (:func:`_may_run_dir`), but for one a child runs (``via_env``:
    ``env command -p cd sub`` runs a program, so it moves no shell)."""
    if name in _DIR_LOST:
        return not _activates(name, words[1:])
    return (conditional and name == "cd") or (not via_env and _may_run_dir(words, timed))


def _case_tokens(text: str) -> list[str]:
    """The words and ``(``, ``)``, ``|`` of a part's ``text`` that the shell reads as syntax: a quoted or escaped
    word (``"case"``, ``\\case``) is none, and a substitution's text is left out (one nested in another may stay)."""
    text = _QUOTED.sub("q", text)
    while (inner := _SUBSTITUTION.sub("q", text)) != text:
        text = inner
    return re.findall(r"[()|]|[^\s()|]+", text)


def _case_opens(text: str) -> int:
    """How many ``case ... esac`` open in a part's ``text``: a ``case`` as a command word (first, past ``{``, ``time``
    and the keywords, or after ``(``, ``|`` or a pattern's ``)``), not followed by ``)`` or ``|``, which ends a pattern
    (``a|case)``). A miscount is on the side of more: ``case`` after any ``|`` counts."""
    tokens, opens, command = _case_tokens(text), 0, True
    for tok, nxt in zip(tokens, [*tokens[1:], ""], strict=True):
        opens += command and tok == "case" and nxt not in (")", "|")
        command = tok in ("(", ")", "|") or (command and tok in _LEAD)
    return opens


def _case_closes(text: str) -> bool:
    """Whether a part's ``text`` starts with ``esac`` as a word of syntax, past ``{``, ``time`` and the keywords."""
    tokens = _case_tokens(text)
    while tokens[:1] and tokens[0] in _LEAD:
        tokens = tokens[1:]
    return tokens[:1] == ["esac"]

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The shell functions a script defines, for :func:`~dokimi.repos.cli_traces.shell_walk`: a call of one runs the function,
not the command of its name (``cat() { echo hi; }; cat a`` reads nothing), and a call of one whose body names a
directory builtin may move the shell, as an ``eval`` may, so the walker loses the directory there."""

from __future__ import annotations

import re

from .shell_words import _DIRS, _runs_dir, _unquote, _unquoted

Functions = dict[str, frozenset[str]]  # a function's name -> the words of its body

# quoted text and escapes: no syntax, masked; a comment (a ``#`` starting a word, to the line's end) is blanked
_MASK = re.compile(r"\\.|'[^']*'|\"(?:\\.|[^\"\\])*\"|(?<![^\s;&|()])#[^\n]*", re.S)
# ``f() {`` or ``function f {`` (``function f() {``), where a command may start, the ``{`` on a later line too, past
# blank lines and comments (``f()\n# c\n{``); group 1 or 2 is the name
_DEF = re.compile(r"(?<![^\s;&|(){}])(?:function\s+([^\s(){};&|<>]+)(?:\s*\(\s*\))?|([^\s(){};&|<>=$`]+)\s*\(\s*\))"
                  r"\s*\{(?=\s|$)")
# ``f() ( ... )`` or ``function f ( ... )`` (no ``()`` after the name: bash takes the ``(`` as the body's), whose body
# runs in a subshell; ``function f() (`` matches at ``f``; group 1 or 2 is the name
_SUBSHELL_DEF = re.compile(r"(?<![^\s;&|(){}])(?:function\s+([^\s(){};&|<>]+)\s*\((?!\s*\))"
                           r"|([^\s(){};&|<>=$`]+)\s*\(\s*\)\s*\()")
_BRACE = re.compile(r"(?<![^\s;&|(){}])(?:\{(?=\s|$)|\}(?=[\s;&|)]|$))")  # a brace group's ``{`` or ``}`` as a word
_BREAK = re.compile(r"[\s;&|()<>`{}$]+")
_COMMAND_END = re.compile(r"[;&|()`{}\n]")  # where a command of a body ends, outside quotes: the next one may start
_SOURCES = frozenset({"source", "."})  # named by a body only as a command's word


def define(known: Functions, script: str) -> frozenset[str]:
    """Add the functions ``script`` defines (``f() { ...; }``, ``function f { ...; }``) to ``known``, and return the
    names in ``known`` whose call may move the shell: the body names a directory builtin, or a function that does
    (``g() { f; }``), as any word (``case x in a) cd sub;; esac`` too); ``source`` and ``.`` only as a command's word, not
    of a virtualenv's activate (``git add .`` names none). A body in parentheses (``f() ( cd sub )``,
    ``function f ( cd sub )``) runs in a subshell: its name is known, so a call of it is no read, but it moves nothing. A definition is found
    anywhere outside quotes, in a subshell or never run too, and a name defined again keeps what each definition
    named: both drop reads only."""
    masked = _MASK.sub(lambda m: (" " if m.group()[0] == "#" else "q") * len(m.group()), script)
    for m in _DEF.finditer(masked):
        name = _name(script, m)
        end = _body_end(masked, m.end())
        known[name] = known.get(name, frozenset()) | _words(script[m.end():end], masked[m.end():end])
    for m in _SUBSHELL_DEF.finditer(masked):
        name = _name(script, m)
        known[name] = known.get(name, frozenset())
    movers: frozenset[str] = frozenset()
    while (more := frozenset(n for n, words in known.items() if words & (_DIRS | movers))) != movers:
        movers = more
    return movers


def _name(script: str, m: re.Match[str]) -> str:
    """The name a definition's match gives (group 1 or 2), unquoted as a call's command word is (``"f"`` is ``f``)."""
    start, end = m.span(1) if m.group(1) else m.span(2)
    return _unquote(script[start:end]) or script[start:end]


def _body_end(masked: str, start: int) -> int:
    """Where the body that opened before ``start`` closes, its nested braces counted; the end of the script if never."""
    depth = 1
    for m in _BRACE.finditer(masked, start):
        depth += 1 if m.group() == "{" else -1
        if not depth:
            return m.start()
    return len(masked)


def _words(body: str, masked: str) -> frozenset[str]:
    """The words of a function's body, unquoted (``"cd"``, ``\\cd``: ``cd``), split at blanks and shell syntax; ``source``
    and ``.`` only as a command's word (:func:`~dokimi.repos.shell_words._runs_dir`), so neither ``find . -name x`` nor
    ``source .venv/bin/activate`` names one. ``masked`` is the body with its quoted text masked, where commands end."""
    ends = [m.start() for m in _COMMAND_END.finditer(masked)]
    sourced = {"source" for start, end in zip([0, *(e + 1 for e in ends)], [*ends, len(body)], strict=True)
               if _runs_dir(body[start:end], _SOURCES)}
    return frozenset(_unquoted([w for w in _BREAK.split(body) if w])) - _SOURCES | sourced

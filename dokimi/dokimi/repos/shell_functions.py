# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The shell functions a script defines, for :func:`~dokimi.repos.cli_traces.shell_walk`: a call of one whose body names
a directory builtin may move the shell, as an ``eval`` may, so the walker loses the directory there."""

from __future__ import annotations

import re

from .shell_words import _DIRS, _unquote, _unquoted

Functions = dict[str, frozenset[str]]  # a function's name -> the words of its body

_MASK = re.compile(r"\\.|'[^']*'|\"(?:\\.|[^\"\\])*\"", re.S)  # quoted text and escapes: no syntax, masked
# ``f() {`` or ``function f {`` (``function f() {``), where a command may start; group 1 or 2 is the name
_DEF = re.compile(r"(?<![^\s;&|(){}])(?:function\s+([^\s(){};&|<>]+)(?:\s*\(\s*\))?|([^\s(){};&|<>=$`]+)\s*\(\s*\))"
                  r"\s*\{(?=\s|$)")
_BRACE = re.compile(r"(?<![^\s;&|(){}])(?:\{(?=\s|$)|\}(?=[\s;&|)]|$))")  # a brace group's ``{`` or ``}`` as a word
_BREAK = re.compile(r"[\s;&|()<>`{}$]+")


def define(known: Functions, script: str) -> frozenset[str]:
    """Add the functions ``script`` defines (``f() { ...; }``, ``function f { ...; }``) to ``known``, and return the
    names in ``known`` whose call may move the shell: the body names a directory builtin, or a function that does
    (``g() { f; }``), as any word (``case x in a) cd sub;; esac`` too). A definition is found anywhere outside quotes,
    in a subshell or never run too, and a name defined again keeps what each definition named: both drop reads only."""
    masked = _MASK.sub(lambda m: "q" * len(m.group()), script)
    for m in _DEF.finditer(masked):
        start, end = m.span(1) if m.group(1) else m.span(2)
        name = script[start:end]
        name = _unquote(name) or name
        known[name] = known.get(name, frozenset()) | _words(script[m.end():_body_end(masked, m.end())])
    movers: frozenset[str] = frozenset()
    while (more := frozenset(n for n, words in known.items() if words & (_DIRS | movers))) != movers:
        movers = more
    return movers


def _body_end(masked: str, start: int) -> int:
    """Where the body that opened before ``start`` closes, its nested braces counted; the end of the script if never."""
    depth = 1
    for m in _BRACE.finditer(masked, start):
        depth += 1 if m.group() == "{" else -1
        if not depth:
            return m.start()
    return len(masked)


def _words(body: str) -> frozenset[str]:
    """The words of a function's body, unquoted (``"cd"``, ``\\cd``: ``cd``), split at blanks and shell syntax."""
    return frozenset(_unquoted([w for w in _BREAK.split(body) if w]))

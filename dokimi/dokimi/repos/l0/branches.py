# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The branch names and patterns a tree's configuration names, read once per scan.

The path rule (:mod:`.core`) takes a path-like claim one of them names for a branch, not a path:
``maintenance/*`` beside a ``no-commit-to-branch`` hook with ``--pattern maintenance/.*``. Read from the
``args`` of that hook in the root ``.pre-commit-config.yaml``, and from the ``branches`` and
``branches-ignore`` filters of the events that take them (``on.push``, ``on.pull_request``, ...) in the workflows GitHub runs.
"""

from __future__ import annotations

import importlib
import posixpath
import re
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from . import workflows

if TYPE_CHECKING:
    from .core import Scan

PRE_COMMIT = ".pre-commit-config.yaml"
HOOK = "no-commit-to-branch"
_LONG = ("--branch", "--pattern")
_SHORT = {"-b": "--branch", "-p": "--pattern"}
_EVENTS = ("push", "pull_request", "pull_request_target", "merge_group", "workflow_run")
_FILTERS = ("branches", "branches-ignore")


class _Str(str):
    """A YAML string that keeps the 1-based line it starts on."""

    line = 0


class _Loader(yaml.SafeLoader):
    """``yaml.safe_load``, with each string a :class:`_Str`."""


def _str(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> _Str:
    s = _Str(loader.construct_scalar(node))
    s.line = node.start_mark.line + 1
    return s


_Loader.add_constructor("tag:yaml.org,2002:str", _str)


@dataclass(frozen=True)
class Source:
    """One branch name (``rx`` is None) or pattern, and the reason naming where it is."""

    text: str
    rx: re.Pattern[str] | _Glob | None
    why: str

    def names(self, claim: str, fills: list[str], file: bool = False) -> bool:
        """``claim`` is the text, or, unless it reads as a ``file``, a pattern starting with its literal first directory
        matches every filling of it."""
        head = claim.split("/")[0]
        literal = "/" in claim and head != "" and not re.search(r"[*?\[<{]", head)
        led = literal and not file and self.text.lstrip("^!").startswith(head + "/")
        return claim == self.text or (self.rx is not None and led and all(self.rx.match(f) for f in fills))


def branch_of(scan: Scan, claim: str, fills: list[str]) -> str | None:
    """The reason of the first source that names ``claim`` (``fills``: it with its wildcards filled in), else None.

    A claim whose last segment ends in an extension some tracked file has (``release/CHANGELOG.md``) reads as a file:
    only a source equal to it names it.
    """
    file = posixpath.splitext(claim.rstrip("/"))[1] in _extensions(scan)
    return next((s.why for s in _sources(scan) if s.names(claim, fills, file)), None)


def _extensions(scan: Scan) -> set[str]:
    return scan.cached("file_extensions", lambda: {posixpath.splitext(f)[1] for f in scan.files} - {""})


def unread_files(scan: Scan) -> dict[str, str]:
    """The ``.pre-commit-config.yaml`` and workflows the branch reading did not read (too big, binary, a dangling link)
    -> why, in tree order."""
    return _read(scan)[1]


def _sources(scan: Scan) -> list[Source]:
    return _read(scan)[0]


def _read(scan: Scan) -> tuple[list[Source], dict[str, str]]:
    return scan.cached("branch_sources", lambda: _read_sources(scan))


def _read_sources(scan: Scan) -> tuple[list[Source], dict[str, str]]:
    flows = workflows.runnable(scan)
    skipped: dict[str, str] = {}
    scan.preload([PRE_COMMIT, *flows], skipped)
    return [*_hook_sources(scan), *_filter_sources(scan, flows)], {f: skipped[f] for f in scan.files if f in skipped}


def _load(scan: Scan, path: str) -> Any:
    text = scan.text(path)
    try:
        return yaml.load(text, Loader=_Loader)  # nosec B506 - a SafeLoader subclass
    except Exception:  # PyYAML's constructors raise more than YAMLError: a bad date, tag value, depth
        return None


def _hook_sources(scan: Scan) -> list[Source]:
    """The ``--branch`` names and ``--pattern`` regexes of each ``no-commit-to-branch`` hook, in file order."""
    doc = _load(scan, PRE_COMMIT) if PRE_COMMIT in scan.file_set else None
    repos = doc.get("repos") if isinstance(doc, dict) else None
    hooks = [h for r in repos if isinstance(r, dict) for h in _list(r.get("hooks"))] if isinstance(repos, list) else []
    out: list[Source] = []
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("id") == HOOK:
            out += [s for opt, value, line in _option_values(_list(hook.get("args")))
                    if (s := _hook_source(opt, value, line))]
    return out


def _list(x: Any) -> list[Any]:
    return x if isinstance(x, list) else []


def _option_values(args: list[Any]) -> list[tuple[str, str, int]]:
    """``(option, value, line)`` for each ``--branch``/``-b`` and ``--pattern``/``-p`` value, in each form :func:`_option` reads."""
    out = []
    for i, arg in enumerate(args):
        opt, value = _option(arg) if isinstance(arg, str) else (None, None)
        nxt = args[i + 1] if i + 1 < len(args) else None
        if opt and value is not None:
            out.append((opt, value, getattr(arg, "line", 0)))
        elif opt and isinstance(nxt, str) and not nxt.startswith("-"):
            out.append((opt, str(nxt), getattr(nxt, "line", 0)))
    return out


def _option(arg: str) -> tuple[str | None, str | None]:
    """The long option ``arg`` is, as the hook's argparse reads it, and the value it holds (None: the next arg).

    ``-b X``, ``-bX``, ``-b=X``, ``--branch X``, ``--branch=X`` and a prefix of the long option (``--bra X``); the same of ``-p``.
    """
    if arg.startswith("--"):
        name, eq, value = arg.partition("=")
        opt = next((o for o in _LONG if len(name) > 2 and o.startswith(name)), None)
        return opt, value if eq else None
    if arg[:2] in _SHORT:
        return _SHORT[arg[:2]], arg[2:].removeprefix("=") if len(arg) > 2 else None
    return None, None


def _hook_source(opt: str, value: str, line: int) -> Source | None:
    """A ``--branch`` name, or a ``--pattern`` the hook tries with ``re.match``; an invalid regex, or one
    :func:`_backtracks` refuses, gives none."""
    where = f"{HOOK} ({PRE_COMMIT}:{line})"
    if opt == "--branch":
        return Source(value, None, f"branch of {where}")
    try:
        if _backtracks(value):
            return None
        return Source(value, re.compile(value), f"branch pattern of {where}")
    except (re.error, OverflowError, RecursionError):  # `a{999999999999}`, a deep nest of groups
        return None


_SRE: Any = importlib.import_module("re._parser")  # the parse re.compile reads; private, and no public API gives it
_REPEATS = (_SRE.MAX_REPEAT, _SRE.MIN_REPEAT, _SRE.POSSESSIVE_REPEAT)
_SPAN = 64  # the most items :func:`_follow` reads, so the check stays linear in the pattern


def _backtracks(pattern: str) -> bool:
    """A repeat that can run more than once (``*``, ``+``, ``{m,n}`` with n over 1) holds a varying item (a repeat with
    m under n, ``?`` too) that can end at the same place two ways, as the next character can be one it takes or one
    that follows it, or an alternation two of whose branches can start with the same character or one be empty:
    ``(a+)+``, ``(a*)*``, ``(a{1,5})+``, ``(a?a?)+``, ``(a|aa)+``. ``([0-9]+\\.)+`` ends each pass at a ``.``: it passes,
    as do the other slow shapes (``.*a.*a.*a.*b``)."""
    with warnings.catch_warnings():  # `re.compile` of the pattern warned already (a nested set)
        warnings.simplefilter("ignore")
        return _nests(_SRE.parse(pattern), False, None)


def _nests(sub: Any, inside: bool, end: set[str] | None) -> bool:
    """``sub`` holds such a repeat; ``inside`` one. ``end``: the characters that can come after the end of ``sub``
    (None: not known)."""
    for i, (op, av) in enumerate(sub):
        if op in _REPEATS:
            if _nests_repeat(sub, i, inside, end):
                return True
        elif op is _SRE.BRANCH or op is _SRE.GROUPREF_EXISTS:  # `(?(1)yes|no)` without `no` has an empty branch
            alts = av[1] if op is _SRE.BRANCH else [av[1], av[2] or []]
            after = _follow(sub, i + 1, end) if inside else None
            if (inside and _overlap(alts)) or any(_nests(a, inside, after) for a in alts):
                return True
        else:
            after = _follow(sub, i + 1, end) if inside and op in (_SRE.SUBPATTERN, _SRE.ATOMIC_GROUP) else None
            if any(_nests(x, inside, after) for x in (av if isinstance(av, tuple) else (av,)) if isinstance(x, _SRE.SubPattern)):
                return True
    return False


def _nests_repeat(sub: Any, i: int, inside: bool, end: set[str] | None) -> bool:
    """The repeat ``sub[i]`` is a varying item of an outer repeat that can end two ways, or holds such a repeat."""
    lo, hi, body = sub[i][1]
    after = _follow(sub, i + 1, end) if inside else None
    if inside and lo < hi:  # taking the body once more or not is told by the next character, or it is not
        first = _first(body)
        if first is None or after is None or first & after:
            return True
    again = _follow(body, 0, None) if hi > 1 else None
    tail = again if not inside else (None if again is None or after is None else again | after)
    return _nests(body, inside or hi > 1, tail if hi > 1 else after)


def _follow(items: Any, start: int, end: set[str] | None) -> set[str] | None:
    """The characters that can start ``items[start:]`` (read by index, not copied), then ``end`` when all of them can
    be empty; None when not known, or when more than ``_SPAN`` of them can be empty in a row."""
    out: set[str] = set()
    for j in range(start, min(len(items), start + _SPAN)):
        op, av = items[j]
        if items[j:j + 1].getwidth()[0] > 0:
            first = _first(items[j:j + 1])
            return None if first is None else out | first
        first = _first(av[-1]) if op in _REPEATS else None  # a repeat that can be skipped; else not known
        if first is None:
            return None
        out |= first
    return None if end is None or start + _SPAN < len(items) else out | end


def _overlap(alts: list[Any]) -> bool:
    """Two of ``alts`` can start with the same character, or one can match the empty string."""
    seen: set[str] = set()
    for alt in alts:
        first = _first(alt)
        if first is None or seen & first:
            return True
        seen |= first
    return False


def _first(sub: Any) -> set[str] | None:
    """The characters ``sub`` can start with, in either case; None when it can be empty or they are not read."""
    if not sub or sub[0:1].getwidth()[0] == 0:
        return None
    op, av = sub[0]
    if op is _SRE.LITERAL:
        return _cased(av)
    if op is _SRE.IN:
        return _class_chars(av)
    if op in (*_REPEATS, _SRE.SUBPATTERN, _SRE.ATOMIC_GROUP):
        return _first(av if op is _SRE.ATOMIC_GROUP else av[-1])
    return None


def _class_chars(items: list[Any]) -> set[str] | None:
    """The characters of a parsed ``[...]``; None for a negated one, a category such as ``\\w`` or a wide range."""
    out: set[str] = set()
    for op, av in items:
        if op is _SRE.LITERAL:
            out |= _cased(av)
        elif op is _SRE.RANGE and av[1] - av[0] < 256:
            out |= {c for i in range(av[0], av[1] + 1) for c in _cased(i)}
        else:
            return None
    return out


def _cased(code: int) -> set[str]:
    """The characters a case-insensitive ``re`` takes for ``chr(code)`` (a superset). ``re`` folds one character to one
    (``İ`` to ``i``), where ``str.lower`` gives ``i̇``: the first character of a longer result counts too."""
    out = {chr(code)}
    for _ in range(2):
        for c in out.copy():
            for s in (c.lower(), c.upper()):
                out |= {s, s[0]}
    return out


def _filter_sources(scan: Scan, flows: list[str]) -> list[Source]:
    """The ``branches`` and ``branches-ignore`` globs of each event of ``_EVENTS``, workflow by workflow.

    A glob that excludes branches (``branches-ignore``, a leading ``!``) names them as one that includes them does: the
    workflow skips ``release/x`` under ``['**', '!release/**']``, but it is still a branch, not a path. The text is
    without that ``!``, so ``!release/1.x`` equals ``release/1.x`` as ``release/1.x`` does.
    """
    out: list[Source] = []
    for path in flows:
        doc = _load(scan, path)
        on = (doc[True] if True in doc else doc.get("on")) if isinstance(doc, dict) else None  # YAML 1.1: `on` is true
        specs = [(e, on.get(e)) for e in _EVENTS] if isinstance(on, dict) else []  # `on: push` has no filters
        out += [Source(p.removeprefix("!"), _filter_regex(p), f"branch filter of on.{event} ({path}:{getattr(p, 'line', 0)})")
                for event, spec in specs if isinstance(spec, dict)
                for key in _FILTERS for p in _list(spec.get(key)) if isinstance(p, str)]
    return out


_FILTER_TOKEN = re.compile(r"\\(.)|(\*\*?)|\[([a-zA-Z0-9-]+)\]|([?+])|(.)", re.S)
_KINDS = ("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789")


class _Glob:
    """A branch filter as one-character atoms, each with its quantifier, matched by tracking every atom the text can
    be at: linear in the text, where ``re`` backtracks polynomially on ``release/*a*a*a*a*a*a*b``."""

    def __init__(self, atoms: list[list[str]]) -> None:
        self.pattern = "".join(a + q for a, q in atoms) + r"\Z"  # the regex it matches as
        self._atoms = [(re.compile(a), q) for a, q in atoms]

    def _skip(self, at: set[int]) -> set[int]:
        """``at`` and each atom after a run of optional atoms (``?``, ``*``) that starts at one of them."""
        out: set[int] = set()
        for i in at:
            while i not in out:  # an index already in ``out`` had its run walked
                out.add(i)
                if i == len(self._atoms) or self._atoms[i][1] not in ("?", "*"):
                    break
                i += 1
        return out

    def match(self, text: str) -> bool:
        """The whole of ``text`` matches; the walk stops at the first character no atom it can be at takes."""
        at = self._skip({0})
        for ch in text:
            took: set[int] = set()
            for i in at:
                if i < len(self._atoms):
                    atom, q = self._atoms[i]
                    if atom.fullmatch(ch):
                        took.update((i, i + 1) if q in ("+", "*") else (i + 1,))
            at = self._skip(took)
            if not at:
                return False
        return len(self._atoms) in at


def _filter_regex(pattern: str) -> _Glob:
    """A GitHub branch filter: ``**`` matches any characters, ``*`` any but ``/``, ``?`` and ``+`` make what is
    before them optional or repeated, ``[...]`` is one of its letters, digits or ranges, ``\\`` escapes; a leading ``!`` is dropped.
    Stacked ``?`` and ``+`` fold into one quantifier (``a++`` is ``a+``, ``a?+`` is ``a*``, ``*+`` is ``*``).
    """
    atoms: list[list[str]] = []  # [one regex atom of one character, its quantifier]
    for m in _FILTER_TOKEN.finditer(pattern.removeprefix("!")):
        escaped, star, body, repeat, char = m.groups()
        if repeat and atoms:
            q = atoms[-1][1]
            atoms[-1][1] = repeat if not q else q if q in ("*", repeat) else "*"
        elif star:
            atoms.append(["." if star == "**" else "[^/]", "*"])
        elif body and (cls := _class(body)):
            atoms.append([cls, ""])
        else:  # a `[...]` _class refuses is its characters, one atom each
            atoms += [[re.escape(c), ""] for c in escaped or repeat or char or m[0]]
    return _Glob(atoms)


def _class(body: str) -> str | None:
    """``[body]`` as a regex class; None when a range runs backwards or mixes lowercase, uppercase and digits."""
    out = ""
    for lo, hi, one in re.findall(r"(\w)-(\w)|(.)", body):
        if not one and not any(lo in k and hi in k and lo <= hi for k in _KINDS):
            return None
        out += re.escape(one) if one else f"{lo}-{hi}"
    return f"[{out}]"

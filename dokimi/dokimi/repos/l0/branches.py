# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The branch names and patterns a tree's configuration names, read once per scan.

The path rule (:mod:`.core`) takes a path-like claim one of them names for a branch, not a path:
``maintenance/*`` beside a ``no-commit-to-branch`` hook with ``--pattern maintenance/.*``. Read from the
``args`` of that hook in the root ``.pre-commit-config.yaml``, and from the ``branches`` and
``branches-ignore`` filters of ``on.push`` and ``on.pull_request`` in the workflows GitHub runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from . import workflows

if TYPE_CHECKING:
    from .core import Scan

PRE_COMMIT = ".pre-commit-config.yaml"
HOOK = "no-commit-to-branch"
_OPTIONS = {"--branch": "--branch", "-b": "--branch", "--pattern": "--pattern", "-p": "--pattern"}
_EVENTS = ("push", "pull_request")
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
    rx: re.Pattern[str] | None
    why: str

    def names(self, claim: str, fills: list[str]) -> bool:
        """``claim`` is the text, or a pattern starting with its literal first directory matches every filling of it."""
        head = claim.split("/")[0]
        literal = "/" in claim and head != "" and not re.search(r"[*?\[<{]", head)
        led = literal and self.text.lstrip("^!").startswith(head + "/")
        return claim == self.text or (self.rx is not None and led and all(self.rx.match(f) for f in fills))


def branch_of(scan: Scan, claim: str, fills: list[str]) -> str | None:
    """The reason of the first source that names ``claim`` (``fills``: it with its wildcards filled in), else None."""
    return next((s.why for s in _sources(scan) if s.names(claim, fills)), None)


def _sources(scan: Scan) -> list[Source]:
    return scan.cached("branch_sources", lambda: [*_hook_sources(scan), *_filter_sources(scan)])


def _load(scan: Scan, path: str) -> Any:
    text, loader = scan.text(path), None
    try:
        loader = _Loader(text)  # its reader rejects a control character such as U+0001 already here
        return loader.get_single_data()
    except Exception:  # PyYAML's constructors raise more than YAMLError: a bad date, tag value, depth
        return None
    finally:
        if loader is not None:
            loader.dispose()


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
    """``(option, value, line)`` for each ``--branch X``, ``-b X``, ``--branch=X`` and the same of ``--pattern``/``-p``."""
    out = []
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        opt, eq, value = arg.partition("=")
        nxt = args[i + 1] if i + 1 < len(args) else None
        if eq and opt in ("--branch", "--pattern"):
            out.append((opt, value, getattr(arg, "line", 0)))
        elif arg in _OPTIONS and isinstance(nxt, str) and not nxt.startswith("-"):
            out.append((_OPTIONS[arg], str(nxt), getattr(nxt, "line", 0)))
    return out


def _hook_source(opt: str, value: str, line: int) -> Source | None:
    """A ``--branch`` name, or a ``--pattern`` the hook tries with ``re.match``; an invalid regex gives none."""
    where = f"{HOOK} ({PRE_COMMIT}:{line})"
    if opt == "--branch":
        return Source(value, None, f"branch of {where}")
    try:
        return Source(value, re.compile(value), f"branch pattern of {where}")
    except (re.error, OverflowError, RecursionError):  # `a{999999999999}`, a deep nest of groups
        return None


def _filter_sources(scan: Scan) -> list[Source]:
    """The ``branches`` and ``branches-ignore`` globs of ``on.push`` and ``on.pull_request``, workflow by workflow."""
    flows = workflows.runnable(scan)
    scan.preload(flows)
    out: list[Source] = []
    for path in flows:
        doc = _load(scan, path)
        on = (doc[True] if True in doc else doc.get("on")) if isinstance(doc, dict) else None  # YAML 1.1: `on` is true
        specs = [(e, on.get(e)) for e in _EVENTS] if isinstance(on, dict) else []  # `on: push` has no filters
        out += [Source(p, _filter_regex(p), f"branch filter of on.{event} ({path}:{getattr(p, 'line', 0)})")
                for event, spec in specs if isinstance(spec, dict)
                for key in _FILTERS for p in _list(spec.get(key)) if isinstance(p, str)]
    return out


_FILTER_TOKEN = re.compile(r"\\(.)|(\*\*?)|\[([a-zA-Z0-9-]+)\]|([?+])|(.)", re.S)
_KINDS = ("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789")


def _filter_regex(pattern: str) -> re.Pattern[str]:
    """A GitHub branch filter: ``**`` matches any characters, ``*`` any but ``/``, ``?`` and ``+`` make what is
    before them optional or repeated, ``[...]`` is one of its letters, digits or ranges, ``\\`` escapes; a leading ``!`` is dropped.
    Stacked ``?`` and ``+`` fold into one quantifier (``a++`` is ``a+``, ``a?+`` is ``a*``, ``*+`` is ``*``), as nesting backtracks exponentially.
    """
    atoms: list[list[str]] = []  # [one regex atom, its quantifier]
    for m in _FILTER_TOKEN.finditer(pattern.removeprefix("!")):
        escaped, star, body, repeat, char = m.groups()
        if repeat and atoms:
            q = atoms[-1][1]
            atoms[-1][1] = repeat if not q else q if q in ("*", repeat) else "*"
        elif star:
            atoms.append(["." if star == "**" else "[^/]", "*"])
        else:
            atoms.append([(_class(body) if body else None) or re.escape(escaped or repeat or char or m[0]), ""])
    return re.compile("".join(a + q for a, q in atoms) + r"\Z")


def _class(body: str) -> str | None:
    """``[body]`` as a regex class; None when a range runs backwards or mixes lowercase, uppercase and digits."""
    out = ""
    for lo, hi, one in re.findall(r"(\w)-(\w)|(.)", body):
        if not one and not any(lo in k and hi in k and lo <= hi for k in _KINDS):
            return None
        out += re.escape(one) if one else f"{lo}-{hi}"
    return f"[{out}]"

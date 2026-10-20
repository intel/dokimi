#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Dump and diff dokimi's user-facing CLI surface (commands, options, arguments).

The `cli-interface` workflow (.github/workflows/cli-interface.yml) dumps the
surface of the PR's base and of its head, diffs the two and, when they differ,
labels the PR `cli-interface-change` and posts a sticky comment listing every
added, removed or changed command and option. So each change to how users call
dokimi is visible in review and stays on record in the PR.

The dump walks the argparse tree `dokimi.cli.build_parser()` returns, of
whichever `dokimi` is first on sys.path: the workflow points PYTHONPATH at a
checkout of each ref in turn.

Limit: only the parser tree is compared. A validator that reads module state
when it runs (for example one that checks against `JUDGE_ROLES`) can reject
more input after a change without any change in the dump, so the diff is empty
for it. Review such changes by hand.

Usage:
  python scripts/cli_surface.py dump [OUT.json]
  python scripts/cli_surface.py diff BASE.json HEAD.json [--markdown]

`diff` exits 0 when the surfaces match, 1 when they differ.
"""

from __future__ import annotations

import argparse
import ast
import enum
import hashlib
import inspect
import json
import re
import sys
import textwrap
from pathlib import Path, PurePath
from typing import Any

# The attributes of an option or argument compared between base and head.
FIELDS = ("required", "is_flag", "nargs", "default", "type", "choices", "hidden", "action",
          "position", "const", "group_key", "group_required")


# Class attributes that name or locate the class rather than define it.
_CLASS_NOISE = frozenset({"__dict__", "__doc__", "__firstlineno__", "__module__", "__qualname__",
                          "__weakref__"})


def _filled(cell: Any) -> bool:
    try:
        _ = cell.cell_contents
    except ValueError:
        return False
    return True


def _code(fn: Any) -> str:
    """A function's code without docstrings, comments or layout: rewording them is no CLI change."""
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return "nosource"
    try:
        tree = ast.parse(src)
    except SyntaxError:  # e.g. a lambda, whose source is the whole line around it
        return src
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree)


def _value(value: Any, _seen: frozenset[int] = frozenset()) -> Any:
    """A JSON value standing for `value`, stable across runs (no object addresses).

    `_seen` holds the functions being hashed up the call chain: a validator that reaches
    itself through a closure cell is named, not hashed again.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_value(v, _seen) for v in value]
    if isinstance(value, dict):
        return {str(k): _value(v, _seen) for k, v in value.items()}
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(repr(_value(v, _seen)) for v in value)
    if isinstance(value, enum.Enum):
        return repr(value)
    if inspect.isfunction(value) and id(value) not in _seen:
        # A validator's name alone hides a loosened body: add a hash of its code.
        src = _code(value)
        # Captured values and defaults (a factory's `lo`, a keyword-only `lim`) change what the
        # function accepts too.
        seen = _seen | {id(value)}
        cells = [c.cell_contents for c in (value.__closure__ or ()) if _filled(c)]
        src += repr([_value(c, seen) for c in cells])
        src += repr(_value(value.__defaults__, seen)) + repr(_value(value.__kwdefaults__, seen))
        digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
        return f"{value.__module__}.{value.__qualname__}#{digest}"
    if inspect.isclass(value) and value.__module__ != "builtins" and id(value) not in _seen:
        # A class used as a type is hashed by its attributes (their code, through _value), so a
        # changed method or a new enum member changes it. Its source is not used: a class defined
        # in a module that is not in sys.modules has none.
        seen = _seen | {id(value)}
        attrs = {k: v for k, v in vars(value).items() if k not in _CLASS_NOISE}
        src = repr(_value(attrs, seen))
        if issubclass(value, enum.Enum):
            src += repr([(m.name, _value(m.value)) for m in value])
        digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
        return f"{value.__module__}.{value.__qualname__}#{digest}"
    if inspect.isfunction(value) and id(value) in _seen:  # reached again through its own closure
        return f"{value.__module__}.{value.__qualname__}"
    name = getattr(value, "__name__", None)
    if isinstance(name, str):
        return name
    text = repr(value)  # e.g. FileType('r'); object addresses would differ run to run
    return type(value).__name__ if re.search(r" at 0x[0-9a-fA-F]+", text) else text


def _param(action: argparse.Action, position: int | None, group_key: str | None = None,
           group_required: bool | None = None) -> dict[str, Any]:
    is_option = bool(action.option_strings)
    return {
        "position": position,  # a positional's index: swapping two is a change
        "key": "/".join(sorted(action.option_strings)) if is_option else action.dest,
        "kind": "option" if is_option else "argument",
        "required": bool(action.required),
        "is_flag": is_option and action.nargs == 0,
        "nargs": _value(action.nargs),
        "default": _value(action.default),
        "const": _value(action.const),
        "type": _value(action.type),
        "choices": sorted(repr(_value(c)) for c in action.choices) if action.choices is not None else None,
        "hidden": action.help == argparse.SUPPRESS,
        "action": type(action).__name__,
        "group_key": group_key,
        "group_required": group_required,
    }


def _command(parser: argparse.ArgumentParser, path: str) -> dict[str, Any]:
    node: dict[str, Any] = {"path": path, "params": [], "subcommands": {},
                            "subcommand_required": None, "allow_abbrev": parser.allow_abbrev}
    position = 0

    # Build a map from action to its mutually exclusive group (if any).
    action_to_group: dict[argparse.Action, tuple[str, bool]] = {}  # action -> (group_key, required)
    for group in parser._mutually_exclusive_groups:
        # Key the group by its members' keys, not creation order, so reordering groups
        # does not report unchanged options as changed.
        member_keys = []
        for action in group._group_actions:
            if action.option_strings:
                member_keys.append("/".join(sorted(action.option_strings)))
            else:
                member_keys.append(action.dest)
        group_key = "|".join(sorted(member_keys))
        for action in group._group_actions:
            action_to_group[action] = (group_key, group.required)

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            node["subcommand_required"] = bool(action.required)
            position += 1  # the subcommand takes a positional slot too
            for name, sub in sorted(action.choices.items()):
                node["subcommands"][name] = _command(sub, f"{path} {name}")
        else:
            # -h/--help and --version are kept (help text and the version string are not dumped)
            group_info = action_to_group.get(action)
            param_group = group_info[0] if group_info else None
            param_required = group_info[1] if group_info else None
            node["params"].append(_param(action, None if action.option_strings else position,
                                         param_group, param_required))
            position += 0 if action.option_strings else 1
    node["params"].sort(key=lambda p: (p["kind"], p["key"]))
    return node


def dump_surface() -> dict[str, Any]:
    """The command tree reachable from `dokimi --help`."""
    from dokimi.cli import build_parser

    return _command(build_parser(), "dokimi")


def _flatten(node: dict[str, Any], out: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out[node["path"]] = node
    for sub in node["subcommands"].values():
        _flatten(sub, out)
    return out


def _diff_params(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[str]:
    old_by = {p["key"]: p for p in old}
    new_by = {p["key"]: p for p in new}
    lines = [f"    - removed {old_by[k]['kind']} `{k}`" for k in sorted(old_by.keys() - new_by.keys())]
    lines += [f"    + added {new_by[k]['kind']} `{k}`" for k in sorted(new_by.keys() - old_by.keys())]
    for k in sorted(old_by.keys() & new_by.keys()):
        changed = [f"{f}: {old_by[k].get(f)!r} -> {new_by[k].get(f)!r}"
                   for f in FIELDS if old_by[k].get(f) != new_by[k].get(f)]
        if changed:
            lines.append(f"    ~ changed {new_by[k]['kind']} `{k}`: " + "; ".join(changed))
    return lines


def diff_surfaces(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """Human-readable diff lines; empty when the surfaces match."""
    old, new = _flatten(base, {}), _flatten(head, {})
    lines: list[str] = []
    for path in sorted(old.keys() - new.keys()):
        lines.append(f"- removed command `{path}`")
        lines += [f"    - {p['kind']} `{p['key']}`" for p in old[path]["params"]]
    for path in sorted(new.keys() - old.keys()):
        lines.append(f"+ added command `{path}`")
        lines += [f"    + {p['kind']} `{p['key']}`" for p in new[path]["params"]]
    for path in sorted(old.keys() & new.keys()):
        if old[path].get("subcommand_required") != new[path].get("subcommand_required"):
            lines.append(f"~ `{path}` subcommand required: "
                         f"{old[path].get('subcommand_required')!r} -> "
                         f"{new[path].get('subcommand_required')!r}")
        if old[path].get("allow_abbrev") != new[path].get("allow_abbrev"):
            # with it, `--ver` is accepted for `--version`
            lines.append(f"~ `{path}` option abbreviations allowed: "
                         f"{old[path].get('allow_abbrev')!r} -> {new[path].get('allow_abbrev')!r}")
        param_lines = _diff_params(old[path]["params"], new[path]["params"])
        if param_lines:
            lines.append(f"~ `{path}` parameters changed:")
            lines += param_lines
    return lines


def render(lines: list[str], *, markdown: bool) -> str:
    if not lines:
        return "No user-facing CLI surface changes detected.\n"
    body = "\n".join(lines)
    if markdown:
        return ("## CLI interface change detected\n\n"
                "This PR changes dokimi's user-facing CLI surface (commands and/or options):\n\n"
                f"```diff\n{body}\n```\n")
    return f"CLI interface change detected:\n{body}\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="write the CLI surface of the importable dokimi as JSON")
    d.add_argument("out", nargs="?", type=Path)
    c = sub.add_parser("diff", help="diff two dumps; exit 1 when they differ")
    c.add_argument("base", type=Path)
    c.add_argument("head", type=Path)
    c.add_argument("--markdown", action="store_true", help="format for a PR comment")
    args = p.parse_args(argv)
    if args.cmd == "dump":
        text = json.dumps(dump_surface(), indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    base = json.loads(args.base.read_text(encoding="utf-8"))
    head = json.loads(args.head.read_text(encoding="utf-8"))
    lines = diff_surfaces(base, head)
    sys.stdout.write(render(lines, markdown=args.markdown))
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())

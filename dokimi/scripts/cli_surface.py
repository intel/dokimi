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

Usage:
  python scripts/cli_surface.py dump [OUT.json]
  python scripts/cli_surface.py diff BASE.json HEAD.json [--markdown]

`diff` exits 0 when the surfaces match, 1 when they differ.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The attributes of an option or argument compared between base and head.
FIELDS = ("required", "is_flag", "nargs", "default", "type", "choices", "hidden", "action")


def _value(value: Any) -> Any:
    """A JSON value standing for `value`, stable across runs (no object addresses)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _value(v) for k, v in value.items()}
    name = getattr(value, "__name__", None)
    return name if isinstance(name, str) else type(value).__name__


def _param(action: argparse.Action) -> dict[str, Any]:
    is_option = bool(action.option_strings)
    return {
        "key": "/".join(sorted(action.option_strings)) if is_option else action.dest,
        "kind": "option" if is_option else "argument",
        "required": bool(action.required),
        "is_flag": is_option and action.nargs == 0,
        "nargs": _value(action.nargs),
        "default": _value(action.default),
        "type": _value(action.type),
        "choices": sorted(str(c) for c in action.choices) if action.choices else None,
        "hidden": action.help == argparse.SUPPRESS,
        "action": type(action).__name__,
    }


def _command(parser: argparse.ArgumentParser, path: str) -> dict[str, Any]:
    node: dict[str, Any] = {"path": path, "params": [], "subcommands": {}}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in sorted(action.choices.items()):
                node["subcommands"][name] = _command(sub, f"{path} {name}")
        elif not isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            node["params"].append(_param(action))
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
    lines = [f"- removed command `{p}`" for p in sorted(old.keys() - new.keys())]
    lines += [f"+ added command `{p}`" for p in sorted(new.keys() - old.keys())]
    for path in sorted(old.keys() & new.keys()):
        param_lines = _diff_params(old[path]["params"], new[path]["params"])
        if param_lines:
            lines.append(f"~ `{path}` options changed:")
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

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Cases of the CLI fault contract (tests/test_cli_fault_contract.py), generated from the parser tree.

`build_parser()` is the CLI surface; `docs/reference/cli-interface.json` is its dump and
tests/test_cli_flags.py keeps the two equal. Every option or operand whose help, metavar, type,
name or default names a file or directory is a path parameter, and each gets the hostile inputs of its
role: a reader of a file, a reader of a directory, or a writer of either.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Role = Literal["input-file", "input-dir", "output-file", "output-dir"]

# Faults by role. An output may be created where it is missing, so `missing` is for readers only;
# a writer or a directory gets `..`, an absolute path and an escaping link only when its help confines it to a root.
FAULTS: dict[Role, tuple[str, ...]] = {
    "input-file": ("missing", "directory", "unreadable", "dangling-link", "link-out-of-root",
                   "dotdot", "absolute", "empty", "invalid-utf8", "truncated-json", "json-list", "json-null", "json-text"),
    "input-dir": ("missing", "not-a-directory", "unreadable", "dangling-link"),
    "output-file": ("directory", "unwritable-parent"),
    "output-dir": ("not-a-directory", "unwritable-parent"),
}
ESCAPES = ("link-out-of-root", "dotdot", "absolute")
# Well-formed JSON of a shape the reader may not expect: the only break is a crash, as the command may accept the shape.
SHAPES = ("json-list", "json-null", "json-text")
# Faults in what a readable file holds. A reader that does not parse the file as JSON answers them as it answers a
# good file, and the contract test skips the case as not reached.
CONTENT = ("truncated-json", *SHAPES)
# Not a fault: the value a parameter gets when another one is under test. The contract test runs it once
# for each parameter, to tell a case that reached its path from one the command refused before reading it.
GOOD = "good"
# Faults that are valid input for this parameter, by case id, with the help text that says so.
INAPPLICABLE = {"eval inspect | path | not-a-directory": "A task directory, a dataset file, or a tree holding either"}

# Parameters whose help names a path but whose value is not one, or is one only behind a scheme
# (`local:<path>`) or another flag (`--kind subject`): an option string, or "command operand".
NOT_PATHS = frozenset({"--doc-source", "--metric", "--scenarios", "--resource-roles", "--arm", "--format",
                       "--fail-on", "--model", "--top-k", "--name", "--product", "--image",
                       "catalog show name", "skill check skill"})

_FILE_WORDS = re.compile(r"\b(files?|json|jsonl|yaml|csv|toml|path)\b", re.I)
_DIR_WORDS = re.compile(r"\b(director(y|ies)|dir|checkout|root|folder|tree)\b", re.I)
_OUTPUT = re.compile(r"^(output|write|where (to write|each)|also write|base output|markdown report|work directory|parent of)"
                     r"|\b(write (one|the|it)|to write)\b", re.I)
_CONFINED = re.compile(r"\b(relative to|under the)\b", re.I)
_DIR_METAVARS = frozenset({"DIR", "ROOT", "CHECKOUT"})
_FILE_METAVARS = frozenset({"PATH", "FILE", "REACH_JSON", "SKILL", "NAME=PATH"})
_OUTPUT_DESTS = re.compile(r"(^|_)(out|output)(_|$)|^(jsonl|cache_dir|cache_root|work_dir)$")
_DIR_DESTS = re.compile(r"_(dir|root)$")
# A parameter is a path by its name or default too, so one whose help is empty or names no file is still covered.
_PATH_DESTS = re.compile(r"(^|_)(out|output)(_|$)|_(file|path)$")
_PATH_DEFAULT = re.compile(r"^[\w.-]+(/[\w.-]+)+$|\.(json|jsonl|ya?ml|md|csv|toml|txt)$")


@dataclass(frozen=True)
class Param:
    """One path parameter of one leaf command: ``key`` is its longest option string, or an operand's dest."""

    command: str
    key: str
    role: Role
    prefix: str
    confined: bool


@dataclass(frozen=True)
class Case:
    """One hostile input for one path parameter."""

    param: Param
    fault: str

    @property
    def id(self) -> str:
        return f"{self.param.command} | {self.param.key} | {self.fault}"


def leaves(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()) -> Iterator[tuple[str, argparse.ArgumentParser]]:
    """Every leaf command as (``"answers generate"``, its parser); an alias's parser is walked once."""
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield " ".join(path), parser
        return
    seen: set[int] = set()
    for name, sub in sorted(subs[0].choices.items()):
        if id(sub) not in seen:
            seen.add(id(sub))
            yield from leaves(sub, (*path, name))


def _first(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return match.start() if match else len(text) + 1


def _role(action: argparse.Action, help_text: str) -> Role:
    metavar = str(action.metavar)
    is_dir = metavar in _DIR_METAVARS or bool(_DIR_DESTS.search(action.dest)) or (
        metavar not in _FILE_METAVARS and _first(_DIR_WORDS, help_text) < _first(_FILE_WORDS, help_text))
    output = bool(_OUTPUT.search(help_text) or _OUTPUT_DESTS.search(action.dest))
    if output:
        return "output-dir" if is_dir else "output-file"
    return "input-dir" if is_dir else "input-file"


def _is_path(action: argparse.Action, key: str, help_text: str, command: str) -> bool:
    if action.nargs == 0 or action.choices is not None or key in NOT_PATHS or f"{command} {key}" in NOT_PATHS:
        return False
    if action.type is Path:
        return True
    if action.type is not None:
        return False
    if str(action.metavar) in _DIR_METAVARS | _FILE_METAVARS or _DIR_DESTS.search(f"_{action.dest}"):
        return True
    if _PATH_DESTS.search(action.dest) or (isinstance(action.default, str) and _PATH_DEFAULT.search(action.default)):
        return True
    return bool(_FILE_WORDS.search(help_text) or _DIR_WORDS.search(help_text))


def _prefix(action: argparse.Action, help_text: str) -> str:
    if str(action.metavar) == "NAME=PATH":
        return "c="
    return "skill:" if help_text.startswith("skill:<path") else ""


def path_params(command: str, parser: argparse.ArgumentParser) -> list[Param]:
    """The path parameters of one leaf, in parser order."""
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction) or action.help == argparse.SUPPRESS:
            continue
        key = _key(action)
        help_text = action.help or ""
        if _is_path(action, key, help_text, command):
            found.append(Param(command, key, _role(action, help_text),
                               _prefix(action, help_text), bool(_CONFINED.search(help_text))))
    return found


def cases(parser: argparse.ArgumentParser) -> list[Case]:
    """Every (path parameter, fault) pair of every leaf command."""
    out = []
    for command, leaf in leaves(parser):
        for param in path_params(command, leaf):
            faults = FAULTS[param.role]
            if param.confined and param.role != "input-file":
                faults = faults + ESCAPES
            out.extend(c for c in (Case(param, fault) for fault in faults) if c.id not in INAPPLICABLE)
    return out


def can_chmod() -> bool:
    """Whether a mode-000 file is unreadable here: not on Windows, not as root."""
    return os.name != "nt" and getattr(os, "geteuid", lambda: 0)() != 0


class Unbuildable(Exception):
    """The platform cannot build this fault (no chmod refusal, no symlinks)."""


def _link(link: Path, target: Path) -> Path:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        raise Unbuildable(f"no symlink here: {exc}") from exc
    return link


def _locked(path: Path, mode: int) -> Path:
    if not can_chmod():
        raise Unbuildable("chmod does not refuse access here (Windows or root)")
    path.chmod(mode)
    return path


def _outside(outside: Path, is_dir: bool) -> Path:
    if is_dir:
        target = outside / "dir"
        target.mkdir(exist_ok=True)
        (target / "junk.txt").write_text("not a fixture\n", encoding="utf-8")
        return target
    target = outside / "bad.json"
    target.write_text("{ not json", encoding="utf-8")
    return target


def build(fault: str, role: Role, root: Path, outside: Path) -> str:
    """Make the fault under ``root`` (the working directory) and return the argument that names it."""
    if fault == GOOD:
        return _path_filler("good", role, root)
    is_dir = role.endswith("dir")
    builders = {
        "missing": lambda: "missing.json",
        "directory": lambda: (root / "a_dir").mkdir() or "a_dir",
        "not-a-directory": lambda: (root / "a_file.txt").write_text("text\n", encoding="utf-8") and "a_file.txt",
        "unreadable": lambda: _unreadable(root, is_dir),
        "unwritable-parent": lambda: _unwritable_parent(root, is_dir),
        "dangling-link": lambda: _link(root / "dangling", root / "nowhere").name,
        "link-out-of-root": lambda: _link(root / "escape", _outside(outside, is_dir)).name,
        "dotdot": lambda: os.path.relpath(_outside(outside, is_dir), root),
        "absolute": lambda: str(_outside(outside, is_dir)),
        "empty": lambda: (root / "empty.json").write_bytes(b"") or "empty.json",
        "invalid-utf8": lambda: (root / "bad_utf8.json").write_bytes(b"\xff\xfe\x80{") and "bad_utf8.json",
        "truncated-json": lambda: (root / "truncated.json").write_text('{"a": ', encoding="utf-8") and "truncated.json",
        "json-list": lambda: (root / "a_list.json").write_text("[]\n", encoding="utf-8") and "a_list.json",
        "json-null": lambda: (root / "a_null.json").write_text("null\n", encoding="utf-8") and "a_null.json",
        "json-text": lambda: (root / "a_text.json").write_text('"text"\n', encoding="utf-8") and "a_text.json",
    }
    return str(builders[fault]())


def _unreadable(root: Path, is_dir: bool) -> str:
    target = root / ("locked_dir" if is_dir else "locked.json")
    if is_dir:  # empty: the entries of a locked directory are not in the before-and-after tree
        target.mkdir()
    else:
        target.write_text("{}", encoding="utf-8")
    return _locked(target, 0).name


def _unwritable_parent(root: Path, is_dir: bool) -> str:
    parent = root / "readonly"
    parent.mkdir()
    _locked(parent, 0o555)
    return str(Path("readonly") / ("out_dir" if is_dir else "out.json"))


def unlock(root: Path) -> None:
    """Give back the modes a fault took away, so the tmp directory can be removed."""
    for path in [root, *root.rglob("*")] if root.exists() else []:
        if not path.is_symlink() and path.name in ("locked_dir", "locked.json", "readonly"):
            path.chmod(0o755)


# Exit codes a command's own section of docs/quickstart.md ("Exit codes") gives a bad input, where it is not 2 alone.
# `report summary` makes a bad input a not_run or invalid row and exits 0 (cmd_report_summary).
DOCUMENTED = {"repo run": frozenset({1, 2}), "eval check": frozenset({1, 2}), "report summary": frozenset({0, 2})}
# Cases that cannot reach their parameter here, with why.
NEEDS_MODULE = {"eval score ragas": "ragas"}
UNREACHABLE = {"repo skill-reach | --out-dir | unwritable-parent":
               "runs the harness binary before it writes, and PATH holds none"}
# The commands the docs name most often: the harness must find a path parameter on each.
CENTRAL = ("arms run", "skill novelty analyze", "tasks run", "repo compare", "benchmark run", "repo reach",
           "answers generate", "repo scan", "repo run", "eval check", "docs check", "catalog check",
           "baseline compare", "report generate", "eval score pointwise", "trace inspect")

FILLER_INT = re.compile(r"(int|usd|fraction|interval|age|floor|threshold|alpha)", re.I)
# Fillers for required options whose type checks a shape, and file contents that get past a first check.
FILLERS = {"--judge": "openai/m"}
FILE_FILLERS = {"--eval": '{"evaluations": [{"question_id": "q1"}]}'}
DEMO_SKILL = "---\nname: demo-skill\ndescription: A fixture skill.\n---\n# Demo skill\n"


def filler(action: argparse.Action, command: str, root: Path) -> str:
    """A value argparse accepts for a required parameter that is not under test."""
    if action.choices is not None:
        return str(next(iter(action.choices)))
    if _key(action) in FILLERS:
        return FILLERS[_key(action)]
    type_name = getattr(action.type, "__name__", "")
    if action.type in (int, float) or FILLER_INT.search(type_name):
        return "1"
    help_text = action.help or ""
    if not _is_path(action, _key(action), help_text, command):
        return "x"
    prefix = _prefix(action, help_text)
    if prefix == "skill:":
        return prefix + _path_filler(_key(action), "input-dir", root) + "/demo-skill"
    return prefix + _path_filler(_key(action), _role(action, help_text), root)


def _path_filler(key: str, role: Role, root: Path) -> str:
    if role == "input-file":
        name = f"fixture_{key.strip('-')}.json"
        (root / name).write_text(FILE_FILLERS.get(key, "{}"), encoding="utf-8")
        return name
    if role == "input-dir":  # a catalog of one skill, so a command that needs a SKILL.md gets past it
        skill = root / "fixture" / "demo-skill"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(DEMO_SKILL, encoding="utf-8")
        return "fixture"
    return "filler_out" if role == "output-dir" else "filler_out.json"


def argv(case: Case, parser: argparse.ArgumentParser, value: str, root: Path) -> list[str]:
    """The command line of one case: the hostile ``value`` for its parameter, a filler for every other required one."""
    positionals: list[str] = []
    options: list[str] = []
    for action in _required(parser, case.param.key):
        key = _key(action)
        text = case.param.prefix + value if key == case.param.key else filler(action, case.param.command, root)
        if action.option_strings:
            options += [key, text]
        else:
            positionals.append(text)
    return [*case.param.command.split(), *positionals, *options]


def _required(parser: argparse.ArgumentParser, target: str) -> list[argparse.Action]:
    chosen = []
    for group in parser._mutually_exclusive_groups:
        if group.required and not any(_key(a) == target for a in group._group_actions):
            chosen.append(group._group_actions[0])
    in_groups = {id(a) for g in parser._mutually_exclusive_groups for a in g._group_actions}
    for action in parser._actions:
        if _key(action) == target or (action.required and id(action) not in in_groups):
            chosen.append(action)
    return sorted(chosen, key=lambda a: bool(a.option_strings))


def _key(action: argparse.Action) -> str:
    return max(action.option_strings, key=len) if action.option_strings else action.dest


def tree(base: Path) -> set[str]:
    """Every path under ``base``, relative, links not followed."""
    return {str(p.relative_to(base)) for p in base.rglob("*")} if base.exists() else set()

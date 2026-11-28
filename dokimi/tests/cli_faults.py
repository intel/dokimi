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
# Where the filler of a required directory is made, and so what a confined path is relative to.
ROOT_DIR = "fixture"
# Faults that are valid input for this parameter, by case id, with the help text that says so.
INAPPLICABLE = {"eval inspect | path | not-a-directory": "A task directory, a dataset file, or a tree holding either",
                "skill novelty analyze | --placebo-text | truncated-json": "A text file: its content is read as text, never as JSON"}

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
NAMED_DIR = "NAME=DIR"  # the metavar of `--cache`
_FILE_METAVARS = frozenset({"PATH", "FILE", "REACH_JSON", "SKILL", NAMED_DIR})
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
    under: str = ""  # the directory, under the working directory, that a confined path is relative to


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
    if metavar == NAMED_DIR:  # `--cache`: a host directory the command may create, so an unwritable parent refuses it
        return "output-dir"
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
    if action.type in (int, float, bool) or FILLER_INT.search(getattr(action.type, "__name__", "")):
        return False  # a converter that makes a path of its text, like `_skill_ref`, still goes on to the checks below
    if str(action.metavar) in _DIR_METAVARS | _FILE_METAVARS or _DIR_DESTS.search(f"_{action.dest}"):
        return True
    if _PATH_DESTS.search(action.dest) or (isinstance(action.default, str) and _PATH_DEFAULT.search(action.default)):
        return True
    return bool(_FILE_WORDS.search(help_text) or _DIR_WORDS.search(help_text))


def _prefix(action: argparse.Action, help_text: str) -> str:
    if str(action.metavar) == NAMED_DIR:
        return "c="
    named = getattr(action.type, "__name__", "")
    return "skill:" if help_text.startswith("skill:<path") or named == "_skill_ref" else ""


def path_params(command: str, parser: argparse.ArgumentParser) -> list[Param]:
    """The path parameters of one leaf, in parser order."""
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction) or action.help == argparse.SUPPRESS:
            continue
        key = _key(action)
        help_text = action.help or ""
        if _is_path(action, key, help_text, command):
            confined = bool(_CONFINED.search(help_text))
            found.append(Param(command, key, _role(action, help_text), _prefix(action, help_text), confined,
                               ROOT_DIR if confined and _has_root(command, parser, key) else ""))
    return found


def _has_root(command: str, parser: argparse.ArgumentParser, key: str) -> bool:
    """Whether another required parameter of the command is a directory: the root a confined path is relative to."""
    return any(_key(a) != key and _is_path(a, _key(a), a.help or "", command) and _role(a, a.help or "") == "input-dir"
               for a in _required(parser, key))


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


def _catalog(directory: Path) -> Path:
    """A catalog of one skill, so a command that needs a SKILL.md gets past it."""
    skill = directory / "demo-skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(DEMO_SKILL, encoding="utf-8")
    return directory


def _outside(outside: Path, param: Param, name: str) -> Path:
    """Something outside the root. For a parameter confined to a root it is valid input, so that only the
    confinement can refuse it; any other reader may open it, and gets a file it must refuse as malformed."""
    if param.role.endswith("dir"):
        if param.confined:
            return _catalog(outside / "dir")
        (outside / "dir").mkdir(exist_ok=True)
        (outside / "dir" / "junk.txt").write_text("not a fixture\n", encoding="utf-8")
        return outside / "dir"
    target = outside / name / ("SKILL.md" if _is_skill_file(param) else "outside.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    good = DEMO_SKILL if _is_skill_file(param) else FILE_FILLERS.get(param.key, "{}")
    target.write_text(good if param.confined else "{ not json", encoding="utf-8")
    return target


def _is_skill_file(param: Param) -> bool:
    """A skill reader takes a directory or a file named SKILL.md, whatever the fault."""
    return param.prefix == "skill:" and param.role == "input-file"


def build(case: Case, root: Path, outside: Path) -> str:
    """Make the fault under ``root`` (the working directory) and return the argument that names it."""
    param, fault = case.param, case.fault
    base = root / param.under
    base.mkdir(parents=True, exist_ok=True)
    if fault == GOOD:
        if _is_skill_file(param):
            _catalog(root / ROOT_DIR)
            return f"{ROOT_DIR}/demo-skill"
        return _path_filler("good", param.role, base)

    def slot(name: str) -> Path:
        """Where the fault's file is made: a skill file must be called SKILL.md, so it gets a directory of its own."""
        if _is_skill_file(param):
            (base / fault).mkdir(exist_ok=True)
            return base / fault / "SKILL.md"
        return base / name

    if fault in ("missing", "dangling-link", "link-out-of-root", "dotdot", "absolute"):
        return _reference(case, base, outside, slot)
    if fault in CONTENT_BYTES:
        path = slot(CONTENT_NAMES[fault])
        path.write_bytes(CONTENT_BYTES[fault])
        return str(path.relative_to(base))
    return _state(case, base, slot)


def _reference(case: Case, base: Path, outside: Path, slot) -> str:
    """A path that names nothing, a link to nothing, or a way out of the root."""
    fault = case.fault
    if fault == "missing":
        return "missing.json"
    if fault == "dangling-link":
        return str(_link(slot("dangling"), base / "nowhere").relative_to(base))
    target = _outside(outside, case.param, fault)
    if fault == "link-out-of-root":
        return str(_link(slot("escape"), target).relative_to(base))
    return os.path.relpath(target, base) if fault == "dotdot" else str(target)


def _state(case: Case, base: Path, slot) -> str:
    """A directory where a file belongs, a file where a directory does, or a mode that refuses access."""
    is_dir = case.param.role.endswith("dir")
    if case.fault == "not-a-directory":
        path = base / "a_file.txt"
        path.write_text("text\n", encoding="utf-8")
    elif case.fault == "directory":
        path = slot("a_dir")
        path.mkdir()
    elif case.fault == "unreadable":
        path = slot("locked_dir" if is_dir else "locked.json")
        if is_dir:  # empty: the entries of a locked directory are not in the before-and-after tree
            path.mkdir()
        else:
            path.write_text("{}", encoding="utf-8")
        _locked(path, 0)
    else:  # unwritable-parent
        _locked(_dir(base / "readonly"), 0o555)
        return str(Path("readonly") / ("out_dir" if is_dir else "out.json"))
    return str(path.relative_to(base))


def _dir(path: Path) -> Path:
    path.mkdir()
    return path


CONTENT_BYTES = {"empty": b"", "invalid-utf8": b"\xff\xfe\x80{", "truncated-json": b'{"a": ',
                 "json-list": b"[]\n", "json-null": b"null\n", "json-text": b'"text"\n'}
CONTENT_NAMES = {"empty": "empty.json", "invalid-utf8": "bad_utf8.json", "truncated-json": "truncated.json",
                 "json-list": "a_list.json", "json-null": "a_null.json", "json-text": "a_text.json"}


def unlock(root: Path) -> None:
    """Give back the modes a fault took away, so the tmp directory can be removed: 0o755 to a directory, 0o644 to a file."""
    for path in [root, *root.rglob("*")] if root.exists() else []:
        if not path.is_symlink() and path.stat().st_mode & 0o777 in (0, 0o555):
            path.chmod(0o755 if path.is_dir() else 0o644)


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
MANIFEST_FILLER = '{"schema": "repo_manifest.v1", "id": "toy", "guidance": {"globs": ["**/AGENTS.md"]}}'
FILE_FILLERS = {"--eval": '{"evaluations": [{"question_id": "q1"}]}', "--manifest": MANIFEST_FILLER}
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
    if role == "input-dir":
        _catalog(root / ROOT_DIR)
        return ROOT_DIR
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


def tree(base: Path) -> dict[str, str]:
    """Every path under ``base``, relative, links not followed, with what tells a changed file from an untouched one."""
    found = {}
    for path in base.rglob("*") if base.exists() else []:
        info = path.lstat()
        if path.is_symlink():
            what = f"link to {os.readlink(path)}"
        else:
            what = "directory" if path.is_dir() else f"file of {info.st_size} bytes, written at {info.st_mtime_ns}"
        found[str(path.relative_to(base))] = what
    return found


def changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """What a run added, removed or changed, by path."""
    return ([p for p in sorted(after) if p not in before]
            + [f"removed {p}" for p in sorted(before) if p not in after]
            + [f"changed {p}" for p in sorted(before) if p in after and before[p] != after[p]])

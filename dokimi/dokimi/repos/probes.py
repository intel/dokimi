# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Probe events: a command that asks the host for its facts gets a ``probe`` event beside its ``command`` event.

    {"t": 4.0, "kind": "probe", "target": "cpu", "pattern": "nproc", "text": "nproc", "rc": 0}

One event per pattern the command matches, right after the command event, so one command may give both. A
pattern is matched on the commands the shell runs (the words of each simple command, its pipeline stages, the
code of ``python -c`` and of ``$( )`` substitutions), never on the text of an argument: ``grep nproc README``
runs ``grep``, not ``nproc``. The pattern syntax, in a manifest's ``probes`` or a run's ``--probes`` file:

- ``nproc``, ``pip list``, ``Get-CimInstance Win32_Processor``: a program, with the words that follow it (in
  any order, any case); ``sysctl -a | grep machdep.cpu``: consecutive pipeline stages;
- ``/proc/cpuinfo``: a file a reading command names (``cat``, ``grep``, ``head``, ...);
- ``py:os.cpu_count``: a call of that name in the code of a ``python -c`` one-liner.

A target listed in a manifest replaces that target's defaults, and a run's ``--probes`` replaces them again;
an empty list turns the target off. See ``docs/repo-evaluation-contract.md`` §2 and §4.1.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .shell_split import split_script

TARGETS = ("cpu", "gpu", "os", "memory", "packages", "env", "network")

DEFAULT_PROBES: dict[str, tuple[str, ...]] = {
    "cpu": ("lscpu", "/proc/cpuinfo", "nproc", "sysctl -a | grep machdep.cpu", "Get-CimInstance Win32_Processor",
            "py:os.cpu_count", "py:platform.processor"),
    "gpu": ("nvidia-smi", "rocm-smi", "py:torch.cuda.is_available"),
    "os": ("uname", "/etc/os-release", "sw_vers", "systeminfo"),
    "memory": ("free", "/proc/meminfo"),
    "packages": ("pip list", "pip show", "pip freeze", "conda list"),
    "env": ("env", "printenv"),
    "network": (),
}

_READERS = frozenset({"cat", "head", "tail", "less", "more", "nl", "sed", "grep", "egrep", "fgrep", "awk", "tac",
                      "strings", "wc", "rg"})
_WRAPPERS = frozenset({"time", "nohup", "sudo", "command", "exec", "builtin", "nice"})  # each runs the command after it
_OPTION_OPERANDS = frozenset({("nice", "-n"), ("sudo", "-u")})  # wrapper options that take a value word
_PYTHON = re.compile(r"python\d*(?:\.\d+)?|py|pypy\d*")
_PIP = re.compile(r"pip\d*(?:\.\d+)?")
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_SHELL_C = re.compile(r"-[a-z]*c[a-z]*")  # ``-c``, ``-lc``: the next word is a script
_LEX_SKIP = frozenset({"(", ")", "{", "}"})  # grouping a PowerShell or shell expression puts around a command
_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})  # ``|`` splits stages; the rest end a command

Stage = tuple[str, tuple[str, ...]]  # a simple command's program (normalised) and its words


@dataclass(frozen=True)
class Probe:
    """One compiled pattern. ``kind``: ``command`` (``stages``, consecutive pipeline stages), ``path`` (``text``, the
    file's path) or ``python`` (``call``, the regex the code of a ``-c`` one-liner must match)."""

    target: str
    text: str
    kind: str
    stages: tuple[Stage, ...] = ()
    call: re.Pattern[str] | None = None


def validate_config(raw: Any, where: str) -> dict[str, tuple[str, ...]]:
    """A ``probes`` mapping (a manifest's or a run file's) as target -> patterns; ``ValueError`` names what is wrong."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where}: expected a mapping of target to patterns, got {type(raw).__name__}")
    config: dict[str, tuple[str, ...]] = {}
    for target, patterns in raw.items():
        if target not in TARGETS:
            raise ValueError(f"{where}: unknown target {target!r} (known: {', '.join(TARGETS)})")
        if not isinstance(patterns, list) or not all(isinstance(p, str) and p.strip() for p in patterns):
            raise ValueError(f"{where}: {target} must be a list of non-empty pattern strings")
        for pattern in patterns:
            _compile(target, pattern.strip())  # a pattern that cannot be read is refused here, not per cell
        config[target] = tuple(p.strip() for p in patterns)
    return config


def load_probe_file(path: str) -> dict[str, tuple[str, ...]]:
    """A run's ``--probes`` file (YAML, or JSON, which is YAML) through :func:`validate_config`."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"--probes {path}: {exc}") from exc
    return validate_config(raw, f"--probes {path}")


def probes_for(*layers: Mapping[str, Sequence[str]] | None) -> tuple[Probe, ...]:
    """The compiled patterns of the defaults with each layer applied in turn: a target a layer names is replaced."""
    config: dict[str, Sequence[str]] = dict(DEFAULT_PROBES)
    for layer in layers:
        config.update(layer or {})
    return tuple(_compile(target, text.strip()) for target in TARGETS for text in config.get(target, ()))


def with_probes(events: list[dict[str, Any]], probes: Sequence[Probe]) -> list[dict[str, Any]]:
    """``events`` with a ``probe`` event after each ``command`` event whose command matches a pattern."""
    if not probes:
        return events
    out: list[dict[str, Any]] = []
    for event in events:
        out.append(event)
        if event.get("kind") == "command":
            out.extend(_probe_event(event, probe) for probe in _matching(str(event.get("text", "")), probes))
    return out


def _probe_event(command: dict[str, Any], probe: Probe) -> dict[str, Any]:
    event: dict[str, Any] = {"t": command.get("t"), "kind": "probe", "target": probe.target, "pattern": probe.text,
                             "text": command.get("text", ""), "rc": command.get("rc")}
    if "agent" in command:
        event["agent"] = command["agent"]
    return event


def _compile(target: str, text: str) -> Probe:
    if target not in TARGETS:
        raise ValueError(f"unknown probe target {target!r}")
    if text.startswith("py:"):
        name = text[3:].strip()
        if not _DOTTED.fullmatch(name):
            raise ValueError(f"{text!r}: py: takes a dotted Python name such as os.cpu_count")
        return Probe(target, text, "python", call=re.compile(r"(?<![\w.])" + re.escape(name) + r"\s*\("))
    if text.startswith("/"):
        if any(c.isspace() for c in text):
            raise ValueError(f"{text!r}: a path pattern is one word with no spaces")
        return Probe(target, text, "path")
    return Probe(target, text, "command", stages=tuple(_stage(part, text) for part in text.split("|")))


def _stage(part: str, text: str) -> Stage:
    if any(c in part for c in ";&<>"):
        raise ValueError(f"{text!r}: separate commands and redirections are not part of a pattern; use '|' for stages")
    try:
        words = [w for w in shlex.split(part) if w not in _LEX_SKIP]
    except ValueError as exc:
        raise ValueError(f"{text!r}: {exc}") from exc
    if not words:
        raise ValueError(f"{text!r}: a pattern names a program")
    return _program(words[0]), tuple(words[1:])


def _program(word: str) -> str:
    """A program's name as the shell or PowerShell finds it: no directory, no ``.exe``, no case."""
    name = posixpath.basename(word.replace("\\", "/")).casefold()
    return name[:-4] if name.endswith(".exe") else name


def _matching(command: str, probes: Sequence[Probe]) -> list[Probe]:
    """The probes the command runs, each once, in the order they are configured."""
    pipelines = [[list(_views(words)) for words in pipe] for pipe in _pipelines(command)]
    return [p for p in dict.fromkeys(probes) if any(_hits(p, pipe) for pipe in pipelines)]


def _pipelines(script: str) -> Iterator[list[list[str]]]:
    """Each pipeline of the script as its stages' words; the commands of its ``$( )`` and backtick substitutions too."""
    for part, _after, _in_subshell, _outer in split_script(script):
        for inner in _SUBSTITUTION.finditer(part):
            yield from _pipelines(inner.group(1) if inner.group(1) is not None else inner.group(2))
        stages = _stages(_SUBSTITUTION.sub(" ", part))  # the substitutions ran as their own commands, above
        if stages:
            yield stages
        for stage in stages:  # ``bash -lc 'nproc'``: the script a shell runs is a script of its own
            if len(stage) > 2 and _program(stage[0]) in _SHELLS and _SHELL_C.fullmatch(stage[1]):
                yield from _pipelines(stage[2])


def _stages(part: str) -> list[list[str]]:
    """A part's simple commands, cut at its unquoted pipes, each as its words (quotes removed, grouping dropped)."""
    try:
        lexer = shlex.shlex(part, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:  # an unbalanced quote: the words as they are typed
        tokens = part.split()
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            stages.append([])
        elif token not in _SEPARATORS and token not in _LEX_SKIP:
            stages[-1].append(token)
    return [stage for stage in stages if stage]


def _views(words: list[str]) -> Iterator[Stage]:
    """The command a simple command runs: its own program, then the command each wrapper (``time``, ``sudo``...) runs."""
    i = 0
    while i < len(words):
        while i < len(words) and _ASSIGN.match(words[i]):  # NAME=value words set variables for the command after them
            i += 1
        if i >= len(words):
            return
        yield _head(words[i], words[i + 1:])
        wrapper = _program(words[i])
        if wrapper not in _WRAPPERS:
            return
        i += 1
        while i < len(words) and words[i].startswith("-"):  # ``sudo -E``, ``nice -n 5``: options, then the program
            if wrapper == "command" and words[i] in ("-v", "-V"):
                return  # ``command -v nproc`` looks the program up, it does not run it
            i += 2 if (wrapper, words[i]) in _OPTION_OPERANDS else 1


def _head(word: str, args: list[str]) -> Stage:
    """A program and its words; ``python -m pip list`` is the program ``pip``."""
    program = _program(word)
    if _PYTHON.fullmatch(program) and args[:1] == ["-m"] and len(args) > 1 and _PIP.fullmatch(args[1]):
        return "pip", tuple(args[2:])
    return ("pip" if _PIP.fullmatch(program) else program), tuple(args)


def _hits(probe: Probe, pipeline: list[list[Stage]]) -> bool:
    if probe.kind == "command":
        return _in_order(probe.stages, pipeline)
    if probe.kind == "path":
        return any(prog in _READERS and probe.text in args for views in pipeline for prog, args in views)
    return any(_PYTHON.fullmatch(prog) and _calls(args, probe) for views in pipeline for prog, args in views)


def _calls(args: tuple[str, ...], probe: Probe) -> bool:
    """Whether the code of a ``python -c`` one-liner calls the probe's name."""
    if "-c" not in args or probe.call is None:
        return False
    code = args[args.index("-c") + 1:args.index("-c") + 2]
    return bool(code) and probe.call.search(code[0]) is not None


def _in_order(want: tuple[Stage, ...], pipeline: list[list[Stage]]) -> bool:
    """Whether the pattern's stages are consecutive stages of the pipeline (one stage: any stage of it)."""
    n = len(want)
    return any(all(_stage_runs(want[j], pipeline[i + j]) for j in range(n)) for i in range(len(pipeline) - n + 1))


def _stage_runs(want: Stage, views: list[Stage]) -> bool:
    """Whether one of the views is the wanted program with every wanted word among its words, any case."""
    program, words = want
    return any(prog == program and all(w.casefold() in {a.casefold() for a in args} for w in words)
                for prog, args in views)

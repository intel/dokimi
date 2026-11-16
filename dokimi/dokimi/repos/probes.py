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

import ast
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
_SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "sed", "awk"})  # the first operand is an expression, not a file
_WRAPPERS = frozenset({"time", "nohup", "sudo", "command", "exec", "builtin", "nice"})  # each runs the command after it
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!"})  # shell keywords before a command
_OPTION_OPERANDS = frozenset({("nice", "-n"), ("sudo", "-u")})  # wrapper options that take a value word
_VALUE_OPTIONS = frozenset({"-m", "-A", "-B", "-C"})  # a searcher's options whose next word is a value, not an operand
_PYTHON = re.compile(r"python\d*(?:\.\d+)?|py|pypy\d*")
_PIP = re.compile(r"pip\d*(?:\.\d+)?")
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_SHELL_C = re.compile(r"-[a-z]*c[a-z]*")  # ``-c``, ``-lc``: the next word is a script
_LEX_SKIP = frozenset({"(", ")", "{", "}"})  # grouping a PowerShell or shell expression puts around a command
_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})  # ``|`` splits stages; the rest end a command
_ESC = {c: chr(0xE000 + i) for i, c in enumerate("|;&<>$`\\()")}  # stands for a character a quote makes literal
_UNESC = {stand: c for c, stand in _ESC.items()}
_ESCAPABLE = frozenset("\"'\\$`|;&<>()")  # a backslash before one of these, outside single quotes, is an escape
_FD = "\ue0ff"  # stands for a file descriptor digit before a redirection, which is no word
_PY_VALUE_OPTIONS = frozenset({"-W", "-X"})  # python's own options whose next word is their value

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
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:  # deep nesting too
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
        codes, rest = _substitutions(_mask(part))
        for code in codes:  # the substitutions ran as their own commands
            yield from _pipelines(_restore(code))
        stages = _stages(rest)
        if stages:
            yield stages
        for stage in stages:  # ``bash -lc 'nproc'``, ``time bash -c nproc``: the script a shell runs is a script of its own
            for prog, args in _views(stage):
                script_arg = _shell_script(prog, args)
                if script_arg is not None:
                    yield from _pipelines(script_arg)


def _mask(part: str) -> str:
    """``part`` with the characters a quote or a backslash makes literal swapped for stand-ins: ``|;&<>`` in double
    quotes, every special character and ``$`` in single quotes, a backslash before a letter (a Windows path). The quotes
    stay, so shlex still reads each word, and a substitution is found only where ``$`` is live."""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(part):
        c = part[i]
        nxt = part[i + 1] if i + 1 < len(part) else ""
        if quote != "'" and c == "\\" and nxt == "\n":  # a line continuation: the shell reads the pair as nothing
            i += 2
            continue
        if quote != "'" and c == "\\" and nxt:
            if (quote == '"' and nxt in '"\\') or (not quote and nxt in "\"'\\ \t"):
                out.append(c + nxt)  # an escape shlex reads itself
            elif nxt in _ESCAPABLE:
                out.append(_ESC.get(nxt, nxt))  # the escaped character, literal
            else:
                out.append(_ESC["\\"])  # a backslash before a letter: a path's separator, not an escape
                i += 1
                continue
            i += 2
            continue
        if c in "'\"" and quote in ("", c):
            quote = "" if quote else c
            out.append(c)
        elif (quote == "'" and c in _ESC) or (quote == '"' and c in "|;&<>") or (not quote and c == "\\"):
            out.append(_ESC[c])
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _substitutions(part: str) -> tuple[list[str], str]:
    """The code of each ``$( )`` (balanced parentheses) and backtick substitution of a masked part, and the part without
    them: a substitution runs as its own command, so it is no word of this one."""
    codes: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(part):
        if part.startswith("$(", i):
            depth, j = 0, i + 1
            while j < len(part):
                depth += (part[j] == "(") - (part[j] == ")")
                if depth == 0:
                    break
                j += 1
            codes.append(part[i + 2:j])
            rest.append(" ")
            i = j + 1
        elif part[i] == "`":
            j = part.find("`", i + 1)
            j = len(part) if j < 0 else j
            codes.append(part[i + 1:j])
            rest.append(" ")
            i = j + 1
        else:
            rest.append(part[i])
            i += 1
    return codes, "".join(rest)


def _restore(text: str) -> str:
    return "".join(_UNESC.get(c, c) for c in text)


def _fd_masked(part: str) -> str:
    """``part`` with each file descriptor digit before a redirection (``2>``, ``2>&1``) swapped for a stand-in."""
    return re.sub(r"(?<![^\s|;&<>()])\d+(?=[<>])", lambda m: _FD * len(m.group()), part)


def _lexed(text: str) -> list[str]:
    """The tokens of a masked part: each word with its quotes removed, the operators as shlex reads them, and no
    file descriptor digit."""
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:  # an unbalanced quote: the words as they are typed
        tokens = text.split()
    return [token for token in tokens if set(token) != {_FD}]


def _close(stage: list[str], reads: list[str]) -> None:
    if stage:  # an input's target is read by the command, so it follows the command's words
        stage.extend(reads)


def _stages(part: str) -> list[list[str]]:
    """A part's simple commands, cut at its unquoted pipes, each as its words (quotes removed, grouping dropped). An
    output redirection's target is written, so it is no word; an input's target is read, so it follows the words."""
    stages: list[list[str]] = [[]]
    reads: list[str] = []
    target = ""  # the operator whose target is the next token: ``>`` (written) or ``<`` (read)
    for token in _lexed(_fd_masked(part)):
        if target:
            if target == "<":
                reads.append(_restore(token))
            target = ""
        elif token == "|":
            _close(stages[-1], reads)
            reads = []
            stages.append([])
        elif token.lstrip("&").startswith(">") or token == "<":
            target = token.lstrip("&")[:1]
        elif token not in _SEPARATORS and token not in _LEX_SKIP:
            stages[-1].append(_restore(token))
    _close(stages[-1], reads)
    return [stage for stage in stages if stage]


def _views(words: list[str]) -> Iterator[Stage]:
    """The command a simple command runs: its own program, then the command each wrapper (``time``, ``sudo``...) runs."""
    i = 0
    while i < len(words):
        while i < len(words) and (_ASSIGN.match(words[i]) or words[i] in _KEYWORDS):  # NAME=value, ``then``: the command is next
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
    """A program and its words; ``python -I -m pip list`` is the program ``pip``."""
    program = _program(word)
    if _PYTHON.fullmatch(program):
        run = _python_run(tuple(args))
        if run and run[0] == "-m" and run[1] and _PIP.fullmatch(run[1][0]):
            return "pip", run[1][1:]
    return ("pip" if _PIP.fullmatch(program) else program), tuple(args)


def _shell_script(prog: str, args: tuple[str, ...]) -> str | None:
    """The script a shell runs with ``-c`` (``bash --noprofile -c nproc``), or None."""
    if prog not in _SHELLS:
        return None
    for i, word in enumerate(args):
        if _SHELL_C.fullmatch(word):
            return args[i + 1] if i + 1 < len(args) else None
        if not word.startswith("-"):
            return None
    return None


def _python_run(args: tuple[str, ...]) -> tuple[str, tuple[str, ...]] | None:
    """What a python runs: ``("-c", the code and the words after it)``, ``("-m", the module and the words after it)``,
    or None for a script. Its options are read up to the first word that is not one, as python reads them."""
    i = 0
    while i < len(args):
        word = args[i]
        if word in ("-c", "-m"):
            return word, args[i + 1:]
        if word.startswith("-c"):  # ``-cimport os``: the code is in the same word
            return "-c", (word[2:],) + args[i + 1:]
        if word == "--" or not word.startswith("-"):
            return None
        i += 2 if word in _PY_VALUE_OPTIONS else 1
    return None


def _hits(probe: Probe, pipeline: list[list[Stage]]) -> bool:
    if probe.kind == "command":
        return _in_order(probe.stages, pipeline)
    if probe.kind == "path":
        return any(prog in _READERS and probe.text in _operands(prog, args) for views in pipeline for prog, args in views)
    return any(_PYTHON.fullmatch(prog) and _calls(args, probe) for views in pipeline for prog, args in views)


def _operands(program: str, args: tuple[str, ...]) -> tuple[str, ...]:
    """The words of a reader that can name a file: a searcher's search expression (its ``-e`` values, else its first operand) is not one."""
    if program not in _SEARCHERS:
        return args
    if "-e" in args:
        return tuple(a for i, a in enumerate(args) if a != "-e" and args[i - 1:i] != ("-e",))
    first = next((i for i, a in enumerate(args) if not a.startswith("-") and (args[i - 1] if i else "") not in _VALUE_OPTIONS), None)
    return args if first is None else args[:first] + args[first + 1:]


def _calls(args: tuple[str, ...], probe: Probe) -> bool:
    """Whether the code of a ``python -c`` one-liner calls the probe's name; a string that only spells the call is no call."""
    run = _python_run(args)
    if probe.call is None or run is None or run[0] != "-c" or not run[1]:
        return False
    code = run[1][0]
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):  # code that is no Python we can read: the name as it is written
        return probe.call.search(code) is not None
    name = probe.text[3:].strip()
    return any(isinstance(node, ast.Call) and ast.unparse(node.func) == name for node in ast.walk(tree))


def _in_order(want: tuple[Stage, ...], pipeline: list[list[Stage]]) -> bool:
    """Whether the pattern's stages are consecutive stages of the pipeline (one stage: any stage of it)."""
    n = len(want)
    return any(all(_stage_runs(want[j], pipeline[i + j]) for j in range(n)) for i in range(len(pipeline) - n + 1))


def _stage_runs(want: Stage, views: list[Stage]) -> bool:
    """Whether one of the views is the wanted program with every wanted word among its words, any case."""
    program, words = want
    return any(prog == program and all(w.casefold() in {a.casefold() for a in args} for w in words)
                for prog, args in views)

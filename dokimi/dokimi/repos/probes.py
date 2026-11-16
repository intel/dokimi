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

from .shell_split import split_script, substitution_end

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
_WRAPPER_VALUES = {"sudo": frozenset({"-u", "-g", "-h", "-p", "-r", "-t", "-C", "-D", "-R", "-T", "-U"}),
                   "nice": frozenset({"-n", "--adjustment"}), "exec": frozenset({"-a"}),
                   "time": frozenset({"-f", "-o", "--format", "--output"})}  # options that take the next word
_PATTERN_READERS = frozenset({"grep", "egrep", "fgrep", "rg", "sed", "awk"})  # the first operand is a pattern or script
_GREP_VALUES = {"-e", "-f", "-m", "-A", "-B", "-C", "--regexp", "--file", "--max-count", "--after-context",
                "--before-context", "--context"}
_READER_VALUES = {"head": {"-n", "-c", "--lines", "--bytes"}, "tail": {"-n", "-c", "--lines", "--bytes"},
                  "grep": _GREP_VALUES, "egrep": _GREP_VALUES, "fgrep": _GREP_VALUES,
                  "rg": _GREP_VALUES | {"-g", "-t", "-T", "--glob", "--type", "--type-not"},
                  "sed": {"-e", "-f", "-l", "--expression", "--file", "--line-length"},
                  "awk": {"-F", "-v", "-f", "--file", "--assign", "--field-separator"}}  # options that take the next word
_PATTERN_OPTIONS = frozenset({"-e", "-f", "--regexp", "--file", "--expression"})  # the pattern, script or a file of it comes from the option, not the first operand
_REDIRECT = re.compile(r"[<>&]*[<>][<>&]*")
_OPERATOR = "\0"  # marks a word that is a shell operator, as against a quoted ``'>'``
_PYTHON = re.compile(r"python\d*(?:\.\d+)?|py|pypy\d*")
_PIP = re.compile(r"pip\d*(?:\.\d+)?")
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1")
_SHELL_LINE = re.compile(r"\s*(?:(?:sudo|exec|command|time)\s+)*(?:bash|sh|zsh|dash)\b")  # a heredoc fed to a shell runs
_PUNCT = frozenset("();<>&|")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_SHELL_C = re.compile(r"-[a-z]*c[a-z]*")  # ``-c``, ``-lc``: the next word is a script
_LEX_SKIP = frozenset({"(", ")", "{", "}"})  # grouping a PowerShell or shell expression puts around a command
_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})  # ``|`` splits stages; the rest end a command

Stage = tuple[str, tuple[str, ...]]  # a simple command's program (normalised) and its words


@dataclass(frozen=True)
class Probe:
    """One compiled pattern. ``kind``: ``command`` (``stages``, consecutive pipeline stages), ``path`` (``text``, the
    file's path) or ``python`` (``call``, the dotted name a call in the code of a ``-c`` one-liner must have)."""

    target: str
    text: str
    kind: str
    stages: tuple[Stage, ...] = ()
    call: str | None = None


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
        return Probe(target, text, "python", call=name)
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
    never = False  # the part after ``true ||`` never runs; one after any other command may
    for part, after, _in_subshell, _outer in split_script(_without_heredocs(script)):
        skipped, never = never, after == "||" and part.strip() in ("true", ":")
        if skipped:
            continue
        stages, inner = command_stages(part)
        for body in inner:
            yield from _pipelines(body)
        if stages:
            yield stages
        for stage in stages:  # ``sudo bash -lc 'nproc'``: the script a shell runs is a script of its own
            for rest in command_words(stage):
                if len(rest) > 2 and _program(rest[0]) in _SHELLS and _SHELL_C.fullmatch(rest[1]):
                    yield from _pipelines(rest[2])


def _without_heredocs(script: str) -> str:
    """The script without the bodies of its heredocs, which are data (``cat <<EOF > run.sh``) unless a shell reads them;
    the ``$( )`` and backtick substitutions of an unquoted body ran, so they stay."""
    if "<<" not in script:
        return script
    out: list[str] = []
    end: str | None = None
    body: list[str] = []
    expands = False
    for line in script.split("\n"):
        if end is not None:
            if line.strip() == end:
                out.extend(_expansions("\n".join(body)) if expands else [])
                end, body = None, []
            else:
                body.append(line)
            continue
        out.append(line)
        m = _HEREDOC.search(line)
        if m and not _SHELL_LINE.match(line):
            end, expands = m.group(2), not m.group(1)
    return "\n".join(out)


def _expansions(body: str) -> list[str]:
    """The ``$( )`` and backtick substitutions of an unquoted heredoc body, as text."""
    found: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 1
        elif body[i] in "$`" and (sub := _substitution(body, i)):
            found.append(body[i:sub[1] + 1])
            i = sub[1]
        i += 1
    return found


def command_stages(part: str) -> tuple[list[list[str]], list[str]]:
    """A part's simple commands, cut at its unquoted pipes, each as its words (quotes removed, grouping dropped), and
    the bodies of the ``$( )`` and backtick substitutions outside single quotes: they ran as commands of their own."""
    stages: list[list[str]] = [[]]
    bodies: list[str] = []
    word: list[str] = []
    started = False  # a quoted empty string is a word
    bare = True  # the word so far has no quote or escape in it
    quote, i = "", 0
    while i < len(part):
        c = part[i]
        if c == "'" and not quote:  # nothing inside single quotes is special
            end = part.find("'", i + 1)
            end = len(part) if end < 0 else end
            word.append(part[i + 1:end])
            started, bare = True, False
            i = end
        elif c == "\\" and (part[i + 1:i + 2] in ("$", "`", '"') or (not quote and part[i + 1:i + 2] in tuple("|;&<>()"))):
            word.append(part[i + 1])  # an escaped ``$(`` or ``|`` is text; any other backslash is a path separator
            started, bare = True, False
            i += 1
        elif c in "$`" and (sub := _substitution(part, i)):
            bodies.append(sub[0])
            i = sub[1]
        elif c == quote:
            quote = ""
        elif c == '"' and not quote:
            quote, started, bare = c, True, False
        elif quote or c not in " \t\n" + "".join(_PUNCT):
            word.append(c)
            started = True
        else:
            if c in "<>" and bare and "".join(word).isdigit():  # ``2>file``: the 2 names a descriptor, it is no word
                word.clear()
                started = False
            _end_word(stages, word, started)
            started, bare = False, True
            i = _operator(part, i, stages)
        i += 1
    _end_word(stages, word, started)
    return [stage for stage in stages if stage], bodies


def _end_word(stages: list[list[str]], word: list[str], started: bool) -> None:
    if started and "".join(word) not in _LEX_SKIP:
        stages[-1].append("".join(word))
    word.clear()


def _substitution(part: str, i: int) -> tuple[str, int] | None:
    """The body of the ``$( )`` or backtick substitution at ``i`` and the index of its last character; ``None``
    where ``$`` or a backtick starts none."""
    if part[i] == "$":
        if part[i + 1:i + 2] != "(":
            return None
        end = substitution_end(part, i + 2)
        return part[i + 2:end - 1], end - 1
    end = part.find("`", i + 1)
    end = len(part) if end < 0 else end
    return part[i + 1:end], end


def _operator(part: str, i: int, stages: list[list[str]]) -> int:
    """Take the run of operator characters at ``i`` (a blank is none): a pipe starts a stage, a list separator or a
    grouping parenthesis is dropped, a redirection stays a word; the index of the run's last character."""
    run = part[i].strip()
    if not run:
        return i
    while part[i + 1:i + 2] in _PUNCT:
        i += 1
        run += part[i]
    if "|" in run:
        stages.append([])
    elif run and run not in _SEPARATORS and not set(run) <= set("()"):
        stages[-1].append(_OPERATOR + run)
    return i


def split_redirects(words: Sequence[str]) -> tuple[list[str], list[str]]:
    """The words without their redirections (an operator and its target), and the targets of ``<`` redirections: the
    files the command reads."""
    plain: list[str] = []
    reads: list[str] = []
    i = 0
    while i < len(words):
        if words[i].startswith(_OPERATOR) and _REDIRECT.fullmatch(words[i][1:]):
            if words[i] == _OPERATOR + "<" and i + 1 < len(words):
                reads.append(words[i + 1])
            i += 2
            continue
        plain.append(words[i])
        i += 1
    return plain, reads


def _views(words: list[str]) -> Iterator[Stage]:
    """The command a simple command runs: its own program, then the command each wrapper (``time``, ``sudo``...) runs."""
    for rest in command_words(words):
        yield _head(rest[0], rest[1:])


def command_words(words: list[str]) -> Iterator[list[str]]:
    """The words of the command a simple command runs, from its program on, then those of the command each wrapper
    (``time``, ``sudo -n``, ``nice -n 5``...) runs: its options are not the program."""
    i = 0
    while i < len(words):
        while i < len(words) and _ASSIGN.match(words[i]):  # NAME=value words set variables for the command after them
            i += 1
        if i >= len(words):
            return
        yield words[i:]
        wrapper = _program(words[i])
        if wrapper not in _WRAPPERS:
            return
        i += 1
        while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
            i += 2 if words[i] in _WRAPPER_VALUES.get(wrapper, ()) else 1


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
        return any(prog in _READERS and probe.text in _operands(prog, args) for views in pipeline for prog, args in views)
    return any(_PYTHON.fullmatch(prog) and _calls(args, probe) for views in pipeline for prog, args in views)


def _operands(program: str, args: tuple[str, ...]) -> list[str]:
    """The files a reading command names: not its options, their values, or the pattern ``grep`` and ``sed`` search for."""
    values = _READER_VALUES.get(program, ())
    needs_pattern = program in _PATTERN_READERS
    plain, files = split_redirects(args)
    i = 0
    while i < len(plain):
        word = plain[i]
        if word.startswith("-") and len(word) > 1:
            if needs_pattern and (word.split("=", 1)[0] if word.startswith("--") else word[:2]) in _PATTERN_OPTIONS:
                needs_pattern = False
            i += 2 if word in values else 1
            continue
        if needs_pattern:
            needs_pattern = False
        else:
            files.append(word)
        i += 1
    return files


def _calls(args: tuple[str, ...], probe: Probe) -> bool:
    """Whether the ``-c`` code of a Python command calls the probe's name, as code (not in a string or a comment)."""
    if "-c" not in args or probe.call is None:
        return False
    at = args.index("-c")
    if any(not w.startswith("-") for w in args[:at]):  # after a script name, ``-c`` is the script's own
        return False
    try:
        tree = ast.parse(args[at + 1]) if at + 1 < len(args) else None
    except (SyntaxError, ValueError):
        return False
    return tree is not None and any(_dotted(node.func) == probe.call for node in ast.walk(tree) if isinstance(node, ast.Call))


def _dotted(node: ast.expr) -> str:
    """``os.cpu_count`` for the expression ``os.cpu_count``; ``""`` for anything that is not a dotted name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(parts)]) if isinstance(node, ast.Name) else ""


def _in_order(want: tuple[Stage, ...], pipeline: list[list[Stage]]) -> bool:
    """Whether the pattern's stages are consecutive stages of the pipeline (one stage: any stage of it)."""
    n = len(want)
    return any(all(_stage_runs(want[j], pipeline[i + j]) for j in range(n)) for i in range(len(pipeline) - n + 1))


def _stage_runs(want: Stage, views: list[Stage]) -> bool:
    """Whether one of the views is the wanted program with every wanted word among its words, any case."""
    program, words = want
    return any(prog == program and all(w.casefold() in {a.casefold() for a in args} for w in words)
                for prog, args in views)

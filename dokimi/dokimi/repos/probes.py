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
    "cpu": ("lscpu", "/proc/cpuinfo", "nproc", "sysctl", "Get-CimInstance Win32_Processor",
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
_WRAPPERS = frozenset({"time", "nohup", "sudo", "command", "exec", "builtin", "nice", "timeout", "xargs", "doas", "stdbuf"})  # each runs
# the command after it
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!"})  # shell keywords before a command
_OPTION_OPERANDS = frozenset({("doas", "-u"), ("doas", "-C"), ("stdbuf", "-i"), ("stdbuf", "-o"), ("stdbuf", "-e"),
                              ("nice", "-n"), ("sudo", "-u"), ("timeout", "-s"), ("timeout", "-k"),
                              ("xargs", "-n"), ("xargs", "-I"), ("xargs", "-L"), ("xargs", "-P"), ("xargs", "-d"),
                              ("xargs", "-E"), ("xargs", "-s"), ("xargs", "-a")})  # wrapper options that take a value
_ENV_VALUES = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})  # env options with a value word
_RUN_VALUES = frozenset({"-n", "--name", "-p", "--prefix", "--python", "--with", "--directory", "--project",
                         "--env-file"})  # the options of ``conda run`` and ``uv run`` that take a value word
_PWSH = frozenset({"pwsh", "powershell"})  # PowerShell: its script follows ``-Command`` (``-c``), any case
_PWSH_VALUES = frozenset({"-executionpolicy", "-ep", "-workingdirectory", "-wd", "-settingsfile", "-configurationname",
                          "-inputformat", "-outputformat"})  # PowerShell options whose next word is their value
_GREP_VALUES = frozenset({"-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--after-context", "--before-context",
                          "--context", "--label", "--binary-files", "--directories", "--devices", "--include", "--exclude",
                          "--exclude-from", "--exclude-dir", "--group-separator"})
_RG_VALUES = frozenset({"-g", "--glob", "--iglob", "-t", "--type", "-T", "--type-not", "--type-add", "--type-clear", "-m",
                        "--max-count", "-A", "-B", "-C", "--after-context", "--before-context", "--context", "-M",
                        "--max-columns", "-j", "--threads", "-E", "--encoding", "-r", "--replace", "-d", "--max-depth",
                        "--sort", "--sortr", "--ignore-file", "--pre", "--pre-glob", "--color", "--colors", "--engine",
                        "--max-filesize", "--path-separator", "--context-separator", "--field-context-separator",
                        "--field-match-separator", "--dfa-size-limit", "--regex-size-limit", "--hyperlink-format",
                        "--hostname-bin"})
_GIT_GREP_VALUES = frozenset({"-m", "-A", "-B", "-C", "--max-count", "--after-context", "--before-context", "--context",
                              "--max-depth", "--threads"})  # git grep's own: no -d or -D, and --max-depth takes a word
_SEARCH_VALUES = {"grep": _GREP_VALUES, "egrep": _GREP_VALUES, "fgrep": _GREP_VALUES, "rg": _RG_VALUES,
                  "git grep": _GIT_GREP_VALUES, "awk": frozenset({"-F", "-v"})}  # a searcher's options whose next word is a value, not an operand
_PATTERN_FILE = frozenset({"-f", "--file"})  # a searcher's option whose value is a file it reads, and no expression
_EXPRESSION = frozenset({"-e", "--regexp", "--expression"})  # a searcher's option whose value is the search expression
_GNU_GREP_LONG = frozenset({
    "basic-regexp", "extended-regexp", "fixed-regexp", "fixed-strings", "perl-regexp", "after-context", "before-context",
    "binary-files", "byte-offset", "context", "color", "colour", "count", "dereference-recursive", "devices", "directories",
    "exclude", "exclude-from", "exclude-dir", "file", "files-with-matches", "files-without-match", "group-separator",
    "help", "include", "ignore-case", "no-ignore-case", "initial-tab", "label", "line-buffered", "line-number",
    "line-regexp", "max-count", "no-filename", "no-group-separator", "no-messages", "null", "null-data", "only-matching",
    "quiet", "recursive", "regexp", "invert-match", "silent", "text", "binary", "unix-byte-offsets", "version",
    "with-filename", "word-regexp"})  # GNU grep's long options
_GIT_GREP_LONG = frozenset({
    "cached", "no-index", "index", "untracked", "exclude-standard", "recurse-submodules", "invert-match", "ignore-case",
    "word-regexp", "text", "textconv", "recursive", "max-depth", "extended-regexp", "basic-regexp", "fixed-strings",
    "perl-regexp", "line-number", "column", "full-name", "files-with-matches", "name-only", "files-without-match", "null",
    "only-matching", "count", "color", "break", "heading", "context", "before-context", "after-context", "threads",
    "show-function", "function-context", "and", "or", "not", "quiet", "all-match", "open-files-in-pager", "ext-grep",
    "max-count"})  # git grep's long options
_PYTHON = re.compile(r"python\d*(?:\.\d+)?|py|pypy\d*")
_PIP = re.compile(r"pip\d*(?:\.\d+)?")
_ASSIGN = re.compile(r"[A-Za-z_]\w*=")
_PS_ASSIGN = re.compile(r"\$\w+=")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_INFO_OPTIONS = frozenset({"--help", "--version", "-V"})  # a wrapper given one prints text and runs nothing
_HEREDOC = re.compile(r"<<-?[ \t]*(?P<tag>'[^']*'|\"[^\"]*\"|\\?[\w.-]+)")
_GROUPED = re.compile(r"[{}()]|\b(?:if|then|else|elif|fi|do|done|while|until|case|esac)\b")  # a group or a block
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


def effective_patterns(*layers: Mapping[str, Sequence[str]] | None) -> dict[str, list[str]]:
    """Each target's patterns once the layers are applied, as ``probes_for`` applies them (for the cell's record)."""
    config: dict[str, Sequence[str]] = dict(DEFAULT_PROBES)
    for layer in layers:
        config.update(layer or {})
    return {target: [text.strip() for text in config.get(target, ())] for target in TARGETS}


def probes_for(*layers: Mapping[str, Sequence[str]] | None) -> tuple[Probe, ...]:
    """The compiled patterns of the defaults with each layer applied in turn: a target a layer names is replaced."""
    config: dict[str, Sequence[str]] = dict(DEFAULT_PROBES)
    for layer in layers:
        config.update(layer or {})
    return tuple(_compile(target, text.strip()) for target in TARGETS for text in config.get(target, ()))


def with_probes(events: list[dict[str, Any]], probes: Sequence[Probe],
                failures: list[str] | None = None) -> list[dict[str, Any]]:
    """``events`` with a ``probe`` event after each ``command`` event whose command matches a pattern. A command the
    matcher cannot read gets no probe event and adds a note to ``failures``; the other commands keep theirs."""
    if not probes:
        return events
    out: list[dict[str, Any]] = []
    for event in events:
        out.append(event)
        if event.get("kind") == "command" and not event.get("denied"):
            try:
                matched = _matching(str(event.get("text", "")), probes)
            except Exception as exc:  # noqa: BLE001  # one command nested too deep must not cost the others their probes
                if failures is not None:
                    failures.append(f"probe matching failed ({type(exc).__name__}): {str(exc)[:200]}")
                continue
            out.extend(_probe_event(event, probe) for probe in matched)
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
    launched = _launched(words, 0)  # ``uv run pip list`` is the pattern ``pip list``: the program the command runs
    if launched is not None:
        words = words[launched:]
    return _head(words[0], words[1:])  # ``pip3 list`` and ``python -m pip list`` are ``pip list``, as commands are


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
    status: bool | None = None  # the exit status of the last part that ran; None when it is not known
    before = ""  # the operator before this part
    text = _without_heredocs(script)
    flat = not _GROUPED.search(_substitutions(_mask(text))[1])  # a group or a block may hold an exit that does not run: then nothing ends the scan
    for part, after, in_subshell, _outer in split_script(text):
        conditional = before in ("||", "&&") and status is None  # the part runs only if the one before it did, or did not, succeed
        skipped, before = (before == "||" and status is True) or (before == "&&" and status is False), after
        if skipped:  # a part after ``||`` that succeeded, or after ``&&`` that failed, never runs
            continue
        status = {"true": True, ":": True, "false": False}.get(part.strip())

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

        if flat and not conditional and not in_subshell and after != "&" and len(stages) == 1 and _ends_shell(rest):
            return


def _ends_shell(part: str) -> bool:
    words = _stages(part, include_reads=False)
    stages = list(_views(words[0])) if words else []
    return any(program == "exit" for program, _ in stages) or any(program == "exec" for program, _ in stages[:-1])


def _without_heredocs(script: str) -> str:
    """Keep command text and expansions, skipping each delimiter's data in shell order."""
    if "<<" not in script:
        return script
    out: list[str] = []
    lines = iter(script.split("\n"))
    quote = ""
    for line in lines:
        opening = len(out)
        out.append(line)
        expansions = []
        openers, quote = _openers(line, quote)
        for match in openers:
            tag = match.group("tag")
            quoted = tag.startswith(("'", '"', "\\"))
            delimiter = tag[1:-1] if tag.startswith(("'", '"')) else tag.removeprefix("\\")
            body = []
            for data in lines:
                if (data.lstrip("\t") if match.group(0).startswith("<<-") else data) == delimiter:
                    break
                body.append(data)
            prefix = line[:match.start()]
            words = command_stages(prefix)
            if match is openers[-1] and words and _reads_script_from_stdin(words[-1]):
                out.extend(body)
            elif not quoted:
                expansions.extend(_expansions("\n".join(body)))
        if expansions:
            at = openers[0].start()
            out[opening] = line[:at] + " ".join(expansions) + " " + line[at:]
    return "\n".join(out)


def _reads_script_from_stdin(words: list[str]) -> bool:
    for program, args in _views(words):
        if program in _SHELLS:
            return all(re.fullmatch(r"-[seuxl]+|--norc|--noprofile", word) for word in args)
    return False


def _openers(line: str, quote: str) -> tuple[list[re.Match[str]], str]:
    """Read unquoted heredoc operators and carry an open shell quote to the next line."""
    matches = []
    i = 0
    while i < len(line):
        char = line[i]
        if char == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#" and (not i or line[i - 1].isspace()):
            break
        elif line.startswith("((", i):
            end = line.find("))", i + 2)
            i = len(line) if end < 0 else end + 2
            continue
        elif line.startswith("<<<", i):
            i += 3
            continue
        elif match := _HEREDOC.match(line, i):
            matches.append(match)
            i = match.end()
            continue
        i += 1
    return matches, quote


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


def _substitution(part: str, i: int) -> tuple[str, int] | None:
    """The body of the ``$( )`` or backtick substitution at ``i`` and the index of its last character; ``None``
    where ``$`` or a backtick starts none."""
    if part[i] == "$":
        if part[i + 1:i + 2] != "(" or part[i + 2:i + 3] == "(":  # ``$((`` is arithmetic: it runs no command
            return None
        end = substitution_end(part, i + 2)
        return part[i + 2:end - 1], end - 1
    end = part.find("`", i + 1)
    end = len(part) if end < 0 else end
    return part[i + 1:end], end


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


def _close_of(part: str, j: int) -> int:
    """The index of the ``)`` that closes a ``$(`` whose code starts at ``j``: parentheses count, and a quote inside the
    code is read as the shell reads it, so a ``)`` in quotes does not close it; a nested ``$( )`` is skipped whole."""
    quote, depth = "", 0
    while j < len(part):
        c = part[j]
        if quote == "'":
            quote = "" if c == "'" else quote
        elif c == "\\":
            j += 1
        elif c == "'" and not quote:
            quote = "'"
        elif c == '"':
            quote = "" if quote == '"' else '"'
        elif quote != "'" and part.startswith("$(", j):
            j = _close_of(part, j + 2)
        elif not quote and c == "(":
            depth += 1
        elif not quote and c == ")":
            if depth == 0:
                return j
            depth -= 1
        j += 1
    return len(part)


def _substitutions(part: str) -> tuple[list[str], str]:
    """The code of each ``$( )`` (balanced parentheses) and backtick substitution of a masked part, and the part without
    them: a substitution runs as its own command, so it is no word of this one."""
    codes: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(part):
        if part.startswith("$((", i):
            i = _close_of(part, i + 2) + 1
        elif part.startswith("$(", i):
            end = _close_of(part, i + 2)
            codes.append(part[i + 2:end])
            rest.append("_substitution_")
            i = end + 1
        elif part[i] == "`":
            j = part.find("`", i + 1)
            j = len(part) if j < 0 else j
            codes.append(part[i + 1:j])
            rest.append("_substitution_")
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


def command_stages(part: str) -> list[list[str]]:
    """Each command's words, excluding substitutions and redirection operands."""
    _, rest = _substitutions(_mask(part))
    return _stages(rest, include_reads=False)


def _stages(part: str, *, include_reads: bool = True) -> list[list[str]]:
    """A part's simple commands, cut at its unquoted pipes, each as its words (quotes removed, grouping dropped). An
    output redirection's target is written, so it is no word; an input's target is read, so it follows the words."""
    stages: list[list[str]] = [[]]
    reads: list[str] = []
    target = ""  # the operator whose target is the next token: ``>`` (written) or ``<`` (read)
    for token in _lexed(_fd_masked(part)):
        if target:
            if target == "<" and include_reads:
                reads.append(_restore(token))
            target = ""
        elif token == "|":
            _close(stages[-1], reads)
            reads = []
            stages.append([])
        elif token.lstrip("&").startswith(">") or token in ("<", "<&", "<<<"):
            target = token.lstrip("&")[:3]  # ``<<<`` is a string, not a file: only a lone ``<`` reads its target
        elif token not in _SEPARATORS and token not in _LEX_SKIP:
            stages[-1].append(_restore(token))
    _close(stages[-1], reads)
    return [stage for stage in stages if stage]


def command_words(words: list[str]) -> Iterator[list[str]]:
    """Normalized program and arguments, following wrappers."""
    for program, arguments in _views(words):
        yield [program, *arguments]


def _views(words: list[str]) -> Iterator[Stage]:
    """The command a simple command runs: its own program, then the command each wrapper (``time``, ``sudo``...) runs."""
    i = 0
    while i < len(words):
        while i < len(words):
            if _ASSIGN.match(words[i]) or words[i] in _KEYWORDS:  # NAME=value, ``then``: the command is next
                i += 1
            elif words[i].startswith("$") and words[i + 1:i + 2] == ["="]:  # PowerShell ``$x = ``: the same
                i += 2
            elif _PS_ASSIGN.match(words[i]):  # ``$x=cmd`` or ``$x= cmd``: the same, the command may be in the word
                rest = words[i].partition("=")[2]
                words = [*words[:i], *([rest] if rest else []), *words[i + 1:]]
            else:
                break
        if i >= len(words):
            return
        launched = _launched(words, i)
        if launched is not None:  # ``env FOO=1 nproc``: the program is nproc, and env is no probe of its own
            i = launched
            continue
        yield _head(words[i], words[i + 1:])
        wrapper = _program(words[i])
        if wrapper not in _WRAPPERS:
            return
        after = _past_wrapper_args(words, i + 1, wrapper)
        if after is None:
            return  # ``command -v nproc`` looks the program up, it does not run it
        i = after


def _past_wrapper_args(words: list[str], i: int, wrapper: str) -> int | None:
    """The index of the program a wrapper runs: past its options (``sudo -E``, ``nice -n 5``) and, for ``timeout``, its
    duration; None when the wrapper only looks a program up (``command -v``)."""
    while i < len(words) and words[i].startswith("-"):
        if words[i] in _INFO_OPTIONS or (wrapper, words[i]) in (("command", "-v"), ("doas", "-C")):
            return None
        i += 2 if (wrapper, words[i]) in _OPTION_OPERANDS else 1
    if wrapper == "timeout" and i < len(words):  # ``timeout 5 nproc``: the duration is no program
        i += 1
    return i


def _launched(words: list[str], i: int) -> int | None:
    """The index of the program that ``env`` or ``conda run`` / ``uv run`` at ``i`` runs, or None when no program
    follows its options and assignments."""
    program, j = _program(words[i]), i + 1
    if program == "env":
        while j < len(words) and (words[j].startswith("-") or _ASSIGN.match(words[j])):
            j += 2 if words[j] in _ENV_VALUES else 1
    elif program in ("conda", "uv") and words[i + 1:i + 2] == ["run"]:
        j = i + 2
        while j < len(words) and words[j].startswith("-"):
            j += 2 if words[j] in _RUN_VALUES else 1
    else:
        return None
    return j if j < len(words) else None


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
    if prog not in _SHELLS | _PWSH:
        return None
    i = 0
    while i < len(args):
        word = args[i]
        if (prog in _PWSH and len(word) > 1 and "-command".startswith(word.casefold())) or (prog in _SHELLS and _SHELL_C.fullmatch(word)):
            if prog in _PWSH:  # ``pwsh -Command a b``: the words after -Command are the script, joined
                return " ".join(args[i + 1:]) or None
            return args[i + 1] if i + 1 < len(args) else None
        if prog in _SHELLS and word in ("-o", "+o"):  # ``bash -o pipefail -c nproc``: the option's value is no word
            i += 2
            continue
        if prog in _PWSH and word.casefold() in _PWSH_VALUES:  # ``pwsh -ExecutionPolicy Bypass -Command ...``
            i += 2
            continue
        if not word.startswith("-"):
            return None
        i += 1
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
        if re.fullmatch(r"-[BdEhiIOPqRsSuvx]+[cm]", word):  # ``-Ic code``, ``-Im pip``: flags, then the code or module
            return "-" + word[-1], args[i + 1:]
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


def _value_span(word: str, values: frozenset[str]) -> int:
    """How many words a searcher's option takes with its value: 1 for ``--max-count=1``, for ``-m1`` and for ``-g*.md``
    (the letters after the first value option are its value), 2 for ``-m 1`` and for a flag cluster whose first value
    option is its last letter (``-nA 2``), 0 for a word that is no such option."""
    name, eq, _ = word.partition("=")
    if name in values:
        return 1 if eq else 2
    if not re.fullmatch(r"-[^-=]{2,}", word):
        return 0
    for at, c in enumerate(word[1:], 1):
        if f"-{c}" in values:
            return 2 if at == len(word) - 1 else 1
    return 0


def _attached_pattern(word: str, values: frozenset[str]) -> tuple[str, str, str] | None:
    """A short cluster that holds ``-e`` or ``-f``: (the cluster before that letter, the letter, the rest of the word,
    which is its value, or empty when the value is the next word). ``-ewarn`` is ``-e`` with ``warn``, ``-nf`` is ``-n``
    and ``-f``. None when a letter that takes a value comes first (``-gfoo`` is ``-g`` with ``foo``) or there is none."""
    if len(word) < 3 or word[0] != "-" or word[1] == "-":
        return None
    for at, c in enumerate(word[1:], 1):
        if f"-{c}" in values:
            return None
        if c in "ef":
            return word[:at], c, word[at + 1:]
    return None


def _operands(program: str, args: tuple[str, ...]) -> tuple[str, ...]:
    """The words of a reader that can name a file. A searcher's search expression is not one: it is its ``-e`` value, or
    else its first operand, and a pattern file (``-f``) is a file it reads. A searcher's option whose next word is a
    value (``rg -g '*.c'``, ``grep -m 1``) takes that word, which is neither."""
    if program not in _SEARCHERS:
        return args
    operands, pattern_files, _, _ = search_words(program, args)
    return pattern_files + operands


def _pattern_option(args: Sequence[str], i: int, values: frozenset[str]) -> tuple[int, str, list[str]] | None:
    """The option at ``args[i]`` that gives the search expression or a pattern file, else None: (the words it takes, the
    short flags before it in its cluster, the pattern file it names). ``-e warn``, ``--regexp=warn``, ``-ewarn``, ``-nf F``."""
    name, eq, value = args[i].partition("=")
    if name in _EXPRESSION or name in _PATTERN_FILE:
        before, is_file, inline = "", name in _PATTERN_FILE, value if eq else None
    elif attached := _attached_pattern(args[i], values):
        before, letter, rest = attached
        is_file, inline = letter == "f", rest or None
    else:
        return None
    named = inline if inline is not None else (args[i + 1] if i + 1 < len(args) else None)
    return (1 if inline is not None else 2), before, [named] if is_file and named is not None else []


def _match_limit(word: str, args: Sequence[str], i: int, values: frozenset[str]) -> str | None:
    """The value ``args[i]`` gives to ``-m`` / ``--max-count`` (``-m0``, ``-nm 0``, ``--max-count=0``), else None."""
    name, eq, value = word.partition("=")
    if name in ("-m", "--max-count"):
        return value if eq else (args[i + 1] if i + 1 < len(args) else None)
    if name.startswith("-") and not name.startswith("--"):
        # the caller found a value letter in this word: the cluster ends there, and the rest of it is that letter's value
        at = next(k for k, c in enumerate(name) if k and f"-{c}" in values)
        return (name[at + 1:] or (args[i + 1] if i + 1 < len(args) else None)) if name[at] == "m" else None
    return None


def _gnu_long(program: str, word: str) -> str:
    """``word`` with the long option it names written in full, for GNU grep (``--max=1`` is ``--max-count=1``) and git grep
    (``--max-d`` is ``--max-depth``), each against its own options: both read an unambiguous prefix of an option as the
    option. rg is not held to this, and nor is a word that is no long option or whose prefix is ambiguous (``--exclu`` is
    ``--exclude``, ``--exclude-from`` or ``--exclude-dir``)."""
    name, eq, value = word.partition("=")
    long = _GIT_GREP_LONG if program == "git grep" else _GNU_GREP_LONG
    if program == "rg" or not name.startswith("--") or len(name) == 2 or name[2:] in long:
        return word
    hits = [n for n in long if n.startswith(name[2:])]
    return f"--{hits[0]}{eq}{value}" if len(hits) == 1 else word


def search_words(program: str, args: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...], frozenset[str], str | None]:
    """A searcher's command line as (the files and directories it searches, the pattern files it reads (``-f``), the
    options it was given: ``--quiet`` as written, a short one as ``-q`` also from a cluster, ``-nq``, the value of its
    last ``-m`` / ``--max-count`` or None). Its search expression, an ``-e`` value or else the first operand, is none
    of them, and nor is the value of an option in the table (``--exclude AGENTS.md``)."""
    values = _SEARCH_VALUES.get(program, frozenset())
    operands: list[str] = []
    pattern_files: list[str] = []
    flags: set[str] = set()
    limit: str | None = None
    given = False  # an expression or a pattern file is named, so the first operand is no expression
    i = 0
    args = list(args)
    while i < len(args):
        word = args[i] = _gnu_long(program, args[i])
        if word == "--":
            operands.extend(args[i + 1:])
            break
        if pattern := _pattern_option(args, i, values):
            used, before, pattern_file = pattern
            given = True
            flags.update(_option_names(before, values))
            pattern_files.extend(pattern_file)
            i += used
        elif span := _value_span(word, values):
            flags.update(_option_names(word, values))
            limit = _match_limit(word, args, i, values) or limit
            i += span
        elif word.startswith("-") and len(word) > 1:
            flags.update(_option_names(word, values))
            i += 1
        else:
            operands.append(word)
            i += 1
    return (tuple(operands) if given else tuple(operands[1:])), tuple(pattern_files), frozenset(flags), limit


def _option_names(word: str, values: frozenset[str] = frozenset()) -> set[str]:
    """The options a word gives: ``--quiet`` (``--max-count=1`` as ``--max-count``), or a short cluster's letters, ``-nq`` as
    ``-n`` and ``-q``. A cluster ends at its first letter that takes a value: the rest of the word is that value (``rg -tc``
    is ``-t`` with the type ``c``, ``grep -cm1`` is ``-c`` and ``-m`` with 1)."""
    name = word.partition("=")[0]
    if name.startswith("--"):
        return {name}
    names: set[str] = set()
    for c in word[1:]:
        names.add(f"-{c}")
        if f"-{c}" in values:
            break
    return names


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

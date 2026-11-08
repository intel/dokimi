# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Checks of the scripts a skill bundles. Nothing here runs or imports a script.

A bundled ``.sh``/``.bash``/``.py`` file, or any file that starts with ``#!``, is read as bytes:
CRLF line endings break it (``script-crlf``), and it is parsed for syntax (``script-syntax``):
``ast.parse`` for Python, and ``bash -n`` for shell with the text on stdin, only when bash is on
PATH. ruff and shellcheck lint the bundle when they are on PATH; the counts go in one ``script-lint``
note. Each file is read only inside the skill directory, and only up to :data:`MAX_BYTES`.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import warnings
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from dokimi import process
from dokimi.skills.loader import TEMPLATE_DIR_NAMES, VENDORED_DIR_NAMES

ScriptFinding = tuple[str, str, str]  # (level, code, message), as ``validate.Finding`` takes them
Which = Callable[[str], Optional[str]]

MAX_BYTES = 1_000_000  # a larger file is not read
SYNTAX_TIMEOUT_SEC = 10.0
LINT_TIMEOUT_SEC = 60.0
RUFF_SELECT = "S,E9,F63,F7,F82"
SHELL_SUFFIXES = (".sh", ".bash")
SCRIPT_SUFFIXES = (*SHELL_SUFFIXES, ".py")
BASH_FAMILY = frozenset({"bash", "sh", "dash", "ksh"})  # the shebang names bash -n can judge
ENV_OPERAND_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir"})  # env options with an operand
PARSE_SHOPTS = frozenset({"extglob"})  # bash -O options that change how a script parses; bash -n is given them
PARSE_NEUTRAL_FLAGS = frozenset("BEPTeuxh")  # shebang flags, as letters, that change no parse
PARSE_NEUTRAL_OPTIONS = frozenset({"errexit", "nounset", "pipefail", "xtrace"})  # shebang -o names likewise
SYNTAX_CODES = frozenset({"E999", "invalid-syntax"})  # a syntax error is script-syntax's, not the lint's
SKIP_DIRS = VENDORED_DIR_NAMES | TEMPLATE_DIR_NAMES  # the loader's vendored and scaffold trees
TOP_RULES = 3


def script_findings(
    skill_dir: Path,
    *,
    which: Optional[Which] = None,
    runner: Optional[process.Runner] = None,
    checkout: Optional[Path] = None,
) -> list[ScriptFinding]:
    """The findings on the scripts under ``skill_dir``; ``which`` and ``runner`` default to PATH and :func:`process.run`.

    A skill at the root of ``checkout`` bundles the whole checkout, tests and fixtures included, so it has no script bundle.
    """
    which = which or shutil.which
    runner = runner or process.run
    base = skill_dir.resolve()
    if checkout is not None and base == checkout.resolve():
        return []
    bash = which("bash")
    findings: list[ScriptFinding] = []
    python_files: list[Path] = []
    shell_files: list[Path] = []
    for path in _bundled_files(skill_dir, base):
        data = _script_bytes(path)
        if data is None:
            continue
        if path.suffix.lower() == ".py":
            python_files.append(path)
        elif path.suffix.lower() in SHELL_SUFFIXES:
            shell_files.append(path)
        findings += _file_findings(path, path.relative_to(skill_dir).as_posix(), data, bash, runner)
    tools = _lint(python_files, shell_files, which, runner)
    if any(tools.values()):
        findings.append(("note", "script-lint", _lint_message(tools)))
    return findings


def _file_findings(path: Path, rel: str, data: bytes, bash: Optional[str], runner: process.Runner) -> list[ScriptFinding]:
    """The CRLF and syntax findings on one script: a .py is parsed, a shell script is judged by ``bash -n``."""
    suffix = path.suffix.lower()
    crlf = b"\r\n" in data
    found: list[ScriptFinding] = []
    if crlf and (suffix in SHELL_SUFFIXES or data.startswith(b"#!")):
        found.append(("error", "script-crlf", f"{rel} has CRLF line endings: bash and a shebang "
                      "interpreter fail on the carriage return"))
    if suffix == ".py":
        problem = _python_problem(data, path)
        if problem:
            found.append(("error", "script-syntax", f"{rel} does not parse as Python: {problem}"))
    elif suffix in SHELL_SUFFIXES and bash is not None and not crlf and _runs_under_bash(data):
        # a CRLF script fails on every line, so its crlf finding is the one to fix; fish, zsh etc. are not bash's
        options = _shell_options(data)  # None: the shebang asks for an option bash -n cannot be given
        problem = None if options is None else _shell_problem(bash, options, data, runner)
        if problem:
            found.append(("error", "script-syntax", f"{rel} does not parse as shell: {problem}"))
    return found


def _bundled_files(skill_dir: Path, base: Path) -> list[Path]:
    """Every regular file under ``skill_dir`` that resolves inside ``base``, in path order; no link is followed out."""
    found: list[Path] = []
    for root, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(root) / name
            try:
                real = path.resolve()
                if real.is_relative_to(base) and real.is_file():
                    found.append(path)
            except (OSError, RuntimeError, ValueError):
                continue  # a name the OS refuses, or a link loop: not a script we can read
    return found


def _script_bytes(path: Path) -> Optional[bytes]:
    """The bytes of ``path`` if it is a script (by suffix, or a ``#!`` first line) of at most MAX_BYTES, else None."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        with path.open("rb") as handle:
            head = handle.read(2)
            if path.suffix.lower() not in SCRIPT_SUFFIXES and head != b"#!":
                return None
            handle.seek(0)
            return handle.read()
    except OSError:
        return None


def _python_problem(data: bytes, path: Path) -> Optional[str]:
    """The syntax error ``ast.parse`` gives for ``data``, or None. The file is parsed, never compiled or run."""
    try:
        with warnings.catch_warnings():  # an invalid escape is a SyntaxWarning, not a reason to print
            warnings.simplefilter("ignore")
            ast.parse(data, filename=str(path))
    except SyntaxError as exc:
        return f"{exc.msg} (line {exc.lineno})" if exc.lineno else str(exc.msg)
    except (ValueError, RecursionError) as exc:  # a NUL byte; nesting past the parser's limit
        return " ".join(str(exc).split())[:200]
    return None


def _runs_under_bash(data: bytes) -> bool:
    """True when the script has no shebang, or one naming a POSIX-family shell (``env`` is looked through)."""
    words = _shebang_words(data)
    if words is None:
        return True
    return bool(words) and os.path.basename(words[0]) in BASH_FAMILY


def _shebang_words(data: bytes) -> Optional[list[str]]:
    """The words of the ``#!`` line, from the interpreter on; None when there is no shebang."""
    if not data.startswith(b"#!"):
        return None
    words = data.split(b"\n", 1)[0][2:].decode("utf-8", "replace").split()
    if words and os.path.basename(words[0]) == "env":
        words = _env_command(words[1:])
    return words


def _env_command(args: list[str]) -> list[str]:
    """What ``env`` runs: its options (``-u`` and ``-C`` take an operand; ``-S`` splits what follows) and the
    ``NAME=value`` words before the command are skipped."""
    i = 0
    while i < len(args):
        if args[i] in ENV_OPERAND_OPTIONS:
            i += 2
        elif args[i].startswith("-") or "=" in args[i]:
            i += 1
        else:
            break
    return args[i:]


def _spawn(runner: process.Runner, argv: list[str], **kwargs) -> Optional[process.Completed]:
    """What ``runner`` gives for ``argv``, or None when the program cannot start (a missing interpreter, say)."""
    try:
        return runner(argv, **kwargs)
    except OSError:
        return None


def _shell_options(data: bytes) -> Optional[list[str]]:
    """The bash options ``bash -n`` is given for the shebang's arguments: ``-O`` for a shopt that changes parsing
    (``extglob``), and nothing for a flag or ``-o`` option that changes no parse. None when the shebang has an
    argument this check cannot reproduce, so the script is not judged."""
    words = _shebang_words(data)
    args = words[1:] if words else []
    options: list[str] = []
    i = 0
    while i < len(args):
        word, operand = args[i], args[i + 1] if i + 1 < len(args) else ""
        if word == "-O" and operand in PARSE_SHOPTS:
            options += ["-O", operand]
            i += 2
        elif word == "-o" and operand in PARSE_NEUTRAL_OPTIONS:
            i += 2
        elif word.startswith("-") and len(word) > 1 and set(word[1:]) <= PARSE_NEUTRAL_FLAGS:
            i += 1
        else:
            return None
    if b"extglob" in data and "extglob" not in options:
        # A script that turns extglob on with ``shopt -s extglob`` has its patterns parsed only after that
        # runs, and bash -n runs nothing: so the option is given when the text names it.
        options += ["-O", "extglob"]
    return options


def _shell_problem(bash: str, options: list[str], data: bytes, runner: process.Runner) -> Optional[str]:
    """The first line ``bash -n`` reports for the script on stdin, or None. Nothing is run; no environment is passed."""
    done = _spawn(runner, [bash, *options, "-n"], timeout=SYNTAX_TIMEOUT_SEC, env=process.Env.only(), input=data)
    if done is None or done.timed_out or done.returncode == 0:
        return None
    first = next((line.strip() for line in done.stderr.splitlines() if line.strip()), "")
    return first[:200] or f"bash -n exited {done.returncode}"


def _lint(python_files: list[Path], shell_files: list[Path], which: Which,
          runner: process.Runner) -> dict[str, Counter]:
    """Rule counts per tool that is on PATH and ran to a verdict; a tool that is missing or fails is left out."""
    tools: dict[str, Counter] = {}
    ruff = which("ruff") if python_files else None
    if ruff is not None:
        counts = _ruff_counts(ruff, python_files, runner)
        if counts is not None:
            tools["ruff"] = counts
    shellcheck = which("shellcheck") if shell_files else None
    if shellcheck is not None:
        counts = _shellcheck_counts(shellcheck, shell_files, runner)
        if counts is not None:
            tools["shellcheck"] = counts
    return tools


def _ruff_counts(ruff: str, files: list[Path], runner: process.Runner) -> Optional[Counter]:
    # --isolated: no config from the skill's own directory; --no-cache: nothing written into it
    done = _spawn(runner, [ruff, "check", "--select", RUFF_SELECT, "--output-format", "json", "--isolated",
                           "--no-cache", *map(str, files)], timeout=LINT_TIMEOUT_SEC, env=process.Env.only())
    items = _json_list(done)
    if items is None:
        return None
    codes = [str(item.get("code") or "syntax") for item in items if isinstance(item, dict)]
    return Counter(code for code in codes if code not in SYNTAX_CODES)


def _shellcheck_counts(shellcheck: str, files: list[Path], runner: process.Runner) -> Optional[Counter]:
    done = _spawn(runner, [shellcheck, "-f", "json", "--norc", *map(str, files)],
                  timeout=LINT_TIMEOUT_SEC, env=process.Env.only())
    data = _json_list(done, wrapped_key="comments")
    if data is None:
        return None
    return Counter(f"SC{item['code']}" for item in data
                   if isinstance(item, dict) and item.get("code") is not None)


def _json_list(done: Optional[process.Completed], *, wrapped_key: str = "") -> Optional[list]:
    """The JSON list a linter printed (or the list under ``wrapped_key``); None if it did not run to a verdict.

    Both linters exit 1 when they found something and 0 when they did not; 2 or more is a failure.
    """
    if done is None or done.timed_out or done.returncode not in (0, 1):
        return None
    try:
        data = json.loads(done.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if wrapped_key and isinstance(data, dict):  # a linter version that wraps its list in an object
        data = data.get(wrapped_key)
    return data if isinstance(data, list) else None


def _lint_message(tools: dict[str, Counter]) -> str:
    parts = []
    for name, counts in tools.items():
        if counts:
            top = ", ".join(f"{rule} x{n}" for rule, n in counts.most_common(TOP_RULES))
            parts.append(f"{name} {sum(counts.values())} ({top})")
    return "bundled-script lint: " + "; ".join(parts)

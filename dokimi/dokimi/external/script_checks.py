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
import bisect
import json
import os
import re
import shutil
import warnings
from collections import Counter
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from dokimi import process
from dokimi.skills.loader import TEMPLATE_DIR_NAMES, VENDORED_DIR_NAMES

ScriptFinding = tuple[str, str, str]  # (level, code, message), as ``validate.Finding`` takes them
Which = Callable[[str], Optional[str]]

MAX_BYTES = 1_000_000  # a larger file is not read
SHEBANG_BYTES = 255  # a #! line is read to this many bytes, as Linux 5.1 and later reads it (one byte is kept for a nul)
SYNTAX_TIMEOUT_SEC = 10.0
LINT_TIMEOUT_SEC = 60.0
RUFF_SELECT = "S,E9,F63,F7,F82"
SHELL_SUFFIXES = (".sh", ".bash")
SCRIPT_SUFFIXES = (*SHELL_SUFFIXES, ".py")
BASH_FAMILY = frozenset({"bash", "sh", "dash", "ksh"})  # the shebang names bash -n can judge
ENV_OPERAND_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir"})  # env options with an operand
PARSE_SHOPTS = frozenset({"extglob"})  # bash -O options that change how a script parses; bash -n is given them
# The shopt check reads each command once (linear): a command ends at ; | ) & or a newline (not at &> or >&), and a bare
# ( { ! word starts the next one. Its words are read in order: assignments and redirections, then keywords, then shopt.
COMMAND_SPLIT = re.compile(rb"[;|\n)]|(?<![<>])&(?!>)")
WORD_SPLIT = re.compile(rb"[ \t]+")
WORD_BOUNDARY_CHARS = b"({!)"
ASSIGN_WORD = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*\+?=")
REDIR_WORD = re.compile(rb"&?[0-9]*[<>]+&?")
ARGUMENT_WORDS = frozenset({b"command", b"builtin", b"exec"})  # an assignment after one is an operand, not a prefix
RESERVED_WORDS = frozenset({b"then", b"do", b"else", b"elif", b"if", b"while", b"until", b"time", b"coproc"})
EXTGLOB_WORD = re.compile(rb"extglob(?![A-Za-z0-9_])")
WORD_START = (b" ", b"\t", b"\r", b"\n", b";", b"&", b"|", b"(")  # bytes after which a word starts: a # there is a comment, a (( a command
QUOTED_TEXT = frozenset(bytes([b]) for b in b" \t\n;&|(){}<>!")  # bytes a quote makes text: no separator, operator or blank
# Escapes env -S reads outside quotes (\_ separates words and \c ends the string: see _env_split).
ENV_S_ESCAPES = {"_": " ", "#": "#", "\\": "\\", "'": "'", '"': '"', "$": "$", "n": "\n", "t": "\t", "r": "\r",
                 "f": "\f", "v": "\v"}
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
        found.append(("warn", "script-crlf", f"{rel} has CRLF line endings: bash and a shebang "
                      "interpreter fail on the carriage return"))
    if suffix == ".py":
        problem = _python_problem(data, path)
        if problem:
            found.append(("warn", "script-syntax", f"{rel} does not parse as Python: {problem}"))
    elif suffix in SHELL_SUFFIXES and bash is not None and not crlf and _runs_under_bash(data):
        # a CRLF script fails on every line, so its crlf finding is the one to fix; fish, zsh etc. are not bash's
        options = _shell_options(data)  # None: the shebang asks for an option bash -n cannot be given
        problem = None if options is None else _shell_problem(bash, options, data, runner)
        if problem:
            found.append(("warn", "script-syntax", f"{rel} does not parse as shell: {problem}"))
    return found


def _holds_skill(folder: Path) -> bool:
    """Whether ``folder`` holds a SKILL.md of its own: another skill, whose scripts are that skill's to check."""
    try:
        return (folder / "SKILL.md").is_file()
    except OSError:
        return False


def _bundled_files(skill_dir: Path, base: Path) -> list[Path]:
    """Every regular file under ``skill_dir`` that resolves inside ``base``, in path order; no link is followed out."""
    found: list[Path] = []
    for root, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not _holds_skill(Path(root) / d))
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
    words = data.split(b"\n", 1)[0][:SHEBANG_BYTES][2:].decode("utf-8", "replace").split()
    if words and os.path.basename(words[0]) == "env":
        words = _env_command(words[1:])
    return words


def _env_command(args: list[str]) -> list[str]:
    """What ``env`` runs: its options (``-u`` and ``-C`` take an operand) and the ``NAME=value`` words before the
    command are skipped. ``-S`` takes the rest of the line as one string that env splits as GNU env splits it (see
    :func:`_env_split`), so its quotes group words; a string that does not split is not a command ``env`` runs, so it
    gives nothing. A split that holds another ``-S`` is split again, in a loop: a long run of them is no recursion, and
    the loop ends because each split is shorter than the text it came from."""
    while True:
        i = 0
        split: Optional[list[str]] = None
        while i < len(args):
            if args[i] in ENV_OPERAND_OPTIONS:
                i += 2
            elif args[i].startswith(("-S", "--split-string")):
                head = args[i][2:] if args[i].startswith("-S") else args[i][len("--split-string"):].lstrip("=")
                text = " ".join(([head] if head else []) + args[i + 1:])
                split = _env_split(text) or []
                break
            elif args[i].startswith("-") or "=" in args[i]:
                i += 1
            else:
                break
        if split is None:
            return args[i:]
        args = split


def _env_split(text: str) -> Optional[list[str]]:
    """The words of an ``env -S`` string, split as GNU env splits it: blanks separate words, quotes group them, an
    unquoted ``\\_`` is a separator, ``\\c`` ends the string, and a ``#`` that starts a word begins a comment. None
    for what env refuses: an unknown escape, a ``$`` expansion, an unclosed quote."""
    words: list[str] = []
    cur: list[str] = []
    have = False  # the word has started (a quote or an escape counts, even with no text)
    i = 0
    while i < len(text):
        kind, piece, width = _env_unquoted(text, i, have)
        if kind == "stop":
            break
        if kind == "bad":
            return None
        if kind == "sep":
            if have:
                words.append("".join(cur))
                cur, have = [], False
        else:
            cur.append(piece)
            have = True
        i += width
    if have:
        words.append("".join(cur))
    return words


def _env_unquoted(text: str, i: int, have: bool) -> tuple[str, str, int]:
    """What env -S reads at ``text[i]`` outside quotes, as (kind, text, width). kind is ``sep`` (a blank or ``\\_``),
    ``text``, ``stop`` (``\\c``, or a ``#`` that starts a word) or ``bad`` (a ``$``, an unknown escape, an unclosed
    quote)."""
    c = text[i]
    if c == "\\":
        return _env_escape(text[i + 1:i + 2])
    if c in " \t\n\r\f\v":
        return "sep", "", 1
    if c == "#" and not have:
        return "stop", "", 0
    if c in "'\"":
        got = _env_quoted(text, i)
        return ("bad", "", 0) if got is None else ("text", got[0], got[1] - i)
    if c == "$":
        return "bad", "", 0
    return "text", c, 1


def _env_escape(nxt: str) -> tuple[str, str, int]:
    """The unquoted escape whose letter is ``nxt``: ``\\c`` stops, ``\\_`` separates, the rest are ENV_S_ESCAPES."""
    if nxt == "c":
        return "stop", "", 0
    if nxt == "_":
        return "sep", "", 2
    if nxt in ENV_S_ESCAPES:
        return "text", ENV_S_ESCAPES[nxt], 2
    return "bad", "", 0


def _env_quoted(text: str, i: int) -> Optional[tuple[str, int]]:
    """The text of the quoted run that opens at ``text[i]``, and the index just past its closing quote. None when it
    is unclosed or a double-quoted run holds a ``$``; in double quotes only ``\\\\``, ``\\"`` and ``\\$`` are escapes.
    GNU env also reads ``\\\\`` and ``\\'`` in single quotes, which are not read here: no word they change is ``bash``."""
    quote = text[i]
    out: list[str] = []
    j = i + 1
    while j < len(text):
        c = text[j]
        if c == quote:
            return "".join(out), j + 1
        if quote == '"' and c == "$":
            return None
        if quote == '"' and c == "\\" and text[j + 1:j + 2] in ("\\", '"', "$"):
            out.append(text[j + 1])
            j += 2
            continue
        out.append(c)
        j += 1
    return None


def _spawn(runner: process.Runner, argv: list[str], **kwargs) -> Optional[process.Completed]:
    """What ``runner`` gives for ``argv``, or None when the program cannot start (a missing interpreter, say)."""
    try:
        return runner(argv, **kwargs)
    except OSError:
        return None


def _escaped(data: bytes, j: int) -> bool:
    """True when ``data[j]`` is escaped: an odd number of backslashes come right before it."""
    k = j
    while k > 0 and data[k - 1:k] == b"\\":
        k -= 1
    return (j - k) % 2 == 1


def _in_quotes(c: bytes) -> bytes:
    """A byte inside quotes. A blank, newline or operator there is a word's text, not a separator, so it reads as
    ``\\x01``: ``echo 'shopt -s extglob'`` has no shopt command in it, and ``echo ';'shopt`` has none either."""
    return b"\x01" if c in QUOTED_TEXT else c


HEREDOC_OPEN = re.compile(rb"<<(-?)[ \t]*((?:[^\s;&|<>()'\"\\]|\\.|'[^'\n]*'|\"[^\"\n]*\")+)")  # not <<<, which has no word
HEREDOC_PART = re.compile(rb"\\(.)|'([^'\n]*)'|\"([^\"\n]*)\"")  # an escape or a quoted part of a heredoc delimiter


def _unquoted_text(data: bytes) -> bytes:
    """The script with its comments removed, its quote marks dropped and its line continuations joined, for the shopt
    check. A ``#`` that starts a word, outside quotes, begins a comment (``$#`` and ``${x##*/}`` do not); a quoted
    word is its text, so ``shopt -s 'extglob'`` names extglob and ``echo "a # b"`` has no comment (see
    :func:`_in_quotes`). A heredoc body is text for the shell, not script, so it is left out; a quote mark with no
    closing mark is a character. Arithmetic is copied as it is (:func:`_arith_end`)."""
    out = bytearray()
    prev = b"\n"  # the byte before, outside quotes: a # after a blank or a separator starts a word
    resume = -1  # where the bodies of the heredocs opened on this line end: the line's newline skips to it
    lines = _script_lines(data)
    i = 0
    while i < len(data):
        c = data[i:i + 1]
        if c == b"\n" and resume >= 0:
            out += c
            i, resume, prev = resume, -1, b"\n"
        elif (end := _arith_end(data, i, prev)) is not None:
            out += data[i:end]
            prev, i = b"x", end
        elif c == b"<" and prev != b"<" and (opened := _heredoc_at(data, i, resume, lines)) is not None:
            end, resume = opened
            out += data[i:end]
            prev, i = b"x", end
        else:
            text, i, prev = _text_step(data, i, prev)
            out += text
    return bytes(out)


def _text_step(data: bytes, i: int, prev: bytes) -> tuple[bytes, int, bytes]:
    """What the unquoted text at ``data[i]`` gives the shopt check, as (text, next index, the byte before that index):
    a quoted run, a comment, an escape or a plain byte."""
    c = data[i:i + 1]
    if c in (b"'", b'"'):
        got = _quoted_run(data, i)
        return (c, i + 1, c) if got is None else (got[0], got[1], b"x")
    if c == b"#" and prev in WORD_START:
        return b"", _comment_end(data, i), b"\n"
    if c == b"\\":  # a line continuation is joined, as bash joins it: nothing is kept
        if data[i + 1:i + 2] == b"\n":
            return b"", i + 2, prev
        return _in_quotes(data[i + 1:i + 2]), i + 2, b"x"  # \; is the word ;shopt, and \shopt is shopt
    return c, i + 1, c


def _comment_end(data: bytes, i: int) -> int:
    """Where the comment that starts at ``data[i]`` ends: at its newline, or at the end of the data."""
    end = data.find(b"\n", i)
    return len(data) if end < 0 else end


def _arith_end(data: bytes, i: int, prev: bytes) -> Optional[int]:
    """The index just past the arithmetic expansion or ``(( ))`` command that opens at ``data[i]`` (see
    :func:`_arith_open`), or None when none opens there. Its parens are counted to the end, and an unclosed one runs
    to the end of the data, as bash would reject it. A ``<<`` in it is a shift, never a heredoc."""
    width = _arith_open(data, i, prev)
    if not width:
        return None
    if data[i + 1:i + 2] == b"[":  # $[ ... ], the old form: it closes at the first ]
        end = data.find(b"]", i)
        return len(data) if end < 0 else end + 1
    depth, j = 2, i + width
    while depth and j < len(data):
        if data[j:j + 1] == b"(":
            depth += 1
        elif data[j:j + 1] == b")":
            depth -= 1
        j += 1
    return j


def _arith_open(data: bytes, i: int, prev: bytes) -> int:
    """The width of an arithmetic opener at ``data[i]``: ``$((``, or ``((`` where a command starts (``prev`` is the byte
    before). Zero when there is none."""
    if data.startswith(b"$((", i):
        return 3
    if data.startswith(b"$[", i):
        return 2
    return 2 if data.startswith(b"((", i) and prev in WORD_START else 0


class _Lines(NamedTuple):
    """Where the lines of a script are, so a heredoc's terminator is found by a bisect, not a walk to the end."""
    plain: dict[bytes, list[tuple[int, int]]]  # a line's text -> its (start, end) offsets, in order
    dashed: dict[bytes, list[tuple[int, int]]]  # the same with the leading tabs off, for <<-
    newlines: list[int]


def _script_lines(data: bytes) -> _Lines:
    plain: dict[bytes, list[tuple[int, int]]] = {}
    dashed: dict[bytes, list[tuple[int, int]]] = {}
    start = 0
    for line in data.split(b"\n"):
        span = (start, start + len(line))
        plain.setdefault(line, []).append(span)
        dashed.setdefault(line.lstrip(b"\t"), []).append(span)
        start += len(line) + 1
    return _Lines(plain, dashed, [m.start() for m in re.finditer(b"\n", data)])


def _heredoc_at(data: bytes, i: int, resume: int, lines: _Lines) -> Optional[tuple[int, int]]:
    """For a heredoc operator at ``data[i]``: the index just past the operator, and the index just past its body. The
    body starts on the line after the operator, or after the body of an earlier heredoc on that line (``resume``)."""
    match = HEREDOC_OPEN.match(data, i)
    if match is None:
        return None
    delim = HEREDOC_PART.sub(lambda part: part.group(1) or part.group(2) or part.group(3) or b"", match.group(2))
    if resume < 0:
        k = bisect.bisect_left(lines.newlines, match.end())
        resume = lines.newlines[k] + 1 if k < len(lines.newlines) else len(data)
    body_end = _heredoc_body_end(data, lines, resume, match.group(1) == b"-", delim)
    return None if body_end is None else (match.end(), body_end)


def _heredoc_body_end(data: bytes, lines: _Lines, start: int, dash: bool, delim: bytes) -> Optional[int]:
    """The index just past the line that ends a heredoc body: the first line from ``start`` that is ``delim`` (after its
    leading tabs with ``<<-``). None when no line is ``delim``: then the ``<<`` is a shift in arithmetic, not a heredoc,
    since bash needs the terminator for a heredoc."""
    spans = (lines.dashed if dash else lines.plain).get(delim, [])
    k = bisect.bisect_left(spans, (start,))
    return None if k == len(spans) else min(spans[k][1] + 1, len(data))


def _quoted_run(data: bytes, i: int) -> Optional[tuple[bytes, int]]:
    """The text of the quoted run that opens at ``data[i]``, and the index just past its closing mark; None when it is
    unclosed. Blanks in it are text (:func:`_in_quotes`). In double quotes, as in bash, a backslash before ``$``, a
    backtick, ``"`` or a backslash is that character, a backslash-newline is removed, and any other backslash is kept.
    In ``$'...'`` a backslash escapes the next byte, so ``$'it\\'s'`` closes at its last mark."""
    quote = data[i:i + 1]
    ansi = quote == b"'" and data[i - 1:i] == b"$" and not _escaped(data, i - 1) and data[i - 2:i - 1] != b"$"
    out = bytearray()
    j = i + 1
    while j < len(data):
        c = data[j:j + 1]
        if c == quote:
            return bytes(out), j + 1
        if c == b"\\" and ansi:
            out += _in_quotes(data[j + 1:j + 2])
            j += 2
        elif c == b"\\" and quote == b'"':
            text, width = _dq_backslash(data, j)
            out += text
            j += width
        else:
            out += _in_quotes(c)
            j += 1
    return None


def _dq_backslash(data: bytes, j: int) -> tuple[bytes, int]:
    """What a backslash at ``data[j]`` inside double quotes stands for, and its width: a backslash-newline is nothing,
    a backslash before ``$``, a backtick, ``"`` or a backslash is that character, any other backslash is itself."""
    nxt = data[j + 1:j + 2]
    if nxt == b"\n":
        return b"", 2
    if nxt in (b"$", b"`", b'"', b"\\"):
        return nxt, 2
    return b"\\", 1


def _command_groups(words: list[bytes]):
    """The words of each command in a segment: a bare ( { or ! ends one command and starts the next (function f { shopt ...; })."""
    group: list[bytes] = []
    for word in words:
        if word.lstrip(WORD_BOUNDARY_CHARS):
            group.append(word)
        else:
            yield group
            group = []
    yield group


def _command_word(words: list[bytes]) -> Optional[int]:
    """Index of the command word: past the assignments and redirections in front of it, and past the reserved words. An
    assignment after command, builtin or exec is an operand, so the command word is that word."""
    arguments = False
    i = 0
    while i < len(words):
        w = words[i].lstrip(WORD_BOUNDARY_CHARS)
        if w in ARGUMENT_WORDS:
            arguments = True
        elif w in RESERVED_WORDS or not w:
            pass
        elif not arguments and ASSIGN_WORD.match(w):
            pass
        elif not arguments and (redir := REDIR_WORD.match(w)):
            i += redir.end() == len(w)  # a redirection whose word ends at its operator takes the next word
        else:
            return i
        i += 1
    return None


def _shopt_enables_extglob(operands: list[bytes]) -> bool:
    """Whether the operands of a shopt turn extglob on. The options come first, up to -- or the first word that is not
    one: the option s must be one of them, and extglob is an operand after them."""
    s_option = False
    options = True
    for word in operands:
        if EXTGLOB_WORD.match(word):
            return s_option
        if options and word == b"--":
            options = False
        elif options and word.startswith(b"-"):
            s_option = s_option or (word[1:].isalpha() and b"s" in word)
        else:
            options = False
    return False


def _shopt_extglob(text: bytes) -> bool:
    """Whether a command of the script runs shopt to turn extglob on."""
    for command in COMMAND_SPLIT.split(text):
        for words in _command_groups([w for w in WORD_SPLIT.split(command) if w]):
            i = _command_word(words)
            if i is not None and words[i].lstrip(WORD_BOUNDARY_CHARS) == b"shopt" and _shopt_enables_extglob(words[i + 1:]):
                return True
    return False


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
        if word in ("-", "--"):
            break  # the end of the options: what follows is the script's own arguments
        if word == "-O" and operand in PARSE_SHOPTS:
            options += ["-O", operand]
            i += 2
        elif word == "-o" and operand in PARSE_NEUTRAL_OPTIONS:
            i += 2
        elif word.startswith("-") and len(word) > 1 and set(word[1:]) <= PARSE_NEUTRAL_FLAGS:
            i += 1
        elif (len(word) > 2 and word.startswith("-") and word.endswith("o") and set(word[1:-1]) <= PARSE_NEUTRAL_FLAGS
              and operand in PARSE_NEUTRAL_OPTIONS):
            i += 2  # a flag word ending in o takes the next word as its -o option, as -euo pipefail does
        else:
            return None
    ksh = bool(words) and os.path.basename(words[0]) == "ksh"  # ksh has the extended patterns on, always
    if (ksh or _shopt_extglob(_unquoted_text(data))) and "extglob" not in options:
        # A script that turns extglob on with ``shopt -s extglob`` has its patterns parsed only after that
        # runs, and bash -n runs nothing: so the option is given when a line of the script turns it on.
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
                           "--no-cache", *map(str, files)], timeout=LINT_TIMEOUT_SEC, env=process.Env.only(),
                  capture_limit=None)  # the report is parsed whole: a cut one would drop the note
    items = _json_list(done)
    if items is None:
        return None
    codes = [str(item.get("code") or "syntax") for item in items if isinstance(item, dict)]
    return Counter(code for code in codes if code not in SYNTAX_CODES)


def _shellcheck_counts(shellcheck: str, files: list[Path], runner: process.Runner) -> Optional[Counter]:
    done = _spawn(runner, [shellcheck, "-f", "json", "--norc", *map(str, files)],
                  timeout=LINT_TIMEOUT_SEC, env=process.Env.only(), capture_limit=None)
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

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Token-free checks for risky shell commands in a skill and its bundled text files.

Nothing is run and nothing is sent: these are regular expressions over the text. Three codes,
each a ``warn`` in :mod:`dokimi.external.validate`:

- ``pipe-to-shell``: ``curl`` or ``wget`` piped into a shell or an interpreter (``| sh``,
  ``| sudo bash``, ``| python3``), which runs a download without a look at it.
- ``permission-bypass-flag``: ``--dangerously-skip-permissions``, ``--yolo`` or
  ``bypassPermissions``, which switch off a coding agent's permission prompts.
- ``install-unpinned``: a ``pip``, ``npm``, ``pnpm`` or ``npx`` install that names a package with no
  version. One finding per skill, with the count of distinct ``<manager> <name>`` pairs: the same install
  shown twice is one, and a command inside the quoted text of an ``echo`` or ``printf`` is not an install.
  A requirements file, a local path, a URL and a ``git+`` source are not named packages, so they are not counted.

Where each check reads: ``pipe-to-shell`` and ``permission-bypass-flag`` read every line of the
text, since their tokens are distinctive. Installs are read from code only: the fenced blocks and
code spans of a prose file (Markdown, .mdx, .txt, .rst, .adoc), the string values of a .json file
(each cut where a sentence goes on, at ", " or ". "), and the whole of any other text file
(a script, say). So "pip install to get started" in a sentence is not an install. ``npx tsc`` is not counted in a
file whose code names ``typescript``, since it runs that package's compiler. ``curl | sh`` with no argument for
``curl`` is not a download, so ``pipe-to-shell`` does not read it. A skill at the
root of a scanned checkout reads its own ``SKILL.md`` only, since its bundled files are the rest of that
checkout.

The text is read in each of its :func:`dokimi.external.lookalikes.readings`, so a Cyrillic, Greek or fullwidth
look-alike letter does not hide a command.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import shlex
from pathlib import Path
from typing import Iterator, Sequence

from dokimi.skills.loader import VENDORED_DIR_NAMES

from .lookalikes import readings
from .skill_markdown import code_spans

BUNDLED_MAX_BYTES = 256 * 1024
BUNDLED_MAX_FILES = 200
EXAMPLES_SHOWN = 3
# Prose files: their installs are read from code (fences and spans), as in Markdown.
PROSE_SUFFIXES = (".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc")
# JSON files: their installs are read from the string values, each cut at its first ", " or ". ", where prose goes on.
JSON_SUFFIXES = (".json",)
JSON_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
# a ", " or ". " that follows a word; a lone "." is a path (``pip install . requests``), not a full stop
JSON_KEY_END = re.compile(r"\s*:")
PROSE_BREAK = re.compile(r"(?<=[^\s/,.])[,.][ \t]")
# A program run by npx that a package the same file installs provides: ``npx tsc`` runs the project's compiler.
PROVIDED_BY = {"tsc": "typescript"}

# A word that runs what it reads (exec, eval, a shell, a child process); a word that ends a string
# literal ("eval") is data. A command substitution (``$(``, a backtick) hands the program to the shell.
_EXEC = (r"(?:(?<!\w)(?:exec|eval|system|popen|spawn|subprocess|child_process|__import__|Function|runpy"
         r"|instance_eval|instance_exec|class_eval|module_eval|qx)(?!\w*['\"])|\$\(|`)")
# The inline program after ``-c``/``-e``: one quoted string (it may span lines; a double-quoted one holds ``\"``) or one
# word, none of it running anything, and ended there: a quoted segment glued to it is part of the program.
_CLEAN_PROGRAM = (
    rf"(?:'(?:(?!{_EXEC})[^'])*'|\"(?:\\[\s\S]|(?!{_EXEC})[^\"\\])*\"|(?:(?!{_EXEC})[^\s;&|'\"])+)(?![^\s;&|])"
)
# Bounded (500 pieces) so a long line of curl words stays linear; ``||`` is not a pipe, ``|&`` is.
# A quoted string is taken whole, so a ``&`` or ``;`` in a quoted URL does not end the command.
_PIPE_TO = (
    r"\|&?(?!\|)(?:[ \t]|\r?\n|\\\r?\n)*"
    r"(?:sudo[ \t]+(?:-[\w-]+(?:[ \t]+[\w.:][\w.:-]*)?[ \t]+)*)?(?:env[ \t]+)?(?:/(?:[\w.-]+/)*)?"
)
PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget)\b(?:[ \t]|\\\r?\n)+(?![\s|])(?:[^\n;&|\\'\"]|'[^'\n]*'|\"[^\"\n]*\"|\\\r?\n|\\[^\n]|\|(?!\|)){0,500}?"
    + _PIPE_TO + r"(?:"
    # An inline program (`python -c`, `node -e`, `ruby -e`) or `python -m json.tool` takes the download as data,
    # unless the program execs or evals it; a shell's `-e` and Ruby's `-E` (an encoding) are not inline programs.
    r"(?:(?:ba|z|da|k)?sh|fish)\b"
    rf"|python(?:3(?:\.\d+)?)?(?![\w.])(?![ \t]+-c(?![\w-])[ \t]+{_CLEAN_PROGRAM})(?![ \t]+-m[ \t]+json\.tool(?![\w.-]))"
    rf"|node\b(?![ \t]+-[ep](?![\w-])[ \t]+{_CLEAN_PROGRAM})"
    rf"|perl\b(?![ \t]+-[eE](?![\w-])[ \t]+{_CLEAN_PROGRAM})"
    rf"|ruby\b(?![ \t]+-e(?![\w-])[ \t]+{_CLEAN_PROGRAM})"
    r")"
)
PERMISSION_BYPASS = re.compile(
    r"(?<![\w-])--dangerously-skip-permissions(?![\w-])|(?<![\w-])--yolo(?![\w-])|\bbypassPermissions\b"
)
# the rest of the command, bounded like PIPE_TO_SHELL; a quoted string is taken whole, so a ``;`` in a quoted
# requirement marker (``'flask; python_version >= "3.10"'``) does not end the install
WORDS = r"((?:'[^'\n]*'|\"[^\"\n]*\"|[^\n;&|`#\\]|\\\r?\n|\\[^\n]){0,1000})"
PIP = re.compile(r"(?<![\w.-])(?:python3?(?:\.\d+)?[ \t]+-m[ \t]+pip|uv[ \t]+pip|pip3?(?:\.\d+)?)[ \t]+install\b" + WORDS)
NPM = re.compile(r"(?<![\w.-])npm[ \t]+(?:install|i|add)\b" + WORDS)
NPX = re.compile(r"(?<![\w.-])npx\b" + WORDS)
PIP_NAME = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?(.*)")
PIP_VERSION = re.compile(r"\s*(?:===|==|>=|<=|~=|!=|<|>|@)")
NPM_NAME = re.compile(r"(@[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*|[A-Za-z0-9][\w.-]*)(?:@(.*))?")
VERSION_ONLY = re.compile(r"v?\d[\d.]*")
REDIRECT = re.compile(r"(?:\d|&)?(?:>>?|<)")  # a redirection operator, with its file descriptor
NPM_RANGE = re.compile(r"[\^~<>=]*v?\d")  # a version or a range; a tag such as latest is not one
# options whose next word is their value, not a package
PIP_VALUE_OPTIONS = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-t", "--target", "-i", "--index-url", "--extra-index-url",
    "-f", "--find-links", "--prefix", "--root", "--cache-dir", "--src", "-e", "--editable", "--timeout",
    "--retries", "--python-version", "--platform", "--implementation", "--abi", "--progress-bar", "--log",
    "--proxy", "--trusted-host", "--upgrade-strategy", "--config-settings", "-C", "--global-option",
    "--install-option"})
NPM_VALUE_OPTIONS = frozenset({
    "--prefix", "--registry", "--cache", "--userconfig", "--workspace", "-w", "--tag", "--loglevel",
    "--omit", "--include", "--before", "--scope", "--save-prefix", "--otp", "--proxy", "--fetch-timeout"})
# npx takes npm's config options, so its value options are npm's, plus its own --call; --package and -p are not
# here, since their value is a selection
NPX_VALUE_OPTIONS = NPM_VALUE_OPTIONS | frozenset({"-c", "--call"})
# pnpm's value options: npm's, less ``-w``/``--workspace`` (flags in pnpm), plus a workspace filter, a directory, the
# store and a catalog name
PNPM_VALUE_OPTIONS = (NPM_VALUE_OPTIONS - {"-w", "--workspace"}) | frozenset(
    {"--filter", "-F", "--dir", "-C", "--store-dir", "--save-catalog-name", "--reporter", "--modules-dir",
     "--virtual-store-dir", "--lockfile-dir", "--global-dir"})
# options before the subcommand: a value option takes the next word (quoted or not); any other flag stands alone,
# with an attached quoted value; the lookahead keeps the two parses apart, so a run of them cannot backtrack
def _pnpm_options(value_options: frozenset[str]) -> str:
    alt = "|".join(re.escape(o) for o in sorted(value_options, key=len, reverse=True))
    return (
        r"(?:[ \t]+(?:(?:" + alt + r")[ \t]+(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s;&|'\"][^\s;&|]*)"
        r"|(?!(?:" + alt + r")(?:\s|$))-(?:[^\s;&|'\"]|'[^'\n]*'|\"[^\"\n]*\")*))*"
    )


PNPM = re.compile(r"(?<![\w.-])pnpm" + _pnpm_options(PNPM_VALUE_OPTIONS) + r"[ \t]+(?:install|i|add)\b" + WORDS)
# ``pnpm dlx`` and ``pnpx`` run a package as ``npx`` does; ``--package`` before ``dlx`` is a selection (group 1)
PNPM_DLX = re.compile(
    r"(?<![\w.-])(?:pnpm(" + _pnpm_options(PNPM_VALUE_OPTIONS | {"--package", "-p"}) + r")[ \t]+dlx|pnpx)\b" + WORDS)
NON_PACKAGE_SUFFIXES = (".txt", ".whl", ".gz", ".zip", ".tgz", ".tar", ".cfg", ".toml", ".lock")
# ``echo`` or ``printf`` at the start of a command, after control keywords, ``command``/``builtin``/``exec`` and
# ``NAME=value`` assignments (``then echo "..."``, ``LC_ALL=C printf ...``)
ECHO_COMMAND = re.compile(
    r"[ \t]*(?:(?:if|then|else|elif|do|while|until|time|!|\{|command|builtin|exec)[ \t]+"
    r"|[A-Za-z_]\w*=(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s;&|()'\"`]*)[ \t]+)*(?:echo|printf)(?![\w.-])")
# ``|`` at the end of a command that feeds a shell: what the command printed is run
PIPED_SHELL = re.compile(_PIPE_TO + r"(?:(?:ba|z|da|k)?sh|fish)(?![\w.-])")
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _lines_of(text: str, matches: Iterator[re.Match]) -> list[int]:
    newlines = [m.start() for m in re.finditer("\n", text)]  # one scan, then a bisect per match (linear, not quadratic)
    return sorted({bisect.bisect_left(newlines, m.start()) + 1 for m in matches})


MAX_NESTING = 50  # substitutions entered inside one another; deeper ones are stepped over, so the stack stays bounded
BLOCK_END = "\0"  # ends a fenced block or code span: a quote an ``echo`` opens in one never closes in the next


def _code_text(text: str, markdown: bool) -> str:
    """The text an install is read from: all of it, or for Markdown its fenced blocks and code spans."""
    if not markdown:
        return text
    code: list[str] = []
    prose: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        if fence is None:
            opened = FENCE_OPEN.match(line)
            # CommonMark: a backtick fence's info string has no backtick, so ```pip install x``` is a code span
            if opened and not (opened.group(1)[0] == "`" and "`" in line[opened.end():]):
                fence = opened.group(1)
            else:
                prose.append(line)
            continue
        stripped = line.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            fence = None
            code.append(BLOCK_END)
        else:
            code.append(line)
    # a code span does not run across a paragraph break
    paragraphs = re.split(r"\n[ \t]*\n", "\n".join(prose))
    spans = [m.group(2) for para in paragraphs for m in code_spans(para)]
    return "\n".join(code + [part for span in spans for part in (span, BLOCK_END)])


def _before_prose(line: str) -> str:
    """``line`` up to its first ``, `` or ``. `` that is outside quotes, where a sentence goes on."""
    for cut in PROSE_BREAK.finditer(line):
        before = line[:cut.start()]
        if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:
            return before
    return line


def _json_strings(text: str) -> str:
    """The string values of a JSON text, one per line, each cut where a sentence goes on (``, `` or ``. ``)."""
    lines = []
    for m in JSON_STRING.finditer(text):
        if JSON_KEY_END.match(text, m.end()):
            continue  # an object key is a name, not a value
        try:
            value = json.loads(m.group(0))
        except ValueError:
            value = m.group(0)[1:-1]
        lines += [_before_prose(line) for line in value.splitlines()]  # a newline ends a command: one line each
    return "\n".join(lines)


def _words(rest: str, value_options: frozenset[str]) -> list[str]:
    """The words of a command's rest: shell-split (quotes kept whole), without an option's value.

    A word that is only a version number (``30``, ``2.0``) is not a package name, so it is dropped here too.
    """
    rest = re.sub(r"\\\r?\n", " ", rest)  # a continued line is the same command
    try:
        # punctuation_chars: an unquoted ``>`` or ``<`` is a token of its own, so ``numpy>install.log`` is
        # ``numpy`` and a redirect; a quoted one stays inside its word
        lexer = shlex.shlex(rest, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:  # an unbalanced quote: split on whitespace, as the words are written
        tokens = rest.split()
    words: list[str] = []
    take_value = skip_target = False
    for word in tokens:
        if skip_target:  # the file a redirection writes to or reads from is not a package
            skip_target = False
        elif take_value:  # the value of an option is never a package, even a bare number
            take_value = False
        elif REDIRECT.match(word):  # ``>log``, ``2>&1``: one token; ``>`` alone takes the next word too
            skip_target = REDIRECT.fullmatch(word) is not None
        elif word in value_options:
            take_value = True
        elif not VERSION_ONLY.fullmatch(word):
            words.append(word)
    return words


def _unpinned_pip(rest: str) -> list[str]:
    names = []
    for word in _words(rest, PIP_VALUE_OPTIONS):
        if not word or word[0] in "-./~$<{[" or any(c in word for c in "/:"):
            continue  # a flag, a path, a URL or a git+ source
        if word.lower().endswith(NON_PACKAGE_SUFFIXES):
            continue  # a requirements file or an archive
        match = PIP_NAME.fullmatch(word)
        if match and not PIP_VERSION.match(match.group(3)):
            names.append(match.group(1).lower())
    return names


def _npm_pinned(spec: str | None) -> bool:
    """Whether an npm version spec pins a version. An alias (``npm:<name>@<version>``) pins its target's version.
    A URL, git, ``github:``, ``file:`` or ``link:`` source, a path, or a GitHub shorthand (``user/repo``) is not a
    registry version: it is not counted, so it is treated as pinned. A registry version has neither ``:`` nor ``/``."""
    if spec is not None and spec.startswith("npm:"):
        target = spec[len("npm:"):]
        at = target.rfind("@")  # the last @: a scoped target's @scope is not its version
        spec = target[at + 1:] if at > 0 else None
    if spec is not None and (":" in spec or "/" in spec):
        return True
    return spec is not None and NPM_RANGE.match(spec) is not None


def _unpinned_npm(rest: str, value_options: frozenset[str] = NPM_VALUE_OPTIONS) -> list[str]:
    names = []
    for word in _words(rest, value_options):
        word = word.rstrip("\"')")
        match = NPM_NAME.fullmatch(word) if word and not word.startswith("-") else None
        if not match or word.lower().endswith(NON_PACKAGE_SUFFIXES):
            continue  # a flag, a path, a URL or a tarball
        if not _npm_pinned(match.group(2)):
            names.append(match.group(1))
    return names


def _unpinned_npx(rest: str, value_options: frozenset[str] = NPX_VALUE_OPTIONS,
                  provided: frozenset[str] = frozenset()) -> list[str]:
    """npx runs the packages it is given: each ``--package``/``-p`` selection before the program, or else the program
    itself (the first word that is not a flag). A selection names the package, so the program is not inferred from
    it. Words after the program are its own arguments: its ``-p`` is not npx's selection. A program in ``provided``
    (``tsc``) is run from a package the file installs, so it is not counted when it is the program, though a
    ``--package`` selection of it is."""
    words = _words(rest, value_options)
    selected: list[str] = []
    executable = None
    i = 0
    while i < len(words):
        word = words[i]
        if word in ("--package", "-p") and i + 1 < len(words):
            selected.append(words[i + 1])
            i += 2
            continue
        if word.startswith("--package="):
            selected.append(word[len("--package="):])
        elif word.startswith("-p") and not word.startswith("--") and len(word) > 2:
            selected.append(word[2:])
        elif word and not word.startswith("-"):
            executable = word
            break
        i += 1
    names = []
    for spec in selected or ([executable] if executable and executable not in provided else []):
        match = NPM_NAME.fullmatch(spec.rstrip("\"')"))
        if match is not None and not spec.lower().endswith(NON_PACKAGE_SUFFIXES) and not _npm_pinned(match.group(2)):
            names.append(match.group(1))
    return names


def _unpinned_dlx(head: str | None, rest: str) -> list[str]:
    """``pnpm dlx``: a ``--package``/``-p`` selection before ``dlx`` names the package, as one after it does."""
    try:
        words = shlex.split(head or "")
    except ValueError:  # an unbalanced quote: the words as written
        words = (head or "").split()
    selected: list[str] = []
    i = 0
    while i < len(words):  # a value option's value is not read: ``--dir 'a --package=x'`` selects nothing
        word, i = words[i], i + 1
        if word.startswith("--package="):
            selected.append(word[len("--package="):])
        elif word in ("--package", "-p") and i < len(words):
            selected.append(words[i])
            i += 1
        elif word in PNPM_VALUE_OPTIONS:
            i += 1
        elif word.startswith("-p") and not word.startswith("--") and len(word) > 2:
            selected.append(word[2:])
    return _unpinned_npx(" ".join(f"--package {spec}" for spec in selected) + " " + rest, PNPM_VALUE_OPTIONS)


def _blank(mask: list[str], pieces: Sequence[tuple[int, ...]]) -> None:
    for start, end, *_ in pieces:
        for k in range(start, end):
            if mask[k] not in ("\n", BLOCK_END):
                mask[k] = " "


def _quoted(code: str, i: int, memo: dict, level: int) -> tuple[int, list[tuple[int, int]], list[tuple[int, int]]]:
    """The index of the quote that closes the string opened at ``i``, the ``(start, end)`` of its text, less any
    command substitution in it (that runs), and the pieces that substitution's own echoes print; ``-1`` when it
    never closes in its own block or span: not a string."""
    quote, j, seg, pieces, inner = code[i], i + 1, i + 1, [], []
    while j < len(code) and code[j] not in (quote, BLOCK_END):
        if quote == '"' and (code[j] == "`" or code.startswith("$(", j)):
            pieces.append((seg, j))
            j, printed = _substitution(code, j, memo, level)
            seg = j
            inner += printed
        else:
            j += 2 if quote == '"' and code[j] == "\\" else 1
    if j >= len(code) or code[j] == BLOCK_END:
        return -1, [], []
    return j, pieces + [(seg, j)], inner


def _substitution(code: str, i: int, memo: dict, level: int) -> tuple[int, list[tuple[int, int]]]:
    """The index after the ``$(...)`` or backtick substitution opened at ``i``, and the text its ``echo`` and
    ``printf`` commands print; its commands are scanned too. The text is returned, not blanked: it is the output
    of the command around the substitution, which a pipe to a shell may run.

    ``memo`` holds where each substitution ended, so one that a string failing to close makes the scan meet again is
    not scanned again. Past :data:`MAX_NESTING` levels an opener is stepped over, not entered."""
    stop, skip = (")", 2) if code[i] == "$" else ("`", 1)
    if i in memo:
        return memo[i]
    if level >= MAX_NESTING:
        return i + skip, []
    printed: list[tuple[int, int]] = []
    end = _commands(code, printed, i + skip, stop, memo, level + 1)
    memo[i] = (end + 1 if end < len(code) and code[end] == stop else end), printed
    return memo[i]


# marks an echo whose stdout is redirected; a piece with a third item is blanked even when the command feeds a shell
REDIRECTED = (-1, -1)
# marks an echo whose stderr already goes where its stdout goes (``2>&1``): a later ``>&2`` leaves stdout where it was
ERR_TO_OUT = (-2, -2)


def _flush(out, echo: list, piped: bool) -> None:
    """Add the text of a finished echo to ``out``. Piped to a shell, its text is kept (it runs), unless its stdout
    is redirected or it is already marked as printed by a command inside it."""
    pieces = [piece for piece in echo if piece not in (REDIRECTED, ERR_TO_OUT)]
    if REDIRECTED in echo:
        out += [(start, end, True) for start, end, *_ in pieces]
    else:
        out += [piece for piece in pieces if len(piece) == 3] if piped else pieces


def _dup_of(code: str, i: int, fd: str) -> bool:
    """Whether the ``>`` at ``i`` is ``>&<fd>`` for exactly that descriptor (``>&10`` is descriptor 10)."""
    return code.startswith("&" + fd, i + 1) and not code[i + 3:i + 4].isdigit()


def _stdout_redirected(code: str, i: int) -> bool:
    """Whether the ``>`` at ``i`` opens a redirection of stdout to something other than stdout: not the second ``>`` of
    ``>>``, not ``2>``/``2>>`` (a descriptor other than 1), not ``>&1``."""
    return code[i] == ">" and code[i - 1] not in ">023456789" and not _dup_of(code, i, "1")


def _other(code, i, start, echo):
    """Step over the character at ``i`` (and what it escapes), or over a comment: its quotes and substitutions are
    not shell syntax, so it runs to the end of the line, and after an ``echo`` it is text that is not run.
    Returns ``(start, index, echo)``."""
    if code[i] == "#" and (i == 0 or code[i - 1] in " \t\n;&|()"):
        end = i
        while end < len(code) and code[end] not in ("\n", BLOCK_END):
            end += 1
        return False, end, echo if echo is None else echo + [(i, end, True)]  # a comment never runs, even piped
    before = code[i - 2:i - 1]
    stderr = code[i] == ">" and code[i - 1] == "2" and (before == "" or before in " \t\n;&|()")  # a standalone ``2>``
    if echo is not None and stderr and _dup_of(code, i, "1"):
        echo = echo + [ERR_TO_OUT] if ERR_TO_OUT not in echo else echo
    elif echo is not None and stderr and ERR_TO_OUT in echo and not _dup_of(code, i, "2"):
        echo = [piece for piece in echo if piece != ERR_TO_OUT]  # stderr goes elsewhere now: ``2>&1 2>f >&2`` (``2>&2`` changes nothing)
    elif echo is not None and REDIRECTED not in echo and _stdout_redirected(code, i):
        if not (ERR_TO_OUT in echo and _dup_of(code, i, "2")):  # ``2>&1 >&2``: stdout is still the pipe
            echo = echo + [REDIRECTED]  # stdout goes to a file or another descriptor: nothing reaches a pipe
    return start and code[i] in " \t", i + (2 if code[i] == "\\" else 1), echo


def _in_redirection(code: str, i: int) -> bool:
    return code[i - 1:i] in (">", "<") or code[i + 1:i + 2] == ">"


def _paren_depth(depth: int, c: str) -> int:
    return depth + 1 if c == "(" else max(depth - 1, 0) if c == ")" else depth


def _collect(echo, out, text, printed) -> None:
    """The ``text`` of a string belongs to the echo being read, if any; what a substitution in it printed does too,
    and otherwise is printed outright."""
    if echo is not None:
        echo += text + printed
    else:
        out += printed


def _commands(code: str, out: list[tuple[int, int]], i: int, stop: str, memo: dict, level: int) -> int:
    """Add to ``out`` the quoted text of each ``echo`` or ``printf`` from ``i`` to the ``stop`` that ends the list
    (or a block end): the text is printed, not run, unless the command feeds a shell. A substitution in it runs.
    Returns the index where it stopped."""
    depth, start = 0, True
    echo: list[tuple[int, int]] | None = None  # the text pieces of the echo or printf being read
    while i < len(code) and code[i] != BLOCK_END and not (code[i] == stop and depth == 0):
        c = code[i]
        match = ECHO_COMMAND.match(code, i) if start and echo is None else None
        if match:
            echo, start, i = [], False, match.end()
        elif c in "'\"" and (quoted := _quoted(code, i, memo, level))[0] >= 0:
            _collect(echo, out, quoted[1], quoted[2])
            start, i = False, quoted[0] + 1
        elif c == "`" or code.startswith("$(", i):
            i, printed = _substitution(code, i, memo, level)
            _collect(echo, out, [], printed)
            start = False
        elif c == "&" and _in_redirection(code, i):
            i += 1  # a redirection (``>&2``, ``&>f``), not a separator
        elif c in ";&|\n()":
            if echo:
                _flush(out, echo, c == "|" and PIPED_SHELL.match(code, i) is not None)
            echo, start, i = None, True, i + 1
            depth = _paren_depth(depth, c)
        else:
            start, i, echo = _other(code, i, start, echo)
    if echo:
        _flush(out, echo, False)
    return i


def _without_printed(code: str) -> str:
    """``code`` with the text ``echo`` and ``printf`` print blanked, so an install in it is not read."""
    mask, i = list(code), 0
    memo: dict = {}
    printed: list[tuple[int, int]] = []
    while i < len(code):
        i = _commands(code, printed, i, "", memo, 0) + 1
    _blank(mask, printed)
    return "".join(mask)


def _unpinned_at(code: str, provided: frozenset[str] = frozenset()) -> list[tuple[int, str, int, list[str]]]:
    """``(offset in code, command, end of its arguments, ["pip name", ...])`` of each install command, with an empty
    list when all its packages are pinned. The offset is where the arguments begin, which folding a prefix
    (``ｕｖ pip``) does not move. The quoted text of an ``echo`` or ``printf`` is blanked first, in place, so no
    offset moves."""
    code = _without_printed(code)
    found = [(m.start(1), "pip", m.end(1), [f"pip {n}" for n in _unpinned_pip(m.group(1))]) for m in PIP.finditer(code)]
    found += [(m.start(1), "npm", m.end(1), [f"npm {n}" for n in _unpinned_npm(m.group(1))]) for m in NPM.finditer(code)]
    found += [(m.start(1), "pnpm", m.end(1), [f"pnpm {n}" for n in _unpinned_npm(m.group(1), PNPM_VALUE_OPTIONS)])
              for m in PNPM.finditer(code)]
    found += [(m.start(2), "pnpm", m.end(2), [f"pnpm {n}" for n in _unpinned_dlx(*m.groups())])
              for m in PNPM_DLX.finditer(code)]
    found += [(m.start(1), "npx", m.end(1), [f"npx {n}" for n in _unpinned_npx(m.group(1), provided=provided)]) for m in NPX.finditer(code)]
    return found


def _ends_inside_url(reading: str, end: int) -> bool:
    """Whether the whitespace-delimited word of ``reading`` that holds index ``end`` is a URL: a fullwidth ``；``
    there is a URL character, not a separator."""
    start = max(reading.rfind(" ", 0, end), reading.rfind("\n", 0, end), reading.rfind("\t", 0, end)) + 1
    stop = min((i for i in (reading.find(c, end) for c in " \n\t") if i >= 0), default=len(reading))
    return "://" in reading[start:stop]


def _unpinned_in_readings(text: str, markdown: bool, json_text: bool = False) -> list[str]:
    """The unpinned installs of a text, read as written and folded. The code is taken from the text as written,
    once, so folding cannot turn a character into a Markdown delimiter. A command found in several readings at the
    same place is counted once, with the packages of the most folded reading that finds it, even when that reading
    reads fewer arguments: it has ended the command at a fullwidth separator (a fullwidth ``＝＝`` that pins a package
    may be followed by one). The exception is a separator inside a URL word, which the earlier reading keeps. A command at another place is another install."""
    best: dict[tuple[int, str], tuple[int, list[str], str]] = {}
    code = _json_strings(text) if json_text else _code_text(text, markdown)
    for reading in readings(code):
        installed = _without_printed(reading)
        provided = frozenset(
            run for run, pkg in PROVIDED_BY.items()
            if re.search(rf"(?<![\w.-])(?:npm|pnpm)[ \t]+(?:install|i|add)\b[^\n;&|]*(?<![\w@/.-]){pkg}(?![\w-])", installed))
        for offset, command, end, labels in _unpinned_at(reading, provided):
            key = (offset, command)
            if key in best and best[key][0] != end and _ends_inside_url(best[key][2], end):
                continue  # the earlier reading reads a URL character where this one sees a separator
            best[key] = (end, labels, reading)
    return [label for key in sorted(best) for label in best[key][1]]



def _is_skill_dir(path: Path) -> bool:
    """Whether a directory holds a SKILL.md of its own. One it cannot read is not one: the walk skips it."""
    try:
        return (path / "SKILL.md").is_file()
    except OSError:  # e.g. EACCES: is_file re-raises it, and the scan must not stop on a directory it cannot read
        return False


def _bundled_texts(skill_dir: Path) -> Iterator[tuple[str, str]]:
    """(path relative to the skill, text) of each bundled text file, sorted; SKILL.md is not one of them.

    Symlinks, hidden directories, :data:`VENDORED_DIR_NAMES`, another skill's directory, files over
    :data:`BUNDLED_MAX_BYTES` are not read; a binary file (a NUL byte in the first 8 KiB) is read but not yielded.
    Every file read counts toward :data:`BUNDLED_MAX_FILES`, text or not, so the limit bounds the I/O.
    """
    read = 0
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in VENDORED_DIR_NAMES and not _is_skill_dir(here / d)
        )
        for name in sorted(filenames):
            path = here / name
            if here == skill_dir and name == "SKILL.md":
                continue
            if read >= BUNDLED_MAX_FILES:
                continue  # keep walking: a stopped os.walk leaves its directory handles open
            try:  # lstat (is_symlink) too: a listed file in a directory without x raises EACCES here
                if path.is_symlink() or not path.is_file() or path.stat().st_size > BUNDLED_MAX_BYTES:
                    continue
                read += 1  # charged before the read: a binary file uses the budget too
                data = path.read_bytes()
                if b"\0" in data[:8192]:
                    continue
                text = data.decode("utf-8-sig")  # a byte order mark is not text
            except (OSError, UnicodeDecodeError):
                continue
            yield path.relative_to(skill_dir).as_posix(), text


def command_findings(skill_path: Path, *, at_root: bool) -> list[tuple[str, str]]:
    """``(code, message)`` for each command check that fires on the skill whose ``SKILL.md`` is ``skill_path``.

    The whole file is read, so a line number is the line in the file. ``at_root``: the ``SKILL.md``
    sits in the scanned root, so no bundled file is read.
    """
    sources = [("SKILL.md", skill_path.read_text(encoding="utf-8-sig"))]
    if not at_root:
        sources += list(_bundled_texts(skill_path.parent))
    return _findings_in(sources)


def skill_md_findings(text: str) -> list[tuple[str, str]]:
    """:func:`command_findings` for one ``SKILL.md`` text alone, with no bundled file: an upstream version's bytes."""
    return _findings_in([("SKILL.md", text)])


def _findings_in(sources: list[tuple[str, str]]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    unpinned: list[str] = []
    for rel, raw in sources:
        # Each reading of the text: folding a character that is not a letter can also end a match the raw text has.
        pipes = _lines_of(raw, (m for r in readings(raw) for m in PIPE_TO_SHELL.finditer(_without_printed(r))))
        if pipes:
            where = ", ".join(str(n) for n in pipes)
            findings.append((
                "pipe-to-shell",
                f"a download is piped into a shell or interpreter in {rel} (line {where}); "
                "save the file, read it, then run it",
            ))
        bypass = _lines_of(raw, (m for r in readings(raw) for m in PERMISSION_BYPASS.finditer(r)))
        if bypass:
            where = ", ".join(str(n) for n in bypass)
            findings.append((
                "permission-bypass-flag",
                f"{rel} names a flag or mode that turns off the agent's permission prompts (line {where}); "
                "say what it may run unprompted, or drop it",
            ))
        unpinned += _unpinned_in_readings(raw, rel.lower().endswith(PROSE_SUFFIXES), rel.lower().endswith(JSON_SUFFIXES))

    unpinned = list(dict.fromkeys(unpinned))  # one per package across SKILL.md and the bundled files
    if unpinned:
        shown = ", ".join(unpinned[:EXAMPLES_SHOWN]) + (", ..." if len(unpinned) > EXAMPLES_SHOWN else "")
        s = "" if len(unpinned) == 1 else "s"
        findings.append((
            "install-unpinned",
            f"{len(unpinned)} install{s} name no version ({shown}); pin each as `pkg==1.2.3` (pip) "
            "or `pkg@1.2.3` (npm)",
        ))
    return findings

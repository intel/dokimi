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
code spans of a prose file (Markdown, .mdx, .txt, .rst, .adoc), and the whole of any other text file
(a script, say). So "pip install to get started" in a sentence is not an install. A skill at the
root of a scanned checkout reads its own ``SKILL.md`` only, since its bundled files are the rest of that
checkout.
"""

from __future__ import annotations

import bisect
import os
import re
import shlex
from pathlib import Path
from typing import Iterator

from dokimi.skills.loader import VENDORED_DIR_NAMES

from .skill_markdown import CODE_SPAN_RE

BUNDLED_MAX_BYTES = 256 * 1024
BUNDLED_MAX_FILES = 200
EXAMPLES_SHOWN = 3
# Prose files: their installs are read from code (fences and spans), as in Markdown.
PROSE_SUFFIXES = (".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc")

# Bounded (500 pieces) so a long line of curl words stays linear; ``||`` is not a pipe, ``|&`` is.
# A quoted string is taken whole, so a ``&`` or ``;`` in a quoted URL does not end the command.
PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget)\b(?:[^\n;&|\\'\"]|'[^'\n]*'|\"[^\"\n]*\"|\\\r?\n|\\[^\n]|\|(?!\|)){0,500}?"
    r"\|&?(?!\|)(?:[ \t]|\r?\n|\\\r?\n)*"
    r"(?:sudo[ \t]+(?:-[\w-]+(?:[ \t]+[\w.:][\w.:-]*)?[ \t]+)*)?(?:env[ \t]+)?(?:/(?:[\w.-]+/)*)?"
    r"(?:(?:ba|z|da|k)?sh|fish|python(?:3(?:\.\d+)?)?|node|perl|ruby)\b"
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
    {"--filter", "-F", "--dir", "-C", "--store-dir", "--save-catalog-name"})
_PNPM_VALUE_ALT = "|".join(re.escape(o) for o in sorted(PNPM_VALUE_OPTIONS, key=len, reverse=True))
# options before the subcommand: a value option takes the next word (quoted or not); any other flag stands alone,
# with an attached quoted value; the lookahead keeps the two parses apart, so a run of them cannot backtrack
PNPM = re.compile(
    r"(?<![\w.-])pnpm(?:[ \t]+(?:(?:" + _PNPM_VALUE_ALT + r")[ \t]+(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s;&|'\"][^\s;&|]*)"
    r"|(?!(?:" + _PNPM_VALUE_ALT + r")(?:\s|$))-(?:[^\s;&|'\"]|'[^'\n]*'|\"[^\"\n]*\")*))*"
    r"[ \t]+(?:install|i|add)\b" + WORDS
)
NON_PACKAGE_SUFFIXES = (".txt", ".whl", ".gz", ".zip", ".tgz", ".tar", ".cfg", ".toml", ".lock")
# ``echo`` or ``printf`` at the start of a command, after control keywords, ``command``/``builtin``/``exec`` and
# ``NAME=value`` assignments (``then echo "..."``, ``LC_ALL=C printf ...``)
ECHO_COMMAND = re.compile(
    r"[ \t]*(?:(?:if|then|else|elif|do|while|until|time|!|\{|command|builtin|exec)[ \t]+"
    r"|[A-Za-z_]\w*=(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s;&|()'\"`]*)[ \t]+)*(?:echo|printf)(?![\w.-])")
# ``|`` at the end of a command that feeds a shell: what the command printed is run
PIPED_SHELL = re.compile(r"\|(?!\|)&?[ \t]*(?:sudo[ \t]+)?(?:ba|z|da|k)?sh(?![\w.-])")
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _lines_of(text: str, matches: Iterator[re.Match]) -> list[int]:
    newlines = [m.start() for m in re.finditer("\n", text)]  # one scan, then a bisect per match (linear, not quadratic)
    return sorted({bisect.bisect_left(newlines, m.start()) + 1 for m in matches})


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
    spans = [m.group(2) for para in paragraphs for m in CODE_SPAN_RE.finditer(para)]
    return "\n".join(code + [part for span in spans for part in (span, BLOCK_END)])


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


def _unpinned_npx(rest: str) -> list[str]:
    """npx runs the packages it is given: each ``--package``/``-p`` selection before the program, or else the program
    itself (the first word that is not a flag). A selection names the package, so the program is not inferred from
    it. Words after the program are its own arguments: its ``-p`` is not npx's selection."""
    words = _words(rest, NPX_VALUE_OPTIONS)
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
    for spec in selected or ([executable] if executable else []):
        match = NPM_NAME.fullmatch(spec.rstrip("\"')"))
        if match is not None and not spec.lower().endswith(NON_PACKAGE_SUFFIXES) and not _npm_pinned(match.group(2)):
            names.append(match.group(1))
    return names


def _blank(mask: list[str], pieces: list[tuple[int, int]]) -> None:
    for start, end in pieces:
        for k in range(start, end):
            if mask[k] not in ("\n", BLOCK_END):
                mask[k] = " "


def _quoted(code: str, mask: list[str], i: int) -> tuple[int, list[tuple[int, int]]]:
    """The index of the quote that closes the string opened at ``i`` and the ``(start, end)`` of its text, less any
    command substitution in it (that runs); ``-1`` when it never closes in its own block or span: not a string."""
    quote, j, seg, pieces = code[i], i + 1, i + 1, []
    while j < len(code) and code[j] not in (quote, BLOCK_END):
        if quote == '"' and (code[j] == "`" or code.startswith("$(", j)):
            pieces.append((seg, j))
            j = seg = _substitution(code, mask, j)
        else:
            j += 2 if quote == '"' and code[j] == "\\" else 1
    if j >= len(code) or code[j] == BLOCK_END:
        return -1, []
    return j, pieces + [(seg, j)]


def _substitution(code: str, mask: list[str], i: int) -> int:
    """The index after the ``$(...)`` or backtick substitution opened at ``i``; its commands are scanned too."""
    stop, skip = (")", 2) if code[i] == "$" else ("`", 1)
    end = _commands(code, mask, i + skip, stop)
    return end + 1 if end < len(code) and code[end] == stop else end


def _commands(code: str, mask: list[str], i: int, stop: str) -> int:
    """Blank in ``mask`` the quoted text of each ``echo`` or ``printf`` from ``i`` to the ``stop`` that ends the list
    (or a block end): the text is printed, not run, unless the command feeds a shell. A substitution in it runs.
    Returns the index where it stopped."""
    depth, start = 0, True
    echo: list[tuple[int, int]] | None = None  # the text pieces of the echo or printf being read
    while i < len(code) and code[i] != BLOCK_END and not (code[i] == stop and depth == 0):
        c = code[i]
        match = ECHO_COMMAND.match(code, i) if start and echo is None else None
        if match:
            echo, start, i = [], False, match.end()
        elif c in "'\"" and (quoted := _quoted(code, mask, i))[0] >= 0:
            echo = echo + quoted[1] if echo is not None else echo
            start, i = False, quoted[0] + 1
        elif c == "`" or code.startswith("$(", i):
            start, i = False, _substitution(code, mask, i)
        elif c == "&" and (code[i - 1:i] in (">", "<") or code[i + 1:i + 2] == ">"):
            i += 1  # a redirection (``>&2``, ``&>f``), not a separator
        elif c in ";&|\n()":
            if echo and not (c == "|" and PIPED_SHELL.match(code, i)):
                _blank(mask, echo)
            echo, start, i = None, True, i + 1
            depth = depth + 1 if c == "(" else max(depth - 1, 0) if c == ")" else depth
        else:
            start, i = start and c in " \t", i + (2 if c == "\\" else 1)
    if echo:
        _blank(mask, echo)
    return i


def _without_printed(code: str) -> str:
    """``code`` with the text ``echo`` and ``printf`` print blanked, so an install in it is not read."""
    mask, i = list(code), 0
    while i < len(code):
        i = _commands(code, mask, i, "") + 1
    return "".join(mask)


def _unpinned_installs(code: str) -> list[str]:
    """One entry per distinct ``<manager> <name>``: the same install shown twice is one."""
    found: list[str] = []
    code = _without_printed(code)
    for manager, pattern, names in (
        ("pip", PIP, _unpinned_pip),
        ("npm", NPM, _unpinned_npm),
        ("pnpm", PNPM, lambda rest: _unpinned_npm(rest, PNPM_VALUE_OPTIONS)),
        ("npx", NPX, _unpinned_npx),
    ):
        found += [f"{manager} {n}" for m in pattern.finditer(code) for n in names(m.group(1))]
    return list(dict.fromkeys(found))


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

    findings: list[tuple[str, str]] = []
    unpinned: list[str] = []
    for rel, text in sources:
        pipes = _lines_of(text, PIPE_TO_SHELL.finditer(text))
        if pipes:
            where = ", ".join(str(n) for n in pipes)
            findings.append((
                "pipe-to-shell",
                f"a download is piped into a shell or interpreter in {rel} (line {where}); "
                "save the file, read it, then run it",
            ))
        bypass = _lines_of(text, PERMISSION_BYPASS.finditer(text))
        if bypass:
            where = ", ".join(str(n) for n in bypass)
            findings.append((
                "permission-bypass-flag",
                f"{rel} names a flag or mode that turns off the agent's permission prompts (line {where}); "
                "say what it may run unprompted, or drop it",
            ))
        unpinned += _unpinned_installs(_code_text(text, rel.lower().endswith(PROSE_SUFFIXES)))

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

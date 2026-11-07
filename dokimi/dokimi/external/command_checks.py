# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Token-free checks for risky shell commands in a skill and its bundled text files.

Nothing is run and nothing is sent: these are regular expressions over the text. Three codes,
each a ``warn`` in :mod:`dokimi.external.validate`:

- ``pipe-to-shell``: ``curl`` or ``wget`` piped into a shell or an interpreter (``| sh``,
  ``| sudo bash``, ``| python3``), which runs a download without a look at it.
- ``permission-bypass-flag``: ``--dangerously-skip-permissions``, ``--yolo`` or
  ``bypassPermissions``, which switch off a coding agent's permission prompts.
- ``install-unpinned``: a ``pip``, ``npm`` or ``npx`` install that names a package with no
  version. One finding per skill, with the count. A requirements file, a local path, a URL and
  a ``git+`` source are not named packages, so they are not counted.

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
    r"\|&?(?!\|)(?:[ \t]|\\\r?\n)*"
    r"(?:sudo[ \t]+(?:-[\w-]+(?:[ \t]+[\w.:-]+)?[ \t]+)*)?(?:env[ \t]+)?(?:/(?:[\w.-]+/)*)?"
    r"(?:(?:ba|z|da|k)?sh|fish|python(?:3(?:\.\d+)?)?|node|perl|ruby)\b"
)
PERMISSION_BYPASS = re.compile(
    r"(?<![\w-])--dangerously-skip-permissions(?![\w-])|(?<![\w-])--yolo(?![\w-])|\bbypassPermissions\b"
)
WORDS = r"((?:[^\n;&|`#\\]|\\\r?\n|\\[^\n]){0,1000})"  # the rest of the command, bounded like PIPE_TO_SHELL
PIP = re.compile(r"(?<![\w.-])(?:python3?(?:\.\d+)?[ \t]+-m[ \t]+pip|uv[ \t]+pip|pip3?(?:\.\d+)?)[ \t]+install\b" + WORDS)
NPM = re.compile(r"(?<![\w.-])npm[ \t]+(?:install|i|add)\b" + WORDS)
NPX = re.compile(r"(?<![\w.-])npx\b" + WORDS)
PIP_NAME = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?(.*)")
PIP_VERSION = re.compile(r"\s*(?:===|==|>=|<=|~=|!=|<|>|@)")
NPM_NAME = re.compile(r"(@[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*|[A-Za-z0-9][\w.-]*)(?:@(.*))?")
VERSION_ONLY = re.compile(r"v?\d[\d.]*")
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
NON_PACKAGE_SUFFIXES = (".txt", ".whl", ".gz", ".zip", ".tgz", ".tar", ".cfg", ".toml", ".lock")
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _lines_of(text: str, matches: Iterator[re.Match]) -> list[int]:
    newlines = [m.start() for m in re.finditer("\n", text)]  # one scan, then a bisect per match (linear, not quadratic)
    return sorted({bisect.bisect_left(newlines, m.start()) + 1 for m in matches})


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
            if opened:
                fence = opened.group(1)
            else:
                prose.append(line)
            continue
        stripped = line.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            fence = None
        else:
            code.append(line)
    spans = [m.group(2) for m in CODE_SPAN_RE.finditer("\n".join(prose))]
    return "\n".join(code + spans)


def _words(rest: str, value_options: frozenset[str]) -> list[str]:
    """The words of a command's rest: shell-split (quotes kept whole), without an option's value.

    A word that is only a version number (``30``, ``2.0``) is not a package name, so it is dropped here too.
    """
    rest = re.sub(r"\\\r?\n", " ", rest)  # a continued line is the same command
    try:
        tokens = shlex.split(rest)
    except ValueError:  # an unbalanced quote: split on whitespace, as the words are written
        tokens = rest.split()
    words: list[str] = []
    take_value = False
    for word in tokens:
        if take_value:  # the value of an option is never a package, even a bare number
            take_value = False
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


def _unpinned_npm(rest: str) -> list[str]:
    names = []
    for word in _words(rest, NPM_VALUE_OPTIONS):
        match = NPM_NAME.fullmatch(word) if word and not word.startswith("-") else None
        if not match or word.lower().endswith(NON_PACKAGE_SUFFIXES):
            continue  # a flag, a path, a URL or a tarball
        if not match.group(2) or not NPM_RANGE.match(match.group(2)):
            names.append(match.group(1))
    return names


def _unpinned_npx(rest: str) -> list[str]:
    """npx runs one package: the first word that is not a flag, unless it is pinned."""
    for word in _words(rest, frozenset()):
        if not word or word.startswith("-"):
            continue
        match = NPM_NAME.fullmatch(word)
        if match is None or (match.group(2) is not None and NPM_RANGE.match(match.group(2))):
            return []
        return [match.group(1)]
    return []


def _unpinned_installs(code: str) -> list[str]:
    found = [f"pip {n}" for m in PIP.finditer(code) for n in _unpinned_pip(m.group(1))]
    found += [f"npm {n}" for m in NPM.finditer(code) for n in _unpinned_npm(m.group(1))]
    found += [f"npx {n}" for m in NPX.finditer(code) for n in _unpinned_npx(m.group(1))]
    return found


def _bundled_texts(skill_dir: Path) -> Iterator[tuple[str, str]]:
    """(path relative to the skill, text) of each bundled text file, sorted; SKILL.md is not one of them.

    Symlinks, hidden directories, ``node_modules``, another skill's directory, files over
    :data:`BUNDLED_MAX_BYTES` and binary files (a NUL byte in the first 8 KiB) are not read.
    """
    read = 0
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d != "node_modules" and not (here / d / "SKILL.md").is_file()
        )
        for name in sorted(filenames):
            path = here / name
            if here == skill_dir and name == "SKILL.md" or path.is_symlink():
                continue
            if read >= BUNDLED_MAX_FILES:
                continue  # keep walking: a stopped os.walk leaves its directory handles open
            try:
                if not path.is_file() or path.stat().st_size > BUNDLED_MAX_BYTES:
                    continue
                data = path.read_bytes()
                if b"\0" in data[:8192]:
                    continue
                text = data.decode("utf-8-sig")  # a byte order mark is not text
            except (OSError, UnicodeDecodeError):
                continue
            read += 1
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

    if unpinned:
        shown = ", ".join(unpinned[:EXAMPLES_SHOWN]) + (", ..." if len(unpinned) > EXAMPLES_SHOWN else "")
        s = "" if len(unpinned) == 1 else "s"
        findings.append((
            "install-unpinned",
            f"{len(unpinned)} install{s} name no version ({shown}); pin each as `pkg==1.2.3` (pip) "
            "or `pkg@1.2.3` (npm)",
        ))
    return findings

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Program checks for novelty probes, built from a tool's own ``--help``.

The judge grades an answer against the skill's claim, which is what a novelty probe
measures: does the model know what the skill says? What a judge reads past is a fluent
answer that names an option the tool does not have (``perf report --stdin``). This module
turns a pinned ``--help`` into an ``identifiers`` veto: an answer naming a ``--flag`` on a
``<tool>`` command line that the help does not list is wrong, whatever the judge says.

The check is written into ``atom.meta["grading"]``, so ``skill novelty probe`` carries it
onto the direct and paraphrase probes, and the record reports the judge against it. See
docs/grading-tiers.md.
"""

from __future__ import annotations

import hashlib
import re
import shlex

import regex

from dokimi.eval.reference_checks import (LONG_FLAG, REGEX_TIMEOUT, _negated_before, grade_check,
                                          identifier_names, scope_options)
from dokimi.process import Env, run
from dokimi.utils import InputError

#: A rejection names the unknown argument, and exit status cannot tell it from a listing
#: (some tools exit nonzero after printing real help). A line opening with an option is a listing.
_LISTING = re.compile(r"(?m)^\s*-")
#: The bracketed negation notation of a help (``--[no-]children``): both spellings are options.
_NEGATABLE = re.compile(r"--\[no-\]([A-Za-z][\w-]*)")
#: The rest of a command line after a tool's name: up to a shell comment, a sentence end, or the
#: head of another command (two words, then an option). Only options before those are the tool's.
_SCOPE = (r"(?=\s+-)(?:(?!\.(?:\s|$))(?!\s+\w[\w-]*\s+\w[\w-]*\s+-)[^\n`|;&#])*")
_REJECTION = re.compile(
    r"(?i)\b(?:unrecognized|unknown|invalid|not recognized|no such)\b[^\n]*\b(?:option|argument|flag)s?\b"
    r"|unrecognized arguments")


def _argv(tool: str) -> list[str]:
    """The words of ``tool``; refused when they do not parse or there are none."""
    try:
        argv = shlex.split(tool)
    except ValueError as exc:
        raise InputError(f"--tool {tool!r} does not parse: {exc}") from None
    if not argv:
        raise InputError("--tool is empty: name the command, for example 'perf report'")
    return argv


def _mention(tool: str) -> str:
    """A regex for the tool's name as the words of a command line, not inside another word."""
    return r"(?<![\w/-])" + r"\s+".join(re.escape(word) for word in _argv(tool))


def help_text(tool: str, *, timeout: float = 20.0) -> str:
    """``<tool> --help`` output (stdout and stderr), falling back to ``-h``.

    Raises:
        InputError: The tool is empty or does not parse, is not installed, or neither form
            prints any long option.
    """
    argv = _argv(tool)
    for flag in ("--help", "-h"):
        try:
            done = run([*argv, flag], timeout=timeout, env=Env.inherit())
        except FileNotFoundError:
            raise InputError(f"{argv[0]!r} is not installed; cannot read its help") from None
        if done.timed_out:
            continue
        text = done.stdout + done.stderr
        if help_identifiers(text) and (_LISTING.search(text) or not _REJECTION.search(text)):
            return text
    raise InputError(f"`{tool} --help` lists no long options")


def _declaration(line: str) -> str:
    """The part of an option line that declares options: up to the gap that starts its description.

    A gap after a comma joins the names ("-V,  --version"), and so does a gap after a lone short name
    that a long name follows ("-v  --verbose"). Any other gap starts the description, even when the
    description names another command's option ("--input FILE  --advanced belongs to another command").
    """
    for m in re.finditer(r"\s{2,}|\t", line):
        before, after = line[:m.start()], line[m.end():]
        if before.endswith(","):
            continue
        if re.search(r"(?:^|\s)-[^\s-]$", before) and after.startswith("--"):
            continue
        return before
    return line


def _declared(text: str) -> str:
    """The lines of a help that declare options: its usage block and its option lines.

    A usage block opens at ``usage:`` and runs over the indented lines after it. An option line
    opens with ``-``; the description after its gap (see ``_declaration``) may name another command's
    options (``run other --advanced``), so that part is dropped.
    """
    kept: list[str] = []
    in_usage = False
    for line in text.splitlines():
        if re.match(r"\s*usage:", line, re.IGNORECASE):
            in_usage = True
        elif not line.strip():
            in_usage = False
            continue
        elif not line[:1].isspace():
            in_usage = False
        if re.match(r"\s*-", line):
            kept.append(_declaration(line.strip()))
        elif in_usage:  # a usage line keeps its spacing: docker writes "Usage:  tool [--x]"
            kept.append(line.strip())
    return "\n".join(kept)


def help_identifiers(text: str) -> list[str]:
    """The long options a help text lists, sorted. ``--help`` only when the text lists it.

    Only the declared options count (see ``_declared``): a name a description mentions is no
    option of this tool. A bracketed ``--[no-]x`` lists both ``--x`` and ``--no-x``: the help
    establishes that the parser negates ``x``. A boolean that the help does not show that way
    gets no ``--no-x``.
    """
    declared = _declared(text)
    names = {m.group(1) for m in re.finditer(LONG_FLAG, declared)}
    for m in re.finditer(_NEGATABLE, declared):
        names |= {"--" + m.group(1), "--no-" + m.group(1)}
    return sorted(names)


def identifiers_check(tool: str, text: str, *, version: str | None = None) -> dict:
    """An ``identifiers`` veto for ``tool``'s command lines, from its help ``text``.

    ``within`` limits the check to the rest of a line where the tool's name is followed
    directly by an option, up to a shell comment (``#``), a sentence end, or the next command
    (two words and an option, as in ``perf record --call-graph``). A flag of another command,
    or of a subcommand (``perf`` then ``report``), is then not judged against this help: give
    each subcommand as its own tool. Two bare words before an option end the scope too, so an
    option after ``--sort comm dso`` is not checked: that errs toward passing.
    """
    head = _mention(tool)
    source = {"tool": tool, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    if version:
        source["version"] = version
    return {
        "type": "identifiers", "tier": "verifiable", "role": "veto",
        "within": head + _SCOPE,
        "known": help_identifiers(text),
        "source": source,
    }


def add_checks(atoms: list[dict], checks: list[dict], tools: list[str]) -> int:
    """Attach each tool's check to the atoms that name the tool; how many atoms changed.

    An atom that does not name the tool gets no check: its answers would rarely carry the
    tool's command line, and a check that never fires is noise in the report. A check for
    the same tool already on the atom is replaced, so re-running refreshes the snapshot.
    """
    changed = 0
    for atom in atoms:
        text = " ".join(str(atom.get(k, "")) for k in ("statement", "question", "answer"))
        mine = [c for c, tool in zip(checks, tools, strict=True)
                if re.search(_mention(tool), text)]
        if not mine:
            continue
        meta = atom.setdefault("meta", {})
        grading = meta.setdefault("grading", {"checks": []})
        kept = [c for c in grading.get("checks", [])
                if not (c.get("type") == "identifiers"
                        and (c.get("source") or {}).get("tool") in {m["source"]["tool"] for m in mine})]
        grading["checks"] = kept + mine
        changed += 1
    return changed


def _key_names(check: dict, key: str) -> list[str]:
    """The option names the key names on the lines of ``check``'s ``within``, as the check reads them.

    The same engine and bound as the check's own scan; a scan past the bound names none.
    """
    pattern = str(check.get("pattern") or LONG_FLAG)
    within = check.get("within")
    try:
        scopes = ([m.group(0) for m in regex.finditer(str(within), key, timeout=REGEX_TIMEOUT)
                   if not _negated_before(key, m.start())] if within else [key])
        names: list[str] = []
        for scope in scopes:
            names += scope_options(pattern, scope, timeout=REGEX_TIMEOUT, negated=bool(within))
    except TimeoutError:
        return []
    return names


def verify_keys(atoms: list[dict]) -> dict[str, int]:
    """Check each atom's answer key against its ``identifiers`` checks; counts per status.

    Writes ``meta["key_status"]``: ``verified`` (the key names an option on a checked command
    line, and every such option is in the help), ``not_found`` (with the ``missing`` names), or
    ``unverifiable`` (the key names no checked option, or one of its checks could not finish).
    This checks the skill's claim, not a model's answer. A ``not_found`` is a review flag, not an
    error: a negative claim ("there is no ``--device xpu``") or a version-bound one names an option
    the help rightly lacks.
    """
    counts = {"verified": 0, "not_found": 0, "unverifiable": 0}
    for atom in atoms:
        checks = [c for c in ((atom.get("meta") or {}).get("grading") or {}).get("checks", [])
                  if c.get("type") == "identifiers"]
        if not checks:
            continue
        key = " ".join(str(atom.get(k, "")) for k in ("answer", "statement"))
        missing, named, unfinished = [], False, False
        for check in checks:
            result = grade_check(check, "", key, None)
            if result.passed is None:
                unfinished = True  # a check that did not finish says nothing of the key
            elif result.passed is False:  # every unknown name, not the ten the detail shows
                missing += [n for n in identifier_names(check, key)[0] or [] if n not in missing]
            elif _key_names(check, key):
                named = True  # an option the key names on a checked line, and the help lists it
        status = ("not_found" if missing
                  else "verified" if named and not unfinished else "unverifiable")
        atom["meta"]["key_status"] = {"status": status, **({"missing": missing} if missing else {})}
        counts[status] += 1
    return counts

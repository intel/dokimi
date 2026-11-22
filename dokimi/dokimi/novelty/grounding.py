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

from dokimi.eval.reference_checks import (LONG_FLAG, REGEX_TIMEOUT, grade_check, identifier_names,
                                          scope_options, scope_pairs, validate_grading)
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


_WINDOWS_PATH = re.compile(r"\s*[\"']?[A-Za-z]:\\")


def _argv(tool: str) -> list[str]:
    """The words of ``tool``; refused when they do not parse or there are none."""
    try:
        if _WINDOWS_PATH.match(tool):  # backslashes are path separators here, not escapes
            lex = shlex.shlex(tool, posix=True)
            lex.whitespace_split, lex.commenters, lex.escape = True, "", ""
            argv = list(lex)
        else:
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
        except OSError as exc:
            raise InputError(f"{argv[0]!r} cannot be run: {exc.strerror or exc}") from None
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


def _require_valid_gradings(atoms: list[dict]) -> None:
    """Refuse a malformed ``meta["grading"]`` on any atom, as ``skill novelty probe`` does.

    Raises:
        InputError: A grading block does not validate (names the atom and the problems).
    """
    for atom in atoms:
        grading = (atom.get("meta") or {}).get("grading")
        problems = validate_grading(grading) if grading else []
        if problems:
            raise InputError(f"atom {atom.get('id')}: invalid grading: {'; '.join(problems)}")


def _mention_of(check: dict) -> str:
    """What an atom must name for ``check`` to attach: a tool's command line, or a symbol prefix."""
    source = check.get("source") or {}
    if source.get("kind") == "symbols":
        return str(check["within"])
    return _mention(source["tool"])


def _source_of(check: dict) -> tuple[str, str]:
    """The source a check replaces on re-run: ``("symbols", <its prefixes>)`` for a symbol check,
    else ``("tool", <its name>)``. The tuple keeps a tool named ``symbols`` apart from the symbol
    check, and a run with other prefixes from the one before it."""
    source = check.get("source") or {}
    if source.get("kind") == "symbols":
        return ("symbols", " ".join(source.get("prefixes") or []))
    return ("tool", str(source.get("tool")))


def _replaced_by(old: dict, mine: list[dict]) -> bool:
    """Whether a check ``old`` on an atom is replaced by one of ``mine``.

    A tool's check is replaced by the same tool's. A symbol check is replaced by a symbol check
    whose prefixes include all of its own: its scope lies inside the new one, so nothing is lost.
    A symbol check with a prefix the new one does not cover is kept, and :func:`_merged` carries the
    new names into it; a disjoint one is kept as it is.
    """
    if old.get("type") != "identifiers":
        return False
    if _source_of(old)[0] == "tool":
        return any(_source_of(m) == _source_of(old) for m in mine)
    old_prefixes = (old.get("source") or {}).get("prefixes") or []
    return bool(old_prefixes) and any(
        _source_of(m)[0] == "symbols" and all(
            any(a.startswith(b) for b in m["source"].get("prefixes") or []) for a in old_prefixes)
        for m in mine)


def _merged(old: dict, mine: list[dict]) -> dict | None:
    """``old`` with the names of ``mine``'s symbol checks merged in, as a new dict (``old`` is not changed).

    Only for a symbol check that overlaps one of ``mine``; None when no name is left. The names with
    the new prefixes are replaced by what the new checks read now, so a name no header declares any
    more drops out; the names with ``old``'s prefixes that the new checks read are added; the other
    names stay. The inputs of the new checks are added to ``old``'s; a path overwritten since keeps both
    snapshots: every snapshot is kept, so the list grows by one per overwrite of the path (no pruning).
    """
    symbol = [m for m in mine if _source_of(m)[0] == "symbols"]
    if old.get("type") != "identifiers" or _source_of(old)[0] != "symbols" or not symbol:
        return old
    old_prefixes = tuple((old.get("source") or {}).get("prefixes") or [])
    new_prefixes = tuple(p for m in symbol for p in m["source"].get("prefixes") or [])
    if not any(a.startswith(b) or b.startswith(a) for a in old_prefixes for b in new_prefixes):
        return old
    fresh = {n for m in symbol for n in m.get("known") or [] if n.startswith(old_prefixes)}
    kept = {n for n in old.get("known") or [] if not n.startswith(new_prefixes)}
    if not kept | fresh:  # a check with no name would veto every mention of its prefixes
        return None
    source = dict(old.get("source") or {})
    inputs = {(i["kind"], i["path"], i.get("sha256")): i for i in source.get("inputs") or []}
    for m in symbol:
        inputs.update({(i["kind"], i["path"], i.get("sha256")): i for i in m["source"].get("inputs") or []})
    source["inputs"] = list(inputs.values())
    return dict(old, known=sorted(kept | fresh), source=source,
                link_context=any(m.get("link_context") for m in symbol))


def _absorbed(new: dict, kept: list[dict]) -> bool:
    """Whether a kept symbol check already holds every name of ``new`` (a subset-prefix refresh merged it in)."""
    if _source_of(new)[0] != "symbols":
        return False
    new_prefixes = set(new["source"].get("prefixes") or [])
    return any(_source_of(k)[0] == "symbols" and set(k["source"].get("prefixes") or []) >= new_prefixes
               and set(new.get("known") or []) <= set(k.get("known") or []) for k in kept)


def add_checks(atoms: list[dict], checks: list[dict]) -> int:
    """Attach each check to the atoms that name its tool or prefix; how many atoms changed.

    An atom that names none gets no check: its answers would rarely carry the tool's command
    line, and a check that never fires is noise in the report. A check replaced by a new one
    (see :func:`_replaced_by`) is dropped, so re-running refreshes the snapshot; a kept one has
    the new names merged in (see :func:`_merged`).
    """
    _require_valid_gradings(atoms)
    changed = 0
    for atom in atoms:
        text = " ".join(str(atom.get(k, "")) for k in ("statement", "question", "answer"))
        mine = [c for c in checks if re.search(_mention_of(c), text)]
        if not mine:
            continue
        meta = atom["meta"] = atom.get("meta") or {}
        grading = meta["grading"] = meta.get("grading") or {"checks": []}
        kept = []
        for old in grading.get("checks", []):
            merged = None if _replaced_by(old, mine) else _merged(old, mine)
            if merged is not None:
                kept.append(merged)
        grading["checks"] = kept + [m for m in mine if not _absorbed(m, kept)]
        changed += 1
    return changed


def _key_names(check: dict, key: str) -> list[str]:
    """The option names the key names on the lines of ``check``'s ``within``, as the check reads them.

    The same engine and bound as the check's own scan; a scan past the bound names none.
    """
    pattern = str(check.get("pattern") or LONG_FLAG)
    within = check.get("within")
    try:
        scopes = scope_pairs(str(within), key, timeout=REGEX_TIMEOUT) if within else [(key, "")]
        names: list[str] = []
        for scope, rest in scopes:
            names += scope_options(pattern, scope, timeout=REGEX_TIMEOUT, negated=bool(within), rest=rest)
    except TimeoutError:
        return []
    return names


def verify_keys(atoms: list[dict]) -> dict[str, int]:
    """Check each atom's answer key against its ``identifiers`` checks; counts per status.

    Writes ``meta["key_status"]``: ``verified`` (the key names an option on a checked command
    line, and every such option is in the help), ``not_found`` (with the ``missing`` names), or
    ``unverifiable`` (the key names no checked option, names one only a link context exempts, or one
    of its checks could not finish).
    This checks the skill's claim, not a model's answer. A ``not_found`` is a review flag, not an
    error: a claim names an option the help lacks, as a version-bound one rightly does. A negative
    claim ("there is no ``--device xpu``") is ``unverifiable``: the clause negates the mention.
    """
    _require_valid_gradings(atoms)
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
                # a name only a link context let through is in no source: it confirms nothing
                if identifier_names(dict(check, link_context=False), key)[0] == []:
                    named = True  # an option the key names on a checked line, and the help lists it
                else:
                    unfinished = True
        status = ("not_found" if missing
                  else "verified" if named and not unfinished else "unverifiable")
        atom["meta"]["key_status"] = {"status": status, **({"missing": missing} if missing else {})}
        counts[status] += 1
    return counts

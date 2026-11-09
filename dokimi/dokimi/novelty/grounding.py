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

from dokimi.eval.reference_checks import LONG_FLAG, grade_check
from dokimi.process import Env, run
from dokimi.utils import InputError

#: Options every argument parser adds; never listed as an atom's claim.
_ALWAYS = ("--help",)


def help_text(tool: str, *, timeout: float = 20.0) -> str:
    """``<tool> --help`` output (stdout and stderr), falling back to ``-h``.

    Raises:
        InputError: The tool is not installed, or neither form prints any long option.
    """
    argv = shlex.split(tool)
    for flag in ("--help", "-h"):
        try:
            done = run([*argv, flag], timeout=timeout, env=Env.inherit())
        except FileNotFoundError:
            raise InputError(f"{argv[0]!r} is not installed; cannot read its help") from None
        if done.timed_out:
            continue
        text = done.stdout + done.stderr
        if re.search(LONG_FLAG, text):
            return text
    raise InputError(f"`{tool} --help` lists no long options")


def help_identifiers(text: str) -> list[str]:
    """The long options a help text lists, sorted, ``--help`` included."""
    return sorted({m.group(1) for m in re.finditer(LONG_FLAG, text)} | set(_ALWAYS))


def identifiers_check(tool: str, text: str, *, version: str | None = None) -> dict:
    """An ``identifiers`` veto for ``tool``'s command lines, from its help ``text``.

    ``within`` limits the check to the rest of a line where the tool's name is followed
    directly by an option. A flag of another command, or of a subcommand (``perf`` then
    ``report``), is then not judged against this help: give each subcommand as its own tool.
    """
    head = r"\s+".join(re.escape(word) for word in shlex.split(tool))
    source = {"tool": tool, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    if version:
        source["version"] = version
    return {
        "type": "identifiers", "tier": "verifiable", "role": "veto",
        "within": r"(?<![\w/-])" + head + r"(?=\s+-)[^\n`|;&]*",
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
                if re.search(c["within"], text)]
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


def verify_keys(atoms: list[dict]) -> dict[str, int]:
    """Check each atom's answer key against its ``identifiers`` checks; counts per status.

    Writes ``meta["key_status"]``: ``verified`` (every option the key names on a checked
    command line is in the help), ``not_found`` (with the ``missing`` names), or
    ``unverifiable`` (the key names none). This checks the skill's claim, not a model's
    answer. A ``not_found`` is a review flag, not an error: a negative claim ("there is no
    ``--device xpu``") or a version-bound one names an option the help rightly lacks.
    """
    counts = {"verified": 0, "not_found": 0, "unverifiable": 0}
    for atom in atoms:
        checks = [c for c in ((atom.get("meta") or {}).get("grading") or {}).get("checks", [])
                  if c.get("type") == "identifiers"]
        if not checks:
            continue
        key = " ".join(str(atom.get(k, "")) for k in ("answer", "statement"))
        missing, seen = [], False
        for check in checks:
            seen = seen or not check.get("within") or re.search(check["within"], key) is not None
            result = grade_check(check, "", key, None)
            if result.passed is False:
                missing += [n for n in result.detail.removeprefix("not in source: ").split(", ")
                            if n not in missing]
        status = "not_found" if missing else ("verified" if seen else "unverifiable")
        atom["meta"]["key_status"] = {"status": status, **({"missing": missing} if missing else {})}
        counts[status] += 1
    return counts

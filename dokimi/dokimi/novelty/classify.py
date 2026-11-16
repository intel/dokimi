# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Assign each atom a content category, and find the atoms that fight each other.

The categories are the product of two axes — *did the model already have it* (measured)
and *does the artifact's own task need it* (judged) — plus a conflict flag:

============= ============== =================
              needed         not needed
============= ============== =================
new to model  novel_useful   novel_inert
already known known_useful   known_inert
============= ============== =================

with ``scaffolding`` for text that is structure rather than knowledge, and
``harmful_conflicting`` for atoms that contradict another atom or the artifact itself.
That last one is the only category which can make an artifact worse than nothing, so it
is reported first regardless of size.

The known/new axis comes from the measurement, never from asking the model whether it
knew something. The needed/not-needed axis is judged. In the study both axes were judged,
and an independent reviewer agreed with 71.6% of assignments, with the disagreements
concentrated on useful-vs-inert (±3 points) and known-vs-novel (±2); here only the first
of those figures applies, since known/new is read off the paired probes instead.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .atoms import Atom
from .batching import UnusableReply, ask_in_batches, retry_wording
from .client import LLMClient

CATEGORIES = (
    "scaffolding",
    "novel_useful",
    "known_useful",
    "novel_inert",
    "known_inert",
    "harmful_conflicting",
)

_VALUE_PROMPT = """\
For each numbered statement, judge how it functions for someone doing this task:

TASK: {task}

Return ONLY a JSON array, one object per statement, same order:
[{{"n": 1, "needed": true, "scaffolding": false}}]

- "needed": acting on this task can go wrong, or be much slower, without knowing it.
- "scaffolding": it is structure, navigation, restated headings, or workflow framing
  rather than knowledge that could be right or wrong.

STATEMENTS:
{statements}
"""

_CONFLICT_PROMPT = """\
Find pairs of statements below that cannot both be true, or that give incompatible
instructions for the same situation. Ignore pairs that merely differ in detail.

Return ONLY JSON: {{"conflicts": [{{"a": 3, "b": 17, "why": "<one clause>"}}]}}

STATEMENTS:
{statements}
"""


@dataclass
class AtomVerdict:
    """The judged half of an atom's classification."""

    atom_id: str
    needed: bool
    scaffolding: bool
    conflict: bool = False
    conflict_why: str = ""

    def category(self, outcome: str | None) -> str:
        """Combine the judged axes with the measured outcome into one label.

        ``outcome`` is the paired probe result. When it is missing (an atom that was not
        probed, because probing is sampled) the atom is labelled on the judged axes
        alone and counted as ``novel_*``: the direction that does not silently claim the
        model already knew something nobody asked it.
        """
        if self.conflict:
            return "harmful_conflicting"
        if self.scaffolding:
            return "scaffolding"
        known = outcome == "redundant" or outcome == "degraded"
        if self.needed:
            return "known_useful" if known else "novel_useful"
        return "known_inert" if known else "novel_inert"

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "atom_id": self.atom_id,
            "needed": self.needed,
            "scaffolding": self.scaffolding,
            "conflict": self.conflict,
            "conflict_why": self.conflict_why,
        }


@dataclass
class ClassifyReport:
    """Which atoms the judged stages could not answer for, so none reads as a verdict.

    Attributes:
        unjudged: Atoms with no need/scaffolding verdict. They are left out of the
            category counts.
        unjudged_skipped: How many of those the judge's reply skipped (the call worked
            and had no row for them).
        unjudged_malformed: How many of those had a row in a reply that was otherwise read,
            but whose ``needed`` or ``scaffolding`` was not a boolean.
        unjudged_unusable: How many of those came from a reply with no usable row at all.
        unchecked_unusable: How many of ``unchecked`` came from a reply that named no usable
            pair.
        unchecked: Atoms the conflict check never compared with anything: in a call that
            failed, or alone in a chunk (a one-statement call has no pair to look at).
        unchecked_alone: How many of those were alone in a chunk.
        pairs_total: Atom pairs that could conflict.
        pairs_compared: Pairs that shared an answered conflict-check call. Pairs split
            across calls are never compared, so this bounds what ``conflicts`` can find.
        conflict_rows_dropped: Conflict rows in answered replies that named no pair of
            their batch (a missing ``a`` or ``b``, ``a == b``, or out of range).
        retried_batches: Calls whose reply failed to parse, or had no usable row, and were
            split and re-asked.
        unusable_retries: The subset of ``retried_batches`` whose reply parsed, with no
            usable row. Only the rest are cured by a higher ``--max-tokens``.
        calls: Calls both stages made, splits included.
        value_error: The last value-judge call failure that lost atoms (an unusable reply is
            counted apart).
        conflict_error: The last conflict-check failure that lost atoms. Kept apart from
            ``value_error``, and neither records an error a split recovered from, so a
            warning never quotes another stage's error or one that lost nothing.
    """

    unjudged: list[str] = field(default_factory=list)
    unjudged_skipped: int = 0
    unjudged_malformed: int = 0
    unjudged_unusable: int = 0
    unchecked: list[str] = field(default_factory=list)
    unchecked_unusable: int = 0
    unchecked_alone: int = 0
    pairs_total: int = 0
    pairs_compared: int = 0
    conflict_rows_dropped: int = 0
    retried_batches: int = 0
    unusable_retries: int = 0
    calls: int = 0
    value_error: str = ""
    conflict_error: str = ""

    def warnings(self, n_atoms: int) -> list[str]:
        """Artifact warnings for what was not judged or not compared."""
        out = []
        if self.unjudged:
            out.append(self._unjudged_warning(n_atoms))
        if self.unchecked:
            out.append(self._unchecked_warning(n_atoms))
        if self.conflict_rows_dropped:
            noun = "row" if self.conflict_rows_dropped == 1 else "rows"
            out.append(
                f"{self.conflict_rows_dropped} conflict {noun} could not be used (a and b must "
                f"name two different statements of their batch) and were dropped"
            )
        if self.pairs_total and self.pairs_compared < self.pairs_total:
            out.append(
                f"the conflict check compared {self.pairs_compared} of {self.pairs_total} "
                f"atom pairs, so a contradiction between atoms that did not share an "
                f"answered call is not detected"
            )
        if self.retried_batches:
            label, advice = retry_wording(self.retried_batches, self.unusable_retries)
            out.append(
                f"content classification re-asked {self.retried_batches} {label} batches as "
                f"halves ({self.calls} calls in all){advice}"
            )
        return out

    def _unjudged_warning(self, n_atoms: int) -> str:
        failed = (len(self.unjudged) - self.unjudged_skipped - self.unjudged_malformed
                  - self.unjudged_unusable)
        causes = []
        if failed:
            causes.append(f"{failed} in calls that failed (last error: {self.value_error})")
        if self.unjudged_skipped:
            causes.append(f"{self.unjudged_skipped} skipped by the judge's reply")
        if self.unjudged_malformed:
            causes.append(f"{self.unjudged_malformed} with a malformed verdict row")
        if self.unjudged_unusable:
            causes.append(f"{self.unjudged_unusable} whose reply had no usable verdict row")
        return (
            f"{len(self.unjudged)} of {n_atoms} atoms got no need/scaffolding verdict "
            f"and are left out of the content categories unless they conflict "
            f"({'; '.join(causes)})"
        )

    def _unchecked_warning(self, n_atoms: int) -> str:
        failed = len(self.unchecked) - self.unchecked_alone - self.unchecked_unusable
        causes = []
        if failed:
            causes.append(f"{failed} in calls that failed (last error: {self.conflict_error})")
        if self.unchecked_alone:
            causes.append(f"{self.unchecked_alone} alone in a batch, with no pair to check")
        if self.unchecked_unusable:
            causes.append(f"{self.unchecked_unusable} whose reply named no usable pair")
        return (
            f"{len(self.unchecked)} of {n_atoms} atoms were not checked for conflicts "
            f"({'; '.join(causes)})"
        )


def _index(value: Any) -> int | None:
    """A statement number a reply gave, as an int; ``None`` for a bool, a fraction or junk."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None  # inf and nan are not integers
    if isinstance(value, str) and value.strip().isdecimal():
        try:
            return int(value)
        except ValueError:  # more digits than int() accepts
            return None
    return value if isinstance(value, int) else None


def _statement_rows(rows: list) -> dict[int, dict]:
    """The dict rows of a judge reply, by their ``n`` (a later duplicate wins)."""
    by_index: dict[int, dict] = {}
    for row in rows:
        n = _index(row.get("n")) if isinstance(row, dict) else None
        if n is not None:
            by_index[n] = row
    return by_index


def _is_verdict(row: dict) -> bool:
    """``needed`` is a real boolean (``bool("false")`` is True) and ``scaffolding`` one or absent."""
    return (
        isinstance(row.get("needed"), bool)
        and isinstance(row.get("scaffolding", False), bool)
    )


def _judge_check(chunk: list[Atom], rows: list) -> None:
    """Refuse a reply with no verdict for any statement of this chunk, an empty list included."""
    by_index = _statement_rows(rows)
    if not any(1 <= n <= len(chunk) and _is_verdict(row) for n, row in by_index.items()):
        raise UnusableReply(f"none of the {len(rows)} rows is a verdict for this batch")


def judge_value(
    atoms: list[Atom], *, client: LLMClient, task: str, batch: int = 25,
    report: ClassifyReport | None = None,
) -> dict[str, AtomVerdict]:
    """Judge need and scaffolding per atom.

    ``task`` must come from outside the artifact body — the artifact's name and
    description, or a need graph built from them. Letting the body state the task is what
    allows an artifact to define its own relevance and score itself useful by
    construction.

    An atom the judge did not answer for gets no verdict, rather than ``needed=False``:
    a default would read as *inert* and bias the category table toward cutting. Its id
    goes to ``report.unjudged``, and counts as skipped (no row for it) or malformed (a row
    whose ``needed`` is not a real boolean, ``"false"`` included). A reply with no
    verdict row (all rows malformed, or an empty list) is split and re-asked, and never cached.
    """
    from dokimi.llm import extract_json_array

    report = report if report is not None else ClassifyReport()
    result = ask_in_batches(
        client, atoms, size=batch,
        prompt=lambda chunk: _VALUE_PROMPT.format(task=task, statements=_numbered(chunk)),
        parse=extract_json_array, check=_judge_check,
    )
    if result.stop:  # a run-wide provider error ends the classification as a cap stop does (#708)
        raise result.stop
    report.retried_batches += result.retried_batches
    report.unusable_retries += result.unusable_retries
    report.calls += result.calls
    if result.last_error:
        report.value_error = result.last_error
    report.unjudged.extend(a.id for a in result.failed)
    report.unjudged_unusable += len(result.unusable)
    verdicts: dict[str, AtomVerdict] = {}
    for chunk, rows in result.answered:
        by_index = _statement_rows(rows)
        for index, atom in enumerate(chunk, start=1):
            row = by_index.get(index)
            if row is None:
                report.unjudged.append(atom.id)
                report.unjudged_skipped += 1
            elif not _is_verdict(row):
                report.unjudged.append(atom.id)
                report.unjudged_malformed += 1
            else:
                verdicts[atom.id] = AtomVerdict(
                    atom_id=atom.id,
                    needed=row["needed"],
                    scaffolding=row.get("scaffolding", False),
                )
    return verdicts


def _conflict_rows(reply: str) -> list:
    """The ``conflicts`` list of a conflict-check reply; ``ValueError`` if there is none.

    A reply of ``[]``, ``null`` or an object without a ``conflicts`` list is not "no
    conflicts": it is a reply this stage cannot read, and must go down the split /
    unchecked path instead of counting as checked.
    """
    from .client import parse_json_object

    rows = parse_json_object(reply).get("conflicts")
    if not isinstance(rows, list):
        raise ValueError(f"no `conflicts` list in the reply: {reply[:120]!r}")
    return rows


def _conflict_pair(row: Any, size: int) -> tuple[int, int] | None:
    """The 0-based statement indices a conflict row names, or ``None`` if it names no pair."""
    if not isinstance(row, dict):
        return None
    a, b = _index(row.get("a")), _index(row.get("b"))
    if a is None or b is None:
        return None
    a_index, b_index = a - 1, b - 1
    # A statement cannot contradict itself; a == b would flag one atom harmful.
    if a_index == b_index or not (0 <= a_index < size and 0 <= b_index < size):
        return None
    return a_index, b_index


def _conflict_check(chunk: list[Atom], rows: list) -> None:
    """Refuse a reply with rows, none of which names a pair of statements in this chunk."""
    if rows and not any(_conflict_pair(row, len(chunk)) for row in rows):
        raise UnusableReply(f"none of the {len(rows)} conflict rows names a pair in this batch")


def detect_conflicts(
    atoms: list[Atom], *, client: LLMClient, batch: int = 40,
    report: ClassifyReport | None = None,
) -> list[dict]:
    """Find mutually incompatible atoms.

    Scoped to *internal* consistency: a conflict says "these two disagree", not "this one
    is wrong". Deciding which side is right needs a primary source, which this layer
    deliberately does not claim to consult. Only atoms that share a call are compared;
    ``report.pairs_compared`` says how much of the pair space that covered. A chunk is
    never split below two atoms, since one statement has no pair to check; an atom left
    alone is reported unchecked.
    """
    report = report if report is not None else ClassifyReport()
    result = ask_in_batches(
        client, atoms, size=batch,
        prompt=lambda chunk: _CONFLICT_PROMPT.format(statements=_numbered(chunk)),
        parse=_conflict_rows, min_size=2, check=_conflict_check,
    )
    if result.stop:
        raise result.stop
    report.retried_batches += result.retried_batches
    report.unusable_retries += result.unusable_retries
    report.calls += result.calls
    if result.last_error:
        report.conflict_error = result.last_error
    report.unchecked.extend(a.id for a in result.failed)
    report.unchecked_unusable += len(result.unusable)
    if len(atoms) > 1:
        # One atom in all has nothing to conflict with; that is not a gap in the check.
        report.unchecked.extend(a.id for a in result.too_small)
        report.unchecked_alone += len(result.too_small)
    report.pairs_total = len(atoms) * (len(atoms) - 1) // 2
    report.pairs_compared = sum(len(c) * (len(c) - 1) // 2 for c, _ in result.answered)
    conflicts: list[dict] = []
    for chunk, rows in result.answered:
        for row in rows:
            pair = _conflict_pair(row, len(chunk))
            if pair is None:
                report.conflict_rows_dropped += 1
                continue
            conflicts.append({
                "a": chunk[pair[0]].id,
                "b": chunk[pair[1]].id,
                "why": str(row.get("why", "")).strip(),
            })
    return conflicts


def _numbered(atoms: list[Atom]) -> str:
    return "\n".join(f"{i + 1}. {a.statement}" for i, a in enumerate(atoms))


def apply_conflicts(verdicts: dict[str, AtomVerdict], conflicts: list[dict]) -> None:
    """Mark both sides of every conflict (in place).

    A conflicting atom the value judge did not answer for still gets a verdict: the
    conflict alone decides its category, and it is the one category that must not drop.
    """
    for conflict in conflicts:
        for side in ("a", "b"):
            atom_id = conflict.get(side, "")
            if not atom_id:
                continue
            verdict = verdicts.setdefault(
                atom_id, AtomVerdict(atom_id=atom_id, needed=False, scaffolding=False)
            )
            verdict.conflict = True
            verdict.conflict_why = conflict.get("why", "")


def categorize(
    atoms: list[Atom],
    verdicts: dict[str, AtomVerdict],
    outcomes: dict[str, str],
) -> dict[str, str]:
    """Category per atom id."""
    return {
        atom.id: verdicts[atom.id].category(outcomes.get(atom.id))
        for atom in atoms
        if atom.id in verdicts
    }


def category_counts(categories: dict[str, str]) -> dict[str, int]:
    """Counts per category, all six keys always present so a zero is visible."""
    counted = Counter(categories.values())
    return {name: int(counted.get(name, 0)) for name in CATEGORIES}

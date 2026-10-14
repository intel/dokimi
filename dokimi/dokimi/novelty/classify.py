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

from .atoms import Atom
from .batching import ask_in_batches
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
            and had no row for them); the rest were in calls that failed even alone.
        unchecked: Atoms the conflict check never compared with anything: in a call that
            failed, or alone in a chunk (a one-statement call has no pair to look at).
        unchecked_alone: How many of those were alone in a chunk.
        pairs_total: Atom pairs that could conflict.
        pairs_compared: Pairs that shared an answered conflict-check call. Pairs split
            across calls are never compared, so this bounds what ``conflicts`` can find.
        retried_batches: Calls that failed to parse and were split and re-asked.
        calls: Calls both stages made, splits included.
        value_error: The last value-judge failure that lost atoms.
        conflict_error: The last conflict-check failure that lost atoms. Kept apart from
            ``value_error``, and neither records an error a split recovered from, so a
            warning never quotes another stage's error or one that lost nothing.
    """

    unjudged: list[str] = field(default_factory=list)
    unjudged_skipped: int = 0
    unchecked: list[str] = field(default_factory=list)
    unchecked_alone: int = 0
    pairs_total: int = 0
    pairs_compared: int = 0
    retried_batches: int = 0
    calls: int = 0
    value_error: str = ""
    conflict_error: str = ""

    def warnings(self, n_atoms: int) -> list[str]:
        """Artifact warnings for what was not judged or not compared."""
        out = []
        if self.unjudged:
            failed = len(self.unjudged) - self.unjudged_skipped
            causes = []
            if failed:
                causes.append(f"{failed} in calls that failed (last error: {self.value_error})")
            if self.unjudged_skipped:
                causes.append(f"{self.unjudged_skipped} skipped by the judge's reply")
            out.append(
                f"{len(self.unjudged)} of {n_atoms} atoms got no need/scaffolding verdict "
                f"and are left out of the content categories unless they conflict "
                f"({'; '.join(causes)})"
            )
        if self.unchecked:
            failed = len(self.unchecked) - self.unchecked_alone
            causes = []
            if failed:
                causes.append(
                    f"{failed} in calls that failed (last error: {self.conflict_error})"
                )
            if self.unchecked_alone:
                causes.append(f"{self.unchecked_alone} alone in a batch, with no pair to check")
            out.append(
                f"{len(self.unchecked)} of {n_atoms} atoms were not checked for conflicts "
                f"({'; '.join(causes)})"
            )
        if self.pairs_total and self.pairs_compared < self.pairs_total:
            out.append(
                f"conflicts were looked for within batches: {self.pairs_compared} of "
                f"{self.pairs_total} atom pairs shared a call, so a contradiction between "
                f"atoms in different batches is not detected"
            )
        if self.retried_batches:
            out.append(
                f"content classification re-asked {self.retried_batches} unparseable "
                f"batches as halves ({self.calls} calls in all); a higher --max-tokens "
                f"avoids the extra calls"
            )
        return out


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
    goes to ``report.unjudged``. So does an atom whose row has no boolean ``needed`` (or
    a non-boolean ``scaffolding``): ``"false"`` is not ``False``.
    """
    from dokimi.llm import extract_json_array

    report = report if report is not None else ClassifyReport()
    result = ask_in_batches(
        client, atoms, size=batch,
        prompt=lambda chunk: _VALUE_PROMPT.format(task=task, statements=_numbered(chunk)),
        parse=extract_json_array,
    )
    report.retried_batches += result.retried_batches
    report.calls += result.calls
    if result.last_error:
        report.value_error = result.last_error
    report.unjudged.extend(a.id for a in result.failed)
    verdicts: dict[str, AtomVerdict] = {}
    for chunk, rows in result.answered:
        by_index = {
            int(row["n"]): row for row in rows
            if isinstance(row, dict) and str(row.get("n", "")).isdecimal()
        }
        for index, atom in enumerate(chunk, start=1):
            row = by_index.get(index)
            # A row is a verdict only if `needed` is a real boolean (`bool("false")` is
            # True, and a missing or null value would read as inert) and `scaffolding`
            # is a boolean or absent.
            if (
                row is None
                or not isinstance(row.get("needed"), bool)
                or not isinstance(row.get("scaffolding", False), bool)
            ):
                report.unjudged.append(atom.id)
                report.unjudged_skipped += 1
                continue
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
        parse=_conflict_rows, min_size=2,
    )
    report.retried_batches += result.retried_batches
    report.calls += result.calls
    if result.last_error:
        report.conflict_error = result.last_error
    report.unchecked.extend(a.id for a in result.failed)
    if len(atoms) > 1:
        # One atom in all has nothing to conflict with; that is not a gap in the check.
        report.unchecked.extend(a.id for a in result.too_small)
        report.unchecked_alone += len(result.too_small)
    report.pairs_total = len(atoms) * (len(atoms) - 1) // 2
    report.pairs_compared = sum(len(c) * (len(c) - 1) // 2 for c, _ in result.answered)
    conflicts: list[dict] = []
    for chunk, rows in result.answered:
        for row in rows:
            try:
                a_index, b_index = int(row["a"]) - 1, int(row["b"]) - 1
            except (KeyError, TypeError, ValueError):
                continue
            # A statement cannot contradict itself; a == b would flag one atom harmful.
            if a_index == b_index or not (
                0 <= a_index < len(chunk) and 0 <= b_index < len(chunk)
            ):
                continue
            conflicts.append({
                "a": chunk[a_index].id,
                "b": chunk[b_index].id,
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

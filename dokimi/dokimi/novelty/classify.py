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
knew something. The needed/not-needed axis is judged, and it is the axis that moved: an
independent reviewer agreed with 71.6% of assignments in the study, with the
disagreements concentrated on useful-vs-inert (±3 points) and known-vs-novel (±2).
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
        unjudged: Atoms with no need/scaffolding verdict (the call failed even alone,
            or the reply skipped them). They are left out of the category counts.
        unchecked: Atoms the conflict check never saw, for the same reason.
        pairs_total: Atom pairs that could conflict.
        pairs_compared: Pairs that shared a conflict-check call. Pairs split across
            calls are never compared, so this bounds what ``conflicts`` can find.
        retried_batches: Calls that failed to parse and were split and re-asked.
        last_error: The last failure, for the warning text.
    """

    unjudged: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    pairs_total: int = 0
    pairs_compared: int = 0
    retried_batches: int = 0
    last_error: str = ""

    def warnings(self, n_atoms: int) -> list[str]:
        """Artifact warnings for what was not judged or not compared."""
        out = []
        if self.unjudged:
            out.append(
                f"{len(self.unjudged)} of {n_atoms} atoms got no need/scaffolding verdict "
                f"and are left out of the content categories unless they conflict "
                f"(last error: {self.last_error})"
            )
        if self.unchecked:
            out.append(
                f"{len(self.unchecked)} of {n_atoms} atoms were not checked for conflicts "
                f"(last error: {self.last_error})"
            )
        if self.pairs_total and self.pairs_compared < self.pairs_total:
            out.append(
                f"conflicts were looked for within batches: {self.pairs_compared} of "
                f"{self.pairs_total} atom pairs shared a call, so a contradiction between "
                f"atoms in different batches is not detected"
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
    goes to ``report.unjudged``.
    """
    from dokimi.llm import extract_json_array

    report = report if report is not None else ClassifyReport()
    result = ask_in_batches(
        client, atoms, size=batch,
        prompt=lambda chunk: _VALUE_PROMPT.format(task=task, statements=_numbered(chunk)),
        parse=extract_json_array,
    )
    report.retried_batches += result.retried_batches
    if result.last_error:
        report.last_error = result.last_error
    report.unjudged.extend(a.id for a in result.failed)
    verdicts: dict[str, AtomVerdict] = {}
    for chunk, rows in result.answered:
        by_index = {
            int(row["n"]): row for row in rows
            if isinstance(row, dict) and str(row.get("n", "")).isdigit()
        }
        for index, atom in enumerate(chunk, start=1):
            row = by_index.get(index)
            if row is None:
                report.unjudged.append(atom.id)
                continue
            verdicts[atom.id] = AtomVerdict(
                atom_id=atom.id,
                needed=bool(row.get("needed", False)),
                scaffolding=bool(row.get("scaffolding", False)),
            )
    return verdicts


def detect_conflicts(
    atoms: list[Atom], *, client: LLMClient, batch: int = 40,
    report: ClassifyReport | None = None,
) -> list[dict]:
    """Find mutually incompatible atoms.

    Scoped to *internal* consistency: a conflict says "these two disagree", not "this one
    is wrong". Deciding which side is right needs a primary source, which this layer
    deliberately does not claim to consult. Only atoms that share a call are compared;
    ``report.pairs_compared`` says how much of the pair space that covered.
    """
    from dokimi.llm import extract_json_object

    report = report if report is not None else ClassifyReport()
    result = ask_in_batches(
        client, atoms, size=batch,
        prompt=lambda chunk: _CONFLICT_PROMPT.format(statements=_numbered(chunk)),
        parse=extract_json_object,
    )
    report.retried_batches += result.retried_batches
    if result.last_error:
        report.last_error = result.last_error
    report.unchecked.extend(a.id for a in result.failed)
    report.pairs_total = len(atoms) * (len(atoms) - 1) // 2
    report.pairs_compared = sum(len(c) * (len(c) - 1) // 2 for c, _ in result.answered)
    conflicts: list[dict] = []
    for chunk, rows in result.answered:
        for row in rows.get("conflicts", []) or []:
            try:
                a_index, b_index = int(row["a"]) - 1, int(row["b"]) - 1
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= a_index < len(chunk) and 0 <= b_index < len(chunk)):
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

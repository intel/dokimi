# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How much of an artifact one novelty run read: the bundle record's coverage figures.

:mod:`dokimi.novelty.bundle` writes the bundle record; the gate, the report, the CLI note
and the analyzer's warnings read it. This module is the reading side, and it sits below
all of them so the gate (``components``) can read a record without importing the novelty
track (``tracks``):

* :func:`exclusion_class` is the one rule for which ``excluded`` rows count as what;
* :class:`Coverage` (``Coverage.from_record``) is the one place the coverage figures are
  computed, for a live bundle and for a stored record of any age alike.

:mod:`dokimi.novelty.bundle` re-exports every name here, so its import paths still work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Every ``excluded`` row's ``kind`` belongs to exactly one class, and every reader decides
#: what a row means by its class -- never by its own reading of ``kind`` or ``reason``:
#:
#: * ``gap`` -- text the run could have measured and did not (over a word budget, or
#:   resources not requested). Its words are ``unmeasured_words``; it is what a warning
#:   about excluded files is about.
#: * ``decision`` -- a role decision (a skill's own ``evals/`` by default, or a
#:   ``--resource-roles`` filter). Counted on disk, never charged as a gap, and outside
#:   the ``readable`` coverage denominator: no rerun with ``--include-resources`` would
#:   read it.
#: * ``unknown`` -- text that did not decode. Its size is unknown, so the gate reports
#:   no unmeasured share while one exists; the other surfaces quote a figure that leaves
#:   its words out.
#: * ``not_content`` -- a non-text file, or a symlink (recorded, never followed). Not the
#:   skill's own text in any mode, so it carries no words and is never a reason to warn.
EXCLUSION_CLASS: dict[str, str] = {
    "budget": "gap",
    "not_requested": "gap",
    "policy": "decision",
    "unreadable": "unknown",
    "symlink": "not_content",
    "not_text": "not_content",
}
EXCLUSION_CLASSES = ("gap", "decision", "unknown", "not_content")


def exclusion_kind(row: dict) -> str:
    """The row's ``kind``, recovered from its ``reason`` for records written before kinds.

    An unrecognised row is a ``budget`` row: content the run did not read is the
    conservative reading, because it is charged against coverage rather than hidden.
    """
    kind = row.get("kind")
    if isinstance(kind, str) and kind in EXCLUSION_CLASS:
        return str(kind)
    reason = str(row.get("reason") or "")
    if reason == "not text":
        return "not_text"
    if reason in ("symlink", "unreadable"):
        return reason
    if reason == "resources not requested":
        return "not_requested"
    if reason.startswith("role "):
        return "policy"
    return "budget"


def exclusion_class(row: dict) -> str:
    """Which of :data:`EXCLUSION_CLASSES` an ``excluded`` row belongs to."""
    return EXCLUSION_CLASS[exclusion_kind(row)]


def excluded_of(rows: list[dict] | None, cls: str) -> list[dict]:
    """The ``excluded`` rows of one class, in record order."""
    if cls not in EXCLUSION_CLASSES:
        raise ValueError(f"unknown exclusion class {cls!r}; known: {EXCLUSION_CLASSES}")
    return [row for row in rows or [] if exclusion_class(row) == cls]


def named_exclusions(rows: list[dict] | None) -> tuple[list[dict], int]:
    """The ``excluded`` rows the report names, and the rest's count.

    Every row is named, in record order, except a non-text file, which is only counted:
    39 OOXML schemas in one skill would otherwise bury the two references that matter.
    Gaps, role decisions, undecodable files and symlinks are named alike. The report has
    always grouped them this way, and so does the gate's message for a record without
    ``coverage_basis``; on the ``readable`` basis the gate counts gaps and role decisions
    separately instead.
    """
    rows = rows or []
    named = [row for row in rows if exclusion_kind(row) != "not_text"]
    return named, len(rows) - len(named)


#: The record fields the coverage arithmetic reads. A record missing any of them (a
#: partial block, or a run that predates bundles) has no coverage figure at all: a
#: partial block once produced "0% was not read" from a record that said 0.99.
COVERAGE_FIELDS = ("entry_point_words", "resource_words_on_disk", "resource_words_measured")

#: What a coverage share is a share *of* (:meth:`Coverage.denominator`):
#:
#: * ``readable`` -- the entry point plus the bundled words some run could read, i.e. with
#:   the role decisions (a skill's own ``evals/``) left out. The analyzer's warnings, the
#:   CLI note and the report quote shares on this basis (``measurable_outside_share``).
#: * ``all_bundled_words`` -- the entry point plus every bundled word on disk, role
#:   decisions included. ``outside_share`` describes the tree on this basis, and it is
#:   the basis the gate's ``artifact_coverage`` judged before records were stamped.
#:
#: The two disagree whenever a skill has role decisions: an entry-point run of 400 entry
#: words, a 500-word reference and 1,000 words of evals is 56% unmeasured on the readable
#: basis and 26% on all bundled words. The gate judges the basis the record is stamped
#: with (``coverage_basis``), so a stored artifact keeps the value it was gated at.
COVERAGE_BASES = ("readable", "all_bundled_words")
#: The basis every record is now written with: the gate judges the figure the analyzer
#: warns about.
CURRENT_COVERAGE_BASIS = "readable"
#: How a record without ``coverage_basis`` is read: it was written when the gate judged
#: all bundled words, and re-reading it on another basis would move its stored value.
LEGACY_COVERAGE_BASIS = "all_bundled_words"
#: A run that read this share of the readable words or less is warned, on stdout and in the
#: artifact: its rates describe the measured part, not the artifact. This is the line the
#: analyzer has always warned at (an entry-point run whose outside share is 0.5 or more), and
#: the gate's ``max_unmeasured_outside_share`` is 0.50 on the same readable basis.
MEASURED_SHARE_WARN_AT = 0.5


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0



def _safe_int(value: Any) -> int:
    """Coerce a value to int, returning 0 if it is not a numeric type."""
    if isinstance(value, bool):
        # bool is a subclass of int, but a bool is not a count
        return 0
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, TypeError, OverflowError):
            return 0
    return 0


def _stored_count(value: Any) -> int | None:
    """A stored count as an int, or ``None`` when absent or not a usable count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


def _stored_basis(value: Any) -> str:
    """A record's ``coverage_basis``: :data:`LEGACY_COVERAGE_BASIS` only when it is absent.

    Any other value is kept as written, so a stamp this version does not know -- an empty
    string, a number -- is reported as unknown rather than read on the legacy basis.
    """
    return value if isinstance(value, str) else repr(value)


@dataclass(frozen=True)
class Coverage:
    """How much of an artifact one run read, from a live bundle or a stored record.

    The one place these figures are computed, so the CLI note, the analyzer's warnings,
    the gate and the report derive them the same way; where two surfaces quote different
    figures, the difference is a named basis (:data:`COVERAGE_BASES`), not two formulas.
    """

    include_resources: bool
    n_files: int
    entry_point_words: int
    resource_words_on_disk: int
    resource_words_measured: int
    #: Words in ``decision`` rows: on disk, not a gap, outside the ``readable`` basis.
    policy_excluded_words: int
    unreadable_files: int
    #: How many ``excluded`` rows the gate names on the legacy basis (:func:`named_exclusions`).
    named_excluded_files: int
    gap_files: int
    decision_files: int
    #: The record's ``coverage_basis``; :data:`LEGACY_COVERAGE_BASIS` when it has none.
    basis: str = LEGACY_COVERAGE_BASIS

    @classmethod
    def from_record(cls, record: dict | None) -> "Coverage | None":
        """Read a bundle record of any age; ``None`` when it lacks the counts."""
        record = record or {}
        if not all(key in record for key in COVERAGE_FIELDS):
            return None
        rows = record.get("excluded") or []
        decisions = excluded_of(rows, "decision")
        policy = _stored_count(record.get("policy_excluded_words"))
        unreadable = _stored_count(record.get("unreadable_files"))
        return cls(
            include_resources=bool(record.get("include_resources")),
            n_files=int(record.get("n_files") or len(record.get("files") or []) or 1),
            entry_point_words=int(record["entry_point_words"]),
            resource_words_on_disk=int(record["resource_words_on_disk"]),
            resource_words_measured=int(record["resource_words_measured"]),
            policy_excluded_words=(
                policy if policy is not None
                else sum(_safe_int(row.get("words")) for row in decisions)),
            unreadable_files=(
                unreadable if unreadable is not None else len(excluded_of(rows, "unknown"))),
            named_excluded_files=len(named_exclusions(rows)[0]),
            gap_files=len(excluded_of(rows, "gap")),
            decision_files=len(decisions),
            basis=_stored_basis(record.get("coverage_basis", LEGACY_COVERAGE_BASIS)),
        )

    @property
    def measurable_resource_words(self) -> int:
        """Bundled words some run could read: on disk minus the role decisions."""
        return max(0, self.resource_words_on_disk - self.policy_excluded_words)

    @property
    def unmeasured_words(self) -> int:
        """Bundled words in ``gap`` rows: what a run with larger budgets would add."""
        return max(0, self.measurable_resource_words - self.resource_words_measured)

    def denominator(self, basis: str) -> int:
        """The words a share on ``basis`` is a share of (:data:`COVERAGE_BASES`)."""
        if basis == "readable":
            return self.entry_point_words + self.measurable_resource_words
        if basis == "all_bundled_words":
            return self.entry_point_words + self.resource_words_on_disk
        raise ValueError(f"unknown coverage basis {basis!r}; known: {COVERAGE_BASES}")

    def outside_share_on(self, basis: str) -> float:
        """Share of the ``basis`` words that are not in the entry point, to 4 places."""
        outside = (self.measurable_resource_words if basis == "readable"
                   else self.resource_words_on_disk)
        return _share(outside, self.denominator(basis))

    @property
    def outside_share(self) -> float:
        """Share of the on-disk words that are not in the entry point (describes the tree)."""
        return self.outside_share_on("all_bundled_words")

    @property
    def measurable_outside_share(self) -> float:
        """Share of the readable words that are not in the entry point.

        `outside_share` describes the tree; this describes the measurement, so it is the
        figure to quote when telling a user what `--include-resources` would add. For a
        skill whose only resources are its own evals the two are 0.9 and 0.0.
        """
        return self.outside_share_on("readable")

    @property
    def counted_unmeasured_share(self) -> float:
        """Share of the counted readable words this run did not read, to 4 places.

        Known even when a file did not decode, and then a floor: that file's words are
        unread too. For an entry-point run it equals ``outside_share_on("readable")``.
        """
        return _share(self.unmeasured_words, self.denominator("readable"))

    def unmeasured_share(self, basis: str) -> float | None:
        """Share of the ``basis`` words this run did not read; ``None`` when unknowable.

        Not rounded: the gate compares it with its ceiling first and rounds only the value it
        reports, so a share of 0.50004 is over a 0.5 ceiling. ``None`` while any file is
        ``unknown``: its words are not in the total, so a share computed without them would
        be a lower bound reported as a measurement.
        """
        if self.unreadable_files:
            return None
        whole = self.denominator(basis)
        return self.unmeasured_words / whole if whole else 0.0

    @property
    def measured_share(self) -> float | None:
        """Share of the readable words this run read, to 4 places; ``None`` when unknowable.

        The complement of :meth:`unmeasured_share` on the readable basis, stated as what was
        read rather than what was missed. ``None`` for the same reasons: a file did not
        decode, or there are no readable words to be a share of.
        """
        unmeasured = self.unmeasured_share("readable")
        if unmeasured is None or not self.denominator("readable"):
            return None
        return round(1 - unmeasured, 4)

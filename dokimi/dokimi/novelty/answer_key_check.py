# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Is a saved answer key still valid for the artifact as it is now?

An answer key is the atoms extracted under one bundle selection. Probing it against a
changed artifact would score every moved atom as a result, so :func:`check_answer_key`
replays the recorded selection and refuses (with a verdict, not an exit) when the bytes,
the file set or the artifact differ. The bundle itself is loaded by
:mod:`dokimi.novelty.bundle`; this module only compares.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dokimi.metrics.bundle_coverage import exclusion_class

from .answer_key import ABSENT, AnswerKeyRecord, RecordedFile, parse_answer_key
from .artifact import load_artifact_text
from .bundle import Bundle, load_bundle

#: Why a saved answer key is refused: the artifact is unloadable, the record has no usable
#: files, it names another artifact, or its bundled files changed (see ``check_answer_key``).
ANSWER_KEY_PROBLEMS = ("unloadable", "no_file_rows", "wrong_artifact", "files_changed")


@dataclass(frozen=True)
class AnswerKeyVerdict:
    """Whether a saved answer key may be probed against an artifact as it is now.

    ``problem`` is ``None`` for a valid key; ``text`` and ``bundle`` are then the context to
    inject (``bundle`` is ``None`` for a record that predates bundle provenance, which is
    probed against the entry point alone). ``notes`` are the things the caller must say
    even when the key is valid: a legacy assumption, an edited entry point.
    """

    problem: str | None = None
    message: str = ""
    text: str = ""
    bundle: Bundle | None = None
    notes: tuple[str, ...] = ()
    #: The file sets that were compared (``files_changed`` explains itself with these).
    recorded: tuple[str, ...] = ()
    rebuilt: tuple[str, ...] = ()
    edited: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    entry_point_changed: bool = False

    @property
    def ok(self) -> bool:
        """True when the key is valid for the artifact as it is now."""
        return self.problem is None


def _replay_selection(record: AnswerKeyRecord) -> dict[str, Any]:
    """The ``load_bundle`` arguments that reproduce a recorded selection."""
    rows = record.files
    # The roles asked for, not the roles of the selected files: a role whose files were all
    # over budget must still count as asked, or the rebuild would lose the gap.
    requested = record.resource_roles
    if requested is None:       # a record from before the roles were recorded
        requested = sorted({row.role for row in rows[1:]}) or None
    # Older records lack the budgets: the same greedy pick is reproduced by capping at the
    # largest selected file and at the selected total.
    budgets: dict[str, int] = {}
    if record.max_file_words and record.max_total_words:
        budgets = {"max_file_words": int(record.max_file_words),
                   "max_total_words": int(record.max_total_words)}
    elif record.include_resources and all(row.words is not ABSENT for row in rows[1:]):
        selected = [int(row.words) for row in rows[1:]]
        budgets = {"max_file_words": max(selected, default=0),
                   "max_total_words": sum(selected)}
    include = record.include_resources
    return {"include_resources": include,
            "roles": tuple(requested) if requested is not None and include else None,
            **budgets}


def _changes_answer_key(row: dict, asked_roles: list | None) -> bool:
    """Whether a file that appeared since extraction invalidates the answer key.

    ``not_content`` never does -- a new image or symlink is not text any mode would read.
    A ``decision`` row does only when the record does not say which roles were asked for:
    then the rebuild was narrowed to the selected roles, and a file in a default role that
    simply had no file at extraction time (a first ``scripts/run.sh``) would be read by a
    re-extraction. Every other new row is text a re-extraction would measure.
    """
    cls = exclusion_class(row)
    if cls == "not_content":
        return False
    if cls == "decision":
        return asked_roles is None
    return True


def _edited_since(rows: Sequence[RecordedFile], now: Bundle) -> list[str]:
    """Bundled files whose bytes changed since extraction (the entry point is exempt).

    Same path, different bytes is still a stale key, and a word count cannot see it (`64`
    to `128` keeps the count). The digest does; the word count is the fallback for a record
    written before digests.
    """
    return [
        row.path
        for row, item in zip(rows[1:], now.files[1:], strict=False)
        if (row.sha256 and item.sha256 and row.sha256 != item.sha256)
        or (not row.sha256 and row.words is not ABSENT and row.words != item.words)
    ]


def _added_since(key: AnswerKeyRecord, now: Bundle, recorded: list, rebuilt: list) -> list[str]:
    """Files that appeared since extraction, in any role the walk saw.

    The rebuild is filtered to the roles the extraction measured, so a file added since in a
    role that had no file then would be filtered out of `rebuilt` and pass unnoticed. Compare
    every path the walk saw against every path the extraction saw.
    """
    if not (key.include_resources and key.excluded is not None):
        return []
    seen = set(recorded) | set(key.excluded)
    asked = key.resource_roles
    return sorted(
        {row["path"] for row in now.excluded
         if row["path"] not in seen and _changes_answer_key(row, asked)}
        | (set(rebuilt) - set(recorded)))


def check_answer_key(ref: str, record: Mapping[str, Any] | AnswerKeyRecord | None, *,
                     source: str = "atoms.json") -> AnswerKeyVerdict:
    """Is the answer key extracted under ``record`` still valid for ``ref`` as it is now?

    The bundle mode is taken from the record, not from flags: if atoms came from a bundle
    and the probe injected only ``SKILL.md``, every atom extracted from a reference file
    would score ``still_missing`` because the context never contained it -- a measurement
    error that looks like a result. So the selection is replayed and any difference in the
    *bundled* files is refused rather than reconciled. The entry point alone may change:
    re-measuring a shortened ``SKILL.md`` against the original atoms is the authoring
    path's last step, so that is a note, not a refusal.

    Args:
        ref: The artifact reference to probe (``skill:<path>``).
        record: The ``bundle`` block of the saved atoms, or its parsed form (empty or
            ``None`` for a file written before bundle provenance).
        source: How to name the record in messages (the atoms file's path).

    Returns:
        A verdict; never raises for a stale or foreign key. ``problem`` is one of
        :data:`ANSWER_KEY_PROBLEMS`.
    """
    key = parse_answer_key(record)
    if not key.present:
        try:
            body = load_artifact_text(ref)[4]
        except (OSError, ValueError, RecursionError) as exc:
            return AnswerKeyVerdict(problem="unloadable", message=f"cannot load {ref}: {exc}")
        return AnswerKeyVerdict(text=body, notes=(
            f"WARNING: {source} predates bundle provenance; assuming the entry point only. "
            "Re-run `skill novelty atoms` if the atoms came from bundled files.",))

    rows = key.files
    if not key.usable_files:
        return AnswerKeyVerdict(problem="no_file_rows", message=(
            f"{source} records a bundle without usable file rows; re-run `skill novelty atoms` "
            "rather than probing against an answer key whose provenance cannot be checked"))
    try:
        now = load_bundle(ref, **_replay_selection(key))
    except (OSError, ValueError, RecursionError) as exc:
        return AnswerKeyVerdict(problem="unloadable", message=f"cannot load {ref}: {exc}")

    # An answer key is for one artifact: skill A's atoms must not pass for skill B just
    # because the relative paths match, even for entry-point-only records.
    recorded_name = key.artifact_name
    if recorded_name and recorded_name != now.name:
        return AnswerKeyVerdict(problem="wrong_artifact", message=(
            f"these atoms were extracted from `{recorded_name}` and --artifact is "
            f"`{now.name}`: an answer key belongs to one artifact. Extract atoms for this "
            "one instead."))
    # Checked after the load and the artifact check, where the list is first read, so a
    # malformed list changes neither an unloadable nor a wrong-artifact verdict.
    if not key.excluded_ok:
        return AnswerKeyVerdict(problem="no_file_rows", message=(
            f"{source} records a bundle without a usable excluded list; re-run `skill novelty "
            "atoms` rather than probing against an answer key whose provenance cannot be checked"))

    recorded = [row.path for row in rows]
    rebuilt = [item.path for item in now.files]
    edited = _edited_since(rows, now)
    added = _added_since(key, now, recorded, rebuilt)
    if rebuilt != recorded or edited or added:
        return AnswerKeyVerdict(
            problem="files_changed",
            message=(
                "the artifact's files changed since these atoms were extracted "
                f"({source}: {recorded}; now: {rebuilt}; edited: {edited}; "
                f"added: {added}). Re-run `skill novelty atoms`; probing a different file set "
                "against this answer key would mis-score every moved atom."),
            recorded=tuple(recorded), rebuilt=tuple(rebuilt), edited=tuple(edited),
            added=tuple(added))

    notes: list[str] = []
    # Allowed to differ, not allowed to differ silently: atoms extracted from text that is
    # gone will read as `still_missing`, a result about the edit that must be recognisable.
    entry_recorded = rows[0].sha256
    entry_changed = bool(entry_recorded and entry_recorded != now.files[0].sha256)
    if entry_changed:
        notes.append(
            f"NOTE: {now.files[0].path} has changed since these atoms were extracted. That "
            "is the re-measure step, so the run proceeds; atoms whose text was removed will "
            "score `still_missing` because of the edit, not because of the model.")
    if key.include_resources:
        notes.append(f"Measuring the bundle recorded in {source}: {len(recorded)} files, "
                     f"{key.resource_words_measured} bundled words")
    return AnswerKeyVerdict(text=now.text, bundle=now, notes=tuple(notes),
                            recorded=tuple(recorded), rebuilt=tuple(rebuilt),
                            entry_point_changed=entry_changed)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The saved answer key's ``bundle`` block, parsed once into a typed record.

``atoms.json`` carries the bundle record that :func:`dokimi.novelty.bundle.check_answer_key`
and :func:`dokimi.novelty.bundle.entry_point_only` read. :func:`parse_answer_key` reads the
block once; the checks take the :class:`AnswerKeyRecord` and never index the raw dict.

Values are kept as the record stored them; the one conversion is the truthiness of
``include_resources``. So a value that cannot be used (a budget that is not a number, a
span range that is missing) fails inside the check that uses it, where it always did.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any


class _Absent(Enum):
    """The type of :data:`ABSENT`: an enum member survives copy, deepcopy and pickle as itself."""

    ABSENT = "ABSENT"


#: Marks a key the saved record does not have, as distinct from one stored as ``null``.
ABSENT: Any = _Absent.ABSENT


def _rows(value: Any) -> bool:
    """Whether a stored value is a list of rows: any sequence but a string or bytes."""
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


@dataclass(frozen=True)
class RecordedFile:
    """One row of a saved ``files`` list: the entry point (row 0) or a bundled file.

    A row that is not an object carries no keys at all, so it has no ``path`` and no ``role``,
    and ``is_object`` is False.
    """

    path: Any = ABSENT
    role: Any = ABSENT
    words: Any = ABSENT
    sha256: Any = None
    end: Any = ABSENT
    is_object: bool = True

    @property
    def usable(self) -> bool:
        """Whether the row carries the ``path`` and ``role`` every file row must have."""
        return self.path is not ABSENT and self.role is not ABSENT


@dataclass(frozen=True)
class AnswerKeyRecord:
    """A saved answer key's bundle record. Built by :func:`parse_answer_key`.

    ``present`` is False for an absent or empty record: a file written before bundle
    provenance. ``excluded`` is None when the record has no ``excluded`` key at all; the
    check treats that differently from an empty list.
    """

    present: bool = False
    artifact_name: Any = None
    include_resources: bool = False
    resource_roles: Any = None
    max_file_words: Any = None
    max_total_words: Any = None
    n_files: Any = None
    resource_words_measured: Any = 0
    files: tuple[RecordedFile, ...] = ()
    excluded: tuple[Any, ...] | None = None
    excluded_ok: bool = True

    @property
    def usable_files(self) -> bool:
        """Whether there is at least one file row and every row is usable."""
        return bool(self.files) and all(row.usable for row in self.files)

    @property
    def usable(self) -> bool:
        """Whether the file rows and the ``excluded`` list can both be checked."""
        return self.usable_files and self.excluded_ok


def shown(value: Any) -> Any:
    """A recorded value as a message shows it: an absent key reads as None, as `dict.get` does."""
    return None if value is ABSENT else value


def _recorded_file(row: Any) -> RecordedFile:
    if not isinstance(row, Mapping):
        return RecordedFile(is_object=False)
    return RecordedFile(path=row.get("path", ABSENT), role=row.get("role", ABSENT),
                        words=row.get("words", ABSENT), sha256=row.get("sha256"),
                        end=row.get("end", ABSENT))


def _excluded_path(row: Any) -> Any:
    return row.get("path") if isinstance(row, Mapping) else None


def parse_answer_key(record: Mapping[str, Any] | AnswerKeyRecord | None) -> AnswerKeyRecord:
    """The typed form of a saved bundle record (``None`` or ``{}`` for a legacy file).

    A record that is already parsed is returned as it is, so a caller may parse once and pass
    the result to every check.
    """
    if isinstance(record, AnswerKeyRecord):
        return record
    raw = record or {}
    files = raw.get("files") or []
    rows = tuple(_recorded_file(row) for row in files) if _rows(files) else ()
    excluded = None
    excluded_ok = True
    if "excluded" in raw:
        listed = raw.get("excluded") or []
        # a row is an object whose path is a string or absent (the CLI loader takes a row with no path, and
        # the check reads it as None, as it always did); anything else cannot say what it excludes. The
        # check reads excluded only with include_resources, so a record without them is not refused for it.
        excluded_ok = not raw.get("include_resources") or (_rows(listed) and all(
            isinstance(row, Mapping) and (row.get("path") is None or isinstance(row.get("path"), str))
            for row in listed))
        excluded = tuple(_excluded_path(row) for row in listed) if _rows(listed) else ()
    return AnswerKeyRecord(
        present=bool(raw),
        artifact_name=raw.get("artifact_name"),
        include_resources=bool(raw.get("include_resources")),
        resource_roles=raw.get("resource_roles"),
        max_file_words=raw.get("max_file_words"),
        max_total_words=raw.get("max_total_words"),
        n_files=raw.get("n_files"),
        resource_words_measured=raw.get("resource_words_measured", 0),
        files=rows,
        excluded=excluded,
        excluded_ok=excluded_ok,
    )

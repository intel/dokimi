# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read and write questions artifacts across schema versions.

``questions.v2`` is what the framework writes: the question text is under ``question``, the key
the normalizer, every generator and every reader use. ``questions.v1`` kept it under ``text``;
curated (golden) sets in content repositories are v1 and keep loading. A v2 record may not carry
``text`` at all, so a record never holds two texts that readers could disagree on.

Tolerant reader, strict writer, inputs never mutated:

- :func:`load_questions` is the one read path for a question set, whatever the run does with it
  (a pipeline cache, ``--questions-from``, ``--custom-questions``, ``arms run``, ...). It takes
  v1, v2 and the older shapes sets were hand-written in, and returns the set in the v2 shape.
  A file that needed more than the v1 -> v2 rename is logged once
  (``read <path> in legacy questions format (...)``), so reading those shapes can be retired
  deliberately. A set that is still not v2 after that fails with
  :class:`~dokimi.artifacts.ArtifactValidationError` naming the record and the fix.
- :func:`save_questions_artifact` is the one write path: it renames ``text`` to ``question`` in
  every record (:func:`~dokimi.questions.normalizer.canonical_question`), then hands
  the payload to :func:`~dokimi.artifacts.save_artifact`, which stamps, validates and
  writes it. A generated or merged set that is not a valid v2 artifact fails here, before any
  answer is paid for, and is kept at ``<name>.rejected``.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dokimi.artifacts import (
    SCHEMA_VERSIONS,
    SUPERSEDED_VERSIONS,
    ArtifactValidationError,
    save_artifact,
    schema_errors,
    schema_version_for,
)
from dokimi.questions.normalizer import canonical_question, record_id
from dokimi.utils import question_id

logger = logging.getLogger(__name__)

KIND = "questions"


def upgrade_questions(data: dict) -> dict:
    """A questions payload of any accepted version, in the current version's shape.

    Each record goes through :func:`canonical_question` (``text`` -> ``question``, nothing else
    touched) and ``schema_version`` is set to the current version; every other top-level key is
    kept. The result is not validated: v1 records are open, so a v1 file may carry a key that v2
    declares with a stricter type. ``data`` is not mutated.
    """
    out = {"schema_version": SCHEMA_VERSIONS[KIND],
           **{k: v for k, v in data.items() if k != "schema_version"}}
    records = data.get("questions")
    if isinstance(records, list):
        out["questions"] = [canonical_question(q) if isinstance(q, dict) else q for q in records]
    return out


def save_questions_artifact(data: dict, path: Path | str) -> dict:
    """Write ``data`` as a current-version questions artifact; return exactly what was written.

    Records may still use v1's ``text``; it is renamed to ``question``. Validation, the
    ``.rejected`` copy on failure and the atomic write are :func:`save_artifact`'s.
    """
    return save_artifact(KIND, upgrade_questions(data), path)


@dataclass(frozen=True)
class QuestionSet:
    """A questions artifact read from disk, in the current version's shape."""

    artifact: dict
    #: The version the file is: the one it is valid under (declared, or for an unstamped file the
    #: newest it matches), ``None`` for a legacy file valid under none. With ``validate=False``
    #: the version it declares (``None`` when it declares none).
    source_version: str | None
    #: What reading it changed beyond the v1 -> v2 rename, one entry per kind of change; empty
    #: when the set is v2 once ``text`` is renamed. A non-empty ``legacy`` is what the one-line
    #: notice reports.
    legacy: tuple[str, ...] = ()

    @property
    def questions(self) -> list[dict[str, Any]]:
        return self.artifact.get("questions", [])


#: Legacy record shapes the reader upgrades, each with how the notice names it.
_ID_FROM_QUESTION_ID = "`question_id` read as `id`"
_ID_ASSIGNED = "no `id` (numbered by position, q0000...)"
_TEXT_FROM_QUESTION_TEXT = "`question_text` read as `question`"
_METADATA_LIST = 'list `metadata` read as {"items": [...]}'


def _read_record(q: dict, idx: int, seen: Counter[str], taken: set) -> dict:
    """``q`` in the v2 record shape, counting each legacy shape it had in ``seen``.

    Only what v2 cannot hold is changed: v1 ``text`` -> ``question``; ``question_text`` ->
    ``question`` when there is no other text; a missing ``id`` from ``question_id``, else the
    normalizer's positional ``q{idx:04d}`` (:func:`~dokimi.questions.normalizer.record_id`, so
    ``0`` stays an id), ``_``-prefixed until it is in no other record's ``taken`` id, the rule
    ``arms run`` used; a list ``metadata`` (v2 declares an object) -> ``{"items": [...]}``.
    Every other key is kept as written. ``q`` is not mutated.
    """
    out = canonical_question(q)
    if "question" not in out and "question_text" in out:
        out = {("question" if k == "question_text" else k): v for k, v in out.items()}
        seen[_TEXT_FROM_QUESTION_TEXT] += 1
    if out.get("id") in (None, ""):
        from_question_id = out.get("question_id") not in (None, "")
        new_id = record_id(out, idx)
        while not from_question_id and new_id in taken:
            new_id = f"_{new_id}"
        if isinstance(new_id, (str, int)):
            taken.add(new_id)
        out = {"id": new_id, **{k: v for k, v in out.items()
                                if k != "id" and not (from_question_id and k == "question_id")}}
        seen[_ID_FROM_QUESTION_ID if from_question_id else _ID_ASSIGNED] += 1
    if isinstance(out.get("metadata"), list):
        out["metadata"] = {"items": out["metadata"]}
        seen[_METADATA_LIST] += 1
    return out


def _records(raw: Any, path: Path) -> tuple[dict, bool]:
    """``raw`` as a questions object, and whether it was a bare list; refuse anything else."""
    if isinstance(raw, list):
        return {"questions": raw}, True
    if not isinstance(raw, dict):
        raise ArtifactValidationError(
            f"Expected a list of questions in {path}: a JSON list, or an object with a "
            f"`questions` list; got {type(raw).__name__}")
    if not isinstance(raw.get("questions"), list):
        found = (f"`questions` is {type(raw['questions']).__name__}" if "questions" in raw
                 else f"no `questions` key (keys: {', '.join(map(str, raw)) or 'none'})")
        raise ArtifactValidationError(
            f"Expected a list of questions in {path} under `questions`; {found}")
    return raw, False


def _explain(path: Path, data: dict, errors: list[str]) -> str:
    """The refusal message for a set still invalid after the upgrade: each record, then the fix."""
    records = data["questions"]
    lines = []
    for e in errors:
        where, _, msg = e.partition(": ")
        parts = where.split("/")
        if len(parts) >= 2 and parts[0] == "questions" and parts[1].isdigit():
            n = int(parts[1])
            rec = records[n] if n < len(records) else None
            rid = f" (id {rec['id']!r})" if isinstance(rec, dict) and "id" in rec else ""
            field = "/".join(parts[2:])
            where = f"record {n}{rid}" + (f", `{field}`" if field else "")
        lines.append(f"  - {where}: {msg}")
    return (f"{path} is not a usable question set:\n" + "\n".join(lines) + "\n"
            f"Fix in {path}: give each record above a non-empty question (`question`, or v1 "
            f"`text`) and its declared fields the types in "
            f"dokimi/schemas/{SCHEMA_VERSIONS[KIND]}.json, or drop the record.")


def load_questions(path: Path | str, *, validate: bool = True) -> QuestionSet:
    """Read a question set in any shape this framework has read, and return it as questions.v2.

    Accepts questions.v1 and questions.v2 (stamped, or unstamped and matched by shape) and the
    legacy shapes upgraded by :func:`_read_record`, plus a bare JSON list of records. A file that
    needed an upgrade beyond the v1 -> v2 rename -- or is not valid under the version it
    declares -- is logged once at WARNING. The file itself is never written.

    With ``validate`` the upgraded set must be a valid v2 artifact, or
    :class:`~dokimi.artifacts.ArtifactValidationError` names each failing record and the fix; so
    must a ``schema_version`` this reader does not know. A set that is not a list of questions at
    all (e.g. ``{"questions": {...}}``) is refused either way.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    data, bare_list = _records(raw, path)
    current = SCHEMA_VERSIONS[KIND]
    declared = data.get("schema_version")
    known = (current, *SUPERSEDED_VERSIONS.get(KIND, ()))
    if validate and "schema_version" in data and declared not in known:
        raise ArtifactValidationError(
            f"{path}: schema_version {declared!r} is not a questions version this dokimi reads "
            f"({', '.join(known)}); fix the stamp, or remove it to have the set matched by shape")

    notes = ["a bare list, not an object"] if bare_list else []
    seen: Counter[str] = Counter()
    records = data["questions"]
    taken = {qid for q in records if isinstance(q, dict)
             for qid in (question_id(q),) if isinstance(qid, (str, int))}
    read = [_read_record(q, i, seen, taken) if isinstance(q, dict) else q
            for i, q in enumerate(records)]
    artifact = upgrade_questions({**data, "questions": read})

    source_version: str | None
    if validate:
        matched = None if bare_list else schema_version_for(KIND, data)
        # For v1-declared or v1-matching files, validate against v1 (on original data),
        # not v2. v1 files can have undeclared fields with null values; v2 declares them
        # with specific types. After upgrade, the v2 schema would reject them even though
        # they are valid v1.
        if declared in SUPERSEDED_VERSIONS.get(KIND, ()) or matched in SUPERSEDED_VERSIONS.get(KIND, ()):
            # This is a v1 file (declared or matched); validate against v1 on original data
            validate_schema: str = declared if isinstance(declared, str) else matched  # type: ignore[assignment]
            errors = schema_errors(validate_schema, data)
        else:
            # This is v2 or an unknown version; validate against v2
            validate_schema = current
            errors = schema_errors(current, artifact)

        if errors:
            raise ArtifactValidationError(_explain(path, artifact, errors))
        source_version = matched if matched and not schema_errors(matched, data) else None
        if source_version is None and not bare_list and not seen:
            notes.append(f"not valid as the {matched} it "
                         f"{'declares' if 'schema_version' in data else 'is closest to'}")
    else:
        source_version = declared if isinstance(declared, str) else None
    legacy = (*notes, *(f"{n} record{'s' if n != 1 else ''}: {what}" for what, n in seen.items()))
    if legacy:
        logger.warning("read %s in legacy questions format (%s); using it as %s",
                       path, "; ".join(legacy), current)
    return QuestionSet(artifact=artifact, source_version=source_version, legacy=legacy)

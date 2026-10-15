# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read and write questions artifacts across schema versions.

``questions.v2`` is what the framework writes: the question text is under ``question``, the key
the normalizer, every generator and every reader use. ``questions.v1`` kept it under ``text``;
curated (golden) sets in content repositories are v1 and keep loading. A v2 record may not carry
``text`` at all, so a record never holds two texts that readers could disagree on.

- :func:`save_questions_artifact` is the one write path: it renames ``text`` to ``question`` in
  every record (:func:`~dokimi.questions.normalizer.canonical_question`), then hands
  the payload to :func:`~dokimi.artifacts.save_artifact`, which stamps, validates and
  writes it. A generated or merged set that is not a valid v2 artifact fails here, before any
  answer is paid for, and is kept at ``<name>.rejected``.
- :func:`load_questions` reads either version -- stamped, or unstamped and matched by shape --
  and returns the set in the v2 shape together with the version the file was.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dokimi.artifacts import SCHEMA_VERSIONS, load_artifact, save_artifact, schema_version_for
from dokimi.questions.normalizer import canonical_question

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
    #: The version the file was: the one it validated against, or with ``validate=False`` the one
    #: it declares (``None`` when it declares none).
    source_version: str | None

    @property
    def questions(self) -> list[dict[str, Any]]:
        return self.artifact.get("questions", [])


def load_questions(path: Path | str, *, validate: bool = True) -> QuestionSet:
    """Read a questions.v1 or questions.v2 file and return it in the v2 shape.

    With ``validate`` the file must be valid under the version it declares, or -- unstamped --
    under some accepted version (:func:`~dokimi.artifacts.schema_version_for`);
    otherwise :class:`~dokimi.artifacts.ArtifactValidationError` is raised.
    """
    data = load_artifact(KIND, Path(path), validate=validate)
    if not isinstance(data, dict):
        raise TypeError(f"{path}: a questions artifact is a JSON object, got {type(data).__name__}")
    if validate:
        version: str | None = schema_version_for(KIND, data)
    else:
        declared = data.get("schema_version")
        version = declared if isinstance(declared, str) else None
    return QuestionSet(artifact=upgrade_questions(data), source_version=version)

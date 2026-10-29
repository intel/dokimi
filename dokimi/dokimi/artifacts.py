# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Versioned, schema-validated I/O for produced artifacts.

The benchmark produces long-lived JSON artifact kinds: ``questions``,
``answers``, ``eval`` (LLM-judge scores), ``arms`` (treatment-arm comparisons),
``task_runs`` (executable task results), ``explore_runs`` (ExploreBench
localization results), ``novelty`` (what an artifact's content adds to one
model), ``guidance_reach`` (L1 reach probes), ``repo_readiness`` (L0 claim
checks), ``tool_preference`` (which of two equal tools a model picks, and
what moves it), ``repo_environment`` (static inspection of what a
repository offers an agent), ``plan`` (the cells a run promises to measure),
``result`` (one status per planned cell), ``repo_budget`` (what a repository puts
into an agent's context) and ``task_inspect`` (which eval-task format each
path under a tree is). This module stamps each with a
``schema_version`` and validates it against the JSON Schema in
``dokimi/schemas/`` on save and load, so fixtures and baselines stay
forward-compatible and self-describing.

Every artifact of a registered kind is written through :func:`save_artifact`, partial
(incremental) saves included: a run that would write an invalid artifact fails on its first
save, not after the last paid model call. :func:`schema_errors` is the one place a JSON Schema
is applied, for artifacts and for validated *inputs* (manifests, profiles, specs) alike.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# kind -> current schema_version string (also the schema filename stem).
SCHEMA_VERSIONS: dict[str, str] = {
    "questions": "questions.v2",
    "answers": "answers.v1",
    "eval": "eval.v1",
    "arms": "arms.v1",
    "matrix_rollup": "matrix_rollup.v1",
    "plugin_delta": "plugin_delta.v1",
    "subject_scorecard": "subject_scorecard.v2",
    "novelty": "novelty.v1",
    "task_runs": "task_runs.v1",
    "explore_runs": "explore_runs.v1",
    "guidance_reach": "guidance_reach.v1",
    "repo_readiness": "repo_readiness.v1",
    "tool_preference": "tool_preference.v1",
    "repo_environment": "repo_environment.v1",
    "plan": "plan.v1",
    "result": "result.v1",
    "repo_budget": "repo_budget.v1",
    "task_inspect": "task_inspect.v1",
    "headroom": "headroom.v1",
}

# kind -> superseded versions still accepted on *load*, newest first. An artifact written under an
# older version is still a valid artifact and reading one must not fail; `stamp` always writes the
# current version, so nothing is written back at an old version. A reader that needs the current
# shape upgrades on read (questions.v1 `text` -> v2 `question`: dokimi.questions.artifact).
SUPERSEDED_VERSIONS: dict[str, tuple[str, ...]] = {
    "questions": ("questions.v1",),
    "subject_scorecard": ("subject_scorecard.v1",),
}


class ArtifactValidationError(ValueError):
    """Raised when an artifact does not conform to its schema."""


def _schema_path(kind: str, version: str | None = None) -> Path:
    if kind not in SCHEMA_VERSIONS:
        raise KeyError(f"Unknown artifact kind: {kind!r}. Known: {sorted(SCHEMA_VERSIONS)}")
    return SCHEMA_DIR / f"{version or SCHEMA_VERSIONS[kind]}.json"


def schema_version_for(kind: str, data: dict) -> str:
    """Which schema version ``data`` should be validated against.

    The current version, unless the payload declares a superseded one for this kind — an artifact
    written before a bump is still valid, so reading it must not fail. An unknown version string is
    *not* honoured: it falls through to the current schema, whose ``const`` then rejects it, which
    is how a typo or a future version stays an error instead of silently skipping validation.

    An *unstamped* payload (no ``schema_version`` key: hand-written and older sets, e.g. curated
    question files) is matched against the current version, then each superseded one, newest
    first, and resolves to the first it satisfies; when it satisfies none, to the one it violates
    least (the newest on a tie), so a set with one real fault reports that fault, not every
    record as a wrong shape.
    """
    if "schema_version" not in data and SUPERSEDED_VERSIONS.get(kind):
        counts = {v: len(schema_errors(v, data))
                  for v in (SCHEMA_VERSIONS[kind], *SUPERSEDED_VERSIONS[kind])}
        return min(counts, key=counts.__getitem__)
    declared = data.get("schema_version")
    if declared in SUPERSEDED_VERSIONS.get(kind, ()):
        return str(declared)
    return SCHEMA_VERSIONS[kind]


def schema_errors(schema: str | Path, instance: Any) -> list[str]:
    """Every way ``instance`` violates a JSON Schema (draft 2020-12); empty when it conforms.

    ``schema`` is a schema name in :data:`SCHEMA_DIR` (``"repo_static.v1"``) or the path of a
    schema file elsewhere. Each problem reads ``<path>: <message>``, ``<path>`` being the
    ``/``-joined location in ``instance`` (``<root>`` for the top level), sorted by location.
    An unreadable or malformed schema file raises ``OSError`` / ``json.JSONDecodeError``: that
    is a packaging fault, not a problem with ``instance``.
    """
    from jsonschema import Draft202012Validator

    path = SCHEMA_DIR / f"{schema}.json" if isinstance(schema, str) else Path(schema)
    document = json.loads(path.read_text(encoding="utf-8"))
    return [
        f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
        for e in sorted(Draft202012Validator(document).iter_errors(instance),
                        key=lambda e: list(e.path))
    ]


def validate_artifact(kind: str, data: dict) -> None:
    """Validate ``data`` against the schema for ``kind`` (raises on failure)."""
    errors = schema_errors(_schema_path(kind, schema_version_for(kind, data)), data)
    if errors:
        details = "\n".join(f"  - {e}" for e in errors)
        raise ArtifactValidationError(f"{kind} artifact validation failed:\n{details}")


def stamp(kind: str, data: dict) -> dict:
    """Return ``data`` with its ``schema_version`` set for ``kind``."""
    if kind not in SCHEMA_VERSIONS:
        raise KeyError(f"Unknown artifact kind: {kind!r}")
    data["schema_version"] = SCHEMA_VERSIONS[kind]
    return data


def save_artifact(kind: str, data: dict, path: Path | str) -> dict:
    """Write ``data`` to ``path`` as a ``kind`` artifact; return exactly what was written.

    - ``schema_version`` is set to the current version for ``kind`` and leads the document
      (a value already in ``data`` is replaced); ``data`` itself is not mutated.
    - The payload is validated first. :class:`ArtifactValidationError` means nothing was
      written at ``path`` (a file already there is untouched); the rejected payload is kept
      at ``<name>.rejected`` so a paid run is not lost to a schema problem. A later valid
      save to ``path`` removes that ``<name>.rejected`` (only that sibling), so a leftover
      always belongs to the latest attempt; if it cannot be removed the save still stands and
      a warning is logged. ``KeyError`` means ``kind`` is not registered in
      :data:`SCHEMA_VERSIONS`.
    - The write is atomic (a ``<name>.<pid>.<thread>.tmp`` sibling, then ``os.replace``), so a
      reader or a crash never sees half a file, and concurrent saves to one path (threads or
      processes) each publish one whole payload; the last one wins. A failed write removes its
      tmp file. Missing parent directories of ``path`` are created.
    - A symlink at ``path`` is kept: the payload replaces the file it resolves to (the tmp file
      sits beside that target, so the rename stays on one filesystem). A dangling link creates
      its target when the target's directory exists; otherwise ``FileNotFoundError`` is raised
      and nothing is written (the target's directories are *not* created). A link loop raises
      ``OSError``. ``<name>.rejected`` stays beside ``path``, not the target.
    - JSON with ``indent=2``, ASCII-escaped, UTF-8.
    """
    if kind not in SCHEMA_VERSIONS:
        raise KeyError(f"Unknown artifact kind: {kind!r}")
    payload = {"schema_version": SCHEMA_VERSIONS[kind],
               **{k: v for k, v in data.items() if k != "schema_version"}}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    try:
        validate_artifact(kind, payload)
    except ArtifactValidationError as exc:
        rejected = path.with_name(path.name + ".rejected")
        rejected.write_text(text, encoding="utf-8")
        raise ArtifactValidationError(f"{exc}\n(not written to {path}; payload kept at {rejected})") from exc
    target = _write_target(path)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    stale = path.with_name(path.name + ".rejected")
    try:
        stale.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("saved %s, but could not remove the stale rejected payload %s: %s", path, stale, exc)
    return payload


def _write_target(path: Path) -> Path:
    """The file a save to ``path`` replaces: ``path`` itself, or what a symlink there resolves to."""
    if not path.is_symlink():
        return path
    target = Path(os.path.realpath(path))
    if target.is_symlink():  # realpath gives up on a loop and hands back a link
        raise OSError(f"symlink {path} does not resolve to a file (link loop at {target})")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"symlink {path} points into a missing directory: {target}")
    return target


def load_artifact(kind: str, path: Path, *, validate: bool = True) -> dict:
    """Read and (optionally) validate an artifact from ``path``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Artifact must be a JSON object, got {type(data).__name__}")
    if validate:
        validate_artifact(kind, data)
    return data

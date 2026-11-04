# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Versioned, schema-validated I/O for produced artifacts.

The benchmark produces long-lived JSON artifact kinds: ``questions``,
``answers``, ``eval`` (LLM-judge scores), ``arms`` (treatment-arm comparisons),
``task_runs`` (executable task results), ``explore_runs`` (ExploreBench
localization results), ``novelty`` (what an artifact's content adds to one
model), ``guidance_reach`` (L1 reach probes), ``skill_discovery`` (which
skills a harness lists, from where), ``repo_readiness`` (L0 claim
checks), ``tool_preference`` (which of two equal tools a model picks, and
what moves it), ``repo_environment`` (static inspection of what a
repository offers an agent), ``plan`` (the cells a run promises to measure),
``result`` (one status per planned cell), ``repo_budget`` (what a repository puts
into an agent's context), ``effective_instructions`` (the effective-instructions/v0
receipt of a reach probe), ``need_graph`` and ``eval_coverage`` (a frozen need graph and an
eval set's coverage of it), ``task_inspect`` (which eval-task format each
path under a tree is), ``repo_hits_judged`` (a judge's labels of one replay cell's credited pairs)
and ``dokimi_config`` (a repository's subjects and where their evals are) and ``reach_matrix`` (the reach
conformance matrix). This module stamps each with a ``schema_version`` and validates it against the JSON Schema in
``dokimi/schemas/`` on save and load, so fixtures and baselines stay
forward-compatible and self-describing.

An artifact a run writes (every kind but :data:`INPUT_KINDS`) also carries the run's envelope after
its ``schema_version``: the ``run_id`` of the process that saved it (:func:`run_id`) and the
``dokimi_version`` that wrote it. The files one command writes share a ``run_id``; files written
before the envelope existed have none and stay valid.

Every artifact of a registered kind is written through :func:`save_artifact`, partial
(incremental) saves included: a run that would write an invalid artifact fails on its first
save, not after the last paid model call. :func:`schema_errors` is the one place a JSON Schema
is applied, for artifacts and for validated *inputs* (manifests, profiles, specs) alike.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dokimi._version import __version__
from dokimi.utils import InputError, read_json_input

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
    "skill_evidence": "skill_evidence.v1",
    "novelty": "novelty.v1",
    "task_runs": "task_runs.v1",
    "explore_runs": "explore_runs.v1",
    "guidance_reach": "guidance_reach.v1",
    "skill_discovery": "skill_discovery.v1",
    "repo_readiness": "repo_readiness.v1",
    "tool_preference": "tool_preference.v1",
    "repo_environment": "repo_environment.v1",
    "plan": "plan.v1",
    "result": "result.v1",
    "repo_budget": "repo_budget.v1",
    "effective_instructions": "effective_instructions.v1",
    "task_inspect": "task_inspect.v1",
    "headroom": "headroom.v1",
    "redteam": "redteam.v1",
    "cli_routes": "cli_routes.v1",
    "repo_ledger": "repo_ledger.v1",
    "dokimi_config": "dokimi_config.v1",
    "need_graph": "need_graph.v1",
    "eval_coverage": "eval_coverage.v1",
    "catalog_eval": "catalog_eval.v1",
    "reach_matrix": "reach_matrix.v1",
    "repo_hits_judged": "repo_hits_judged.v1",
}

# kind -> superseded versions still accepted on *load*, newest first. An artifact written under an
# older version is still a valid artifact and reading one must not fail; `stamp` always writes the
# current version, so nothing is written back at an old version. A reader that needs the current
# shape upgrades on read (questions.v1 `text` -> v2 `question`: dokimi.questions.artifact).
SUPERSEDED_VERSIONS: dict[str, tuple[str, ...]] = {
    "questions": ("questions.v1",),
    "subject_scorecard": ("subject_scorecard.v1",),
}


#: Kinds that say what a run is given, not what it produced: a plan, a repository's config, a rule
#: ledger, a frozen need graph, the CLI's route table. :func:`save_artifact` gives them no envelope.
INPUT_KINDS: frozenset[str] = frozenset({"plan", "dokimi_config", "repo_ledger", "need_graph", "cli_routes"})

#: The envelope keys :func:`save_artifact` writes on a run kind, after ``schema_version``.
ENVELOPE_KEYS: tuple[str, ...] = ("run_id", "dokimi_version")

RUN_ID_ENV = "DOKIMI_RUN_ID"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_generated_run_id: str | None = None
_run_id_lock = threading.Lock()


def run_id() -> str:
    """The ``run_id`` this process writes: ``DOKIMI_RUN_ID`` when set and not empty, else its own.

    The process's own id is made on first use and kept, ``<UTC time>-<8 hex digits>``
    (``20261104T153012Z-1a2b3c4d``), so every artifact one command saves shares it. Setting
    ``DOKIMI_RUN_ID`` gives several commands one id (a CI job's steps, say). Either way it is 1-128
    letters, digits, ``.``, ``_`` or ``-``, starting with a letter or a digit, so it can name a
    directory.

    Raises:
        InputError: ``DOKIMI_RUN_ID`` is set to anything else.
    """
    global _generated_run_id
    given = os.environ.get(RUN_ID_ENV, "")
    if given:
        if not _RUN_ID.fullmatch(given):
            raise InputError(f"{RUN_ID_ENV}={given!r} is not a run id: 1-128 letters, digits, '.', '_' "
                             f"or '-', starting with a letter or a digit")
        return given
    with _run_id_lock:
        if _generated_run_id is None:
            _generated_run_id = (f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-"
                                 f"{secrets.token_hex(4)}")
        return _generated_run_id


def envelope(kind: str) -> dict[str, str]:
    """The envelope :func:`save_artifact` writes on a ``kind`` artifact; empty for an input kind."""
    if kind not in SCHEMA_VERSIONS:
        raise KeyError(f"Unknown artifact kind: {kind!r}")
    return {} if kind in INPUT_KINDS else {"run_id": run_id(), "dokimi_version": __version__}


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
    """Return ``data`` with its ``schema_version`` and :func:`envelope` set for ``kind``, as
    :func:`save_artifact` writes them (a ``--json`` print of what ``--out`` saves)."""
    if kind not in SCHEMA_VERSIONS:
        raise KeyError(f"Unknown artifact kind: {kind!r}")
    data["schema_version"] = SCHEMA_VERSIONS[kind]
    data.update(envelope(kind))
    return data


def save_artifact(kind: str, data: dict, path: Path | str) -> dict:
    """Write ``data`` to ``path`` as a ``kind`` artifact; return exactly what was written.

    - ``schema_version`` is set to the current version for ``kind`` and leads the document,
      followed by the :func:`envelope` (``run_id``, ``dokimi_version``) unless ``kind`` is an
      input kind. Values already in ``data`` under these keys are replaced: the file records the
      process that last saved it, so a run resumed from a partial file takes the new run's id.
      ``data`` itself is not mutated.
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
    head = {"schema_version": SCHEMA_VERSIONS[kind], **envelope(kind)}
    payload = {**head, **{k: v for k, v in data.items() if k not in head}}
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


def load_artifact(kind: str, path: Path | str, *, validate: bool = True) -> dict:
    """Read and (optionally) validate an artifact of ``kind`` from ``path``.

    Every failure is an :class:`~dokimi.utils.InputError` naming ``path``: a file that is
    missing, unreadable, not UTF-8 or JSON, not a JSON object, or that fails the schema.
    """
    data = read_json_input(path)
    if not isinstance(data, dict):
        raise InputError(f"{path} is not a {kind} artifact: expected a JSON object, got {type(data).__name__}")
    if validate:
        try:
            validate_artifact(kind, data)
        except ArtifactValidationError as exc:
            raise InputError(f"{path} is not a valid {kind} artifact: {exc}") from exc
    return data

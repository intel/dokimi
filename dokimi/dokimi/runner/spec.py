# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Benchmark spec loading, JSON Schema validation, and golden-manifest selection.

The runtime contract for a benchmark spec is declared in
``dokimi/runner/spec.schema.json``. This module enforces that contract at load
time (not only in CI/Make) and resolves the document set from the spec's
``golden_manifest`` instead of a hardcoded ``docs/`` glob.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

# Shipped next to this module (package data), so it resolves the same from a
# checkout and from an installed wheel, whatever the working directory.
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "spec.schema.json"


class SpecValidationError(ValueError):
    """Raised when a spec fails JSON Schema validation."""


class DocSelectionError(ValueError):
    """Raised when a ``golden_manifest`` glob is unusable or matches a document count outside its bounds."""


def is_full_spec(spec: dict) -> bool:
    """Return True if ``spec`` is a complete v1 spec (vs. a minimal/legacy one).

    Minimal specs (used by some unit tests and ad-hoc runs) only carry
    ``weights``/``metrics`` and are not schema-validated. A spec opts into full
    schema validation by declaring ``version: 1``. (Manifest *usage* is keyed
    separately off the presence of ``golden_manifest`` in the runner.)
    """
    return spec.get("version") == 1


def validate_spec(spec: dict, schema_path: Path | None = None) -> None:
    """Validate ``spec`` against the JSON Schema, raising on the first errors.

    Errors are aggregated into a single human-readable message.
    """
    import json

    from dokimi.artifacts import schema_errors

    schema_path = schema_path or DEFAULT_SCHEMA_PATH
    try:
        errors = schema_errors(Path(schema_path), spec)
    except OSError as exc:
        raise SpecValidationError(f"Failed to read spec schema: {schema_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecValidationError(f"Invalid JSON in spec schema: {schema_path}: {exc}") from exc

    if errors:
        details = "\n".join(f"  - {e}" for e in errors)
        raise SpecValidationError(f"Benchmark spec validation failed:\n{details}")


def load_spec(spec_path: Path, *, validate: bool = True) -> dict:
    """Read a spec YAML and, for full v1 specs, validate it against the schema."""
    try:
        content = Path(spec_path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # ValueError covers bad UTF-8
        raise RuntimeError(f"Failed to read spec file: {spec_path}: {exc}") from exc

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML in spec file: {spec_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Spec root must be a mapping/object: {spec_path}")

    if validate and is_full_spec(data):
        validate_spec(data)

    return data


def _is_excluded(rel_path: str, patterns: list[str]) -> bool:
    """Match a repo-relative posix path against exclude glob patterns.

    Supports both file globs (``docs/*.md``) and directory-recursive patterns
    (``docs/archive/**``), the latter excluding everything beneath the prefix.
    """
    for pattern in patterns:
        # fnmatch's '*' already crosses '/', so collapse '**' to '*'.
        if fnmatch.fnmatch(rel_path, pattern.replace("**", "*")):
            return True
        prefix = pattern.rstrip("/").removesuffix("/**").rstrip("/")
        if prefix and (rel_path == prefix or rel_path.startswith(prefix + "/")):
            return True
    return False


def select_docs(root: Path, manifest: dict) -> list[Path]:
    """Resolve and bound the document set from a ``golden_manifest``.

    Applies ``include``/``exclude`` globs (relative to ``root``) and enforces
    ``min_docs``/``max_docs``. Raises :class:`DocSelectionError` if a pattern
    is unusable or the resulting count violates the manifest bounds.
    """
    root = Path(root)
    root_resolved = root.resolve()
    include = manifest.get("include") or ["docs/**/*.md"]
    exclude = manifest.get("exclude") or []

    selected: set[Path] = set()
    for pattern in include:
        try:
            matches = list(root.glob(pattern))
        except (ValueError, NotImplementedError) as exc:  # an empty or absolute pattern
            raise DocSelectionError(f"golden_manifest include pattern {pattern!r}: {exc}") from exc
        for p in matches:
            # Reject matches that escape root (e.g. via ".." in a pattern).
            if p.is_file() and p.resolve().is_relative_to(root_resolved):
                selected.add(p)

    docs = sorted(
        p for p in selected
        if not _is_excluded(p.resolve().relative_to(root_resolved).as_posix(), exclude)
    )

    n = len(docs)
    min_docs = manifest.get("min_docs")
    max_docs = manifest.get("max_docs")
    if min_docs is not None and n < min_docs:
        raise DocSelectionError(
            f"golden_manifest violation: matched {n} document(s) under {root}, "
            f"fewer than min_docs={min_docs}. include={include} exclude={exclude}"
        )
    if max_docs is not None and n > max_docs:
        raise DocSelectionError(
            f"golden_manifest violation: matched {n} document(s) under {root}, "
            f"more than max_docs={max_docs}. include={include} exclude={exclude}"
        )

    return docs

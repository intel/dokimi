# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The runs root and the artifacts under it, each told apart by its first bytes.

The root is ``results/`` (relative to the working directory, where most commands write by
default) unless ``DOKIMI_RUNS_DIR`` names another. :func:`scan` walks it once: a file is an
artifact when it opens with a ``schema_version`` of a registered kind, the way
:func:`dokimi.artifacts.save_artifact` writes it, so a large file of another kind is never
parsed just to be skipped. Everything else is counted by why it was skipped, never dropped
silently. Nothing here prints or exits.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from dokimi.runs.kinds import FIELDS, VERSION_KINDS, read_field
from dokimi.utils import InputError

ENV_VAR = "DOKIMI_RUNS_DIR"
DEFAULT_ROOT = Path("results")
#: ``save_artifact`` writes ``{"schema_version": "<kind>.vN", ...`` with ``indent=2``.
HEAD_BYTES = 256
_HEAD = re.compile(rb'\A\s*\{\s*"schema_version"\s*:\s*"([^"\\\x00-\x1f]{1,100})"')


@dataclass(frozen=True)
class RunsRoot:
    """The directory ``runs`` reads, and whether ``DOKIMI_RUNS_DIR`` chose it."""

    path: Path
    from_env: bool

    def exists(self) -> bool:
        """True when the root is a directory; False when the default root is not there yet.

        Raises :class:`~dokimi.utils.InputError` when ``DOKIMI_RUNS_DIR`` names a missing path,
        or when the root is something other than a directory.
        """
        if self.path.is_dir():
            return True
        named = f"{ENV_VAR}={self.path}" if self.from_env else str(self.path)
        if self.path.exists():
            raise InputError(f"runs root {named} is not a directory")
        if self.from_env:
            raise InputError(f"runs root {named} does not exist")
        return False


def runs_root(environ: Mapping[str, str] | None = None) -> RunsRoot:
    """``DOKIMI_RUNS_DIR`` when set and not empty, else ``results/``."""
    value = (os.environ if environ is None else environ).get(ENV_VAR, "")
    return RunsRoot(Path(value), True) if value else RunsRoot(DEFAULT_ROOT, False)


@dataclass(frozen=True)
class Artifact:
    """One artifact under the root: its kind and what it says it is about."""

    path: Path  # relative to the root
    schema_version: str
    subjects: tuple[str, ...]
    models: tuple[str, ...]
    generated_at: str | None
    size_bytes: int
    mtime: float

    @property
    def kind(self) -> str:
        return VERSION_KINDS[self.schema_version]

    @property
    def subject(self) -> str | None:
        return ", ".join(self.subjects) or None

    @property
    def model(self) -> str | None:
        return ", ".join(self.models) or None

    @property
    def recorded_at(self) -> datetime | None:
        """``generated_at`` in UTC (read as UTC if it names no zone); ``None`` if absent, not ISO
        8601 or out of range."""
        try:
            stamp = datetime.fromisoformat(self.generated_at or "")
            return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(
                timezone.utc)
        except (ValueError, OverflowError):
            return None

    def to_json(self) -> dict:
        """The ``--json`` row. ``run_id`` is ``None``: no artifact records one yet (#277)."""
        return {"schema_version": self.schema_version, "kind": self.kind, "run_id": None,
                "subject": self.subject, "model": self.model, "generated_at": self.generated_at,
                "path": self.path.as_posix(), "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class Unreadable:
    """A file or directory under the root that could not be read; ``schema_version`` if known."""

    path: Path
    reason: str
    schema_version: str | None = None

    def to_json(self) -> dict:
        return {"path": self.path.as_posix(), "schema_version": self.schema_version,
                "reason": self.reason}


#: Why a file was skipped (the ``--json`` key) -> how ``runs list`` names it.
SKIP_REASONS = {
    "no_leading_schema_version": "JSON with no leading schema_version",
    "unknown_schema_version": "JSON of an unknown schema_version",
    "temporary": "*.tmp (a save in progress or interrupted)",
    "rejected": "*.rejected (payloads save_artifact refused)",
    "not_json": "not .json",
    "not_a_file": "not a regular file (a FIFO, socket or device)",
    "linked_directory": "symlinked directories (not walked)",
}


@dataclass
class ScanResult:
    """What :func:`scan` found: the artifacts, newest first, and every file it skipped."""

    root: Path
    artifacts: list[Artifact] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    unknown_versions: Counter[str] = field(default_factory=Counter)
    unreadable: list[Unreadable] = field(default_factory=list)


def head_version(path: Path) -> str | None:
    """The ``schema_version`` a file opens with, read from its first bytes; ``None`` if none."""
    with open(path, "rb") as fh:
        match = _HEAD.match(fh.read(HEAD_BYTES))
    return match.group(1).decode("utf-8", errors="replace") if match else None


def read_artifact(root: Path, path: Path, version: str) -> Artifact:
    """Parse the artifact at ``path`` whose first bytes declared ``version`` (a known one).

    Raises ``OSError`` when it cannot be read and ``ValueError`` when it is not the JSON object
    its first bytes promised.
    """
    stat = path.stat()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != version:
        raise ValueError(f"the document does not hold the schema_version {version} it opens with")
    fields = FIELDS[VERSION_KINDS[version]]
    times = read_field(data, fields.time)
    return Artifact(path=path.relative_to(root), schema_version=version,
                    subjects=read_field(data, fields.subject),
                    models=read_field(data, fields.model),
                    generated_at=times[0] if times else None,
                    size_bytes=stat.st_size, mtime=stat.st_mtime)


def _skip_reason(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".tmp"):
        return "temporary"
    if lower.endswith(".rejected"):
        return "rejected"
    return None if lower.endswith(".json") else "not_json"


def _reason(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return f"cannot read: {exc.strerror}"
    if isinstance(exc, UnicodeDecodeError):
        return "not UTF-8"
    if isinstance(exc, json.JSONDecodeError):
        return f"not valid JSON: {exc.msg} at line {exc.lineno}"
    return str(exc)


def _scan_file(result: ScanResult, path: Path) -> None:
    rel = path.relative_to(result.root)
    skip = _skip_reason(path.name)
    if skip:
        result.skipped[skip] += 1
        return
    try:
        regular = stat.S_ISREG(path.stat().st_mode)  # opening a FIFO would block
        version = head_version(path) if regular else None
    except OSError as exc:
        result.unreadable.append(Unreadable(rel, _reason(exc)))
        return
    if not regular:
        result.skipped["not_a_file"] += 1
    elif version is None:
        result.skipped["no_leading_schema_version"] += 1
    elif version not in VERSION_KINDS:
        result.skipped["unknown_schema_version"] += 1
        result.unknown_versions[version] += 1
    else:
        try:
            result.artifacts.append(read_artifact(result.root, path, version))
        except (OSError, ValueError, RecursionError) as exc:
            result.unreadable.append(Unreadable(rel, _reason(exc), version))


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:  # a directory listed but not searchable: os.walk reports it when it enters
        return False


def newest_first(artifacts: list[Artifact]) -> list[Artifact]:
    """By the time the artifact records (its file's mtime when it records none), then path."""
    def when(artifact: Artifact) -> float:
        stamp = artifact.recorded_at
        return stamp.timestamp() if stamp else artifact.mtime

    return sorted(artifacts, key=lambda a: (-when(a), a.path.as_posix()))


def scan(root: Path) -> ScanResult:
    """Every artifact under ``root`` (a directory), newest first; symlinked dirs are not walked."""
    result = ScanResult(root=root)

    def unlisted(exc: OSError) -> None:
        where = Path(exc.filename).relative_to(root) if exc.filename else Path(".")
        result.unreadable.append(Unreadable(where, f"cannot list: {exc.strerror or exc}"))

    for directory, dirnames, filenames in os.walk(root, onerror=unlisted):
        base = Path(directory)  # os.walk does not descend into a linked directory: count it
        result.skipped["linked_directory"] += sum(_is_link(base / d) for d in dirnames)
        dirnames.sort()
        for name in sorted(filenames):
            _scan_file(result, base / name)
    result.artifacts = newest_first(result.artifacts)
    result.skipped = +result.skipped  # drop the zero counts
    return result


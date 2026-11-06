# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One artifact under the runs root: its kind, whether it meets its schema, what it holds and
what lies next to it; or one run, the artifacts that share a ``run_id``. A failed schema is
reported, not raised: showing is not a gate."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from dokimi.artifacts import is_run_id, schema_errors, schema_version_for
from dokimi.runs.kinds import VERSION_KINDS, contents
from dokimi.runs.scan import (
    Artifact,
    Unreadable,
    head_version,
    inside,
    load_document,
    plain,
    read_artifact,
    read_error_reason,
    scan,
)
from dokimi.utils import InputError

#: Entries of the artifact's directory that ``runs show`` names; the rest are only counted.
MAX_SIBLINGS = 20


@dataclass(frozen=True)
class Sibling:
    """An entry beside the artifact: a file (with its schema_version, if it opens with one)
    or a directory (``name`` ends in ``/``)."""

    name: str
    schema_version: str | None = None


@dataclass
class Shown:
    """What ``runs show`` prints about one artifact."""

    artifact: Artifact
    schema: str  # the schema version the document was checked against
    errors: list[str]
    contents: dict[str, int]
    siblings: list[Sibling] = field(default_factory=list)
    siblings_total: int = 0
    siblings_error: str | None = None


def resolve(root: Path, address: str) -> tuple[Path, Path]:
    """The file ``address`` names, and the root its path is relative to (both absolute).

    ``address`` is a path relative to the root (as ``runs list`` prints it), or an absolute or
    working-directory path; once symlinks are resolved it must lie inside the resolved root, so
    nothing outside the root is read. Raises ``InputError`` otherwise.
    """
    candidates = [root / address, Path(address)]
    real_root = os.path.realpath(root)
    label, shown_root = plain(address), plain(root.as_posix())  # for messages only
    try:  # Python 3.11-3.13 raise PermissionError here for a directory that cannot be searched
        path = next((p for p in candidates if p.exists()), candidates[0])
        if not inside(real_root, os.path.realpath(path)):
            where = "leads outside" if inside(root, path) else "is not under"  # via a symlink
            raise InputError(f"{label} {where} the runs root {shown_root}/")
        if not path.exists():
            raise InputError(f"no file {label} under the runs root {shown_root}/")
        if path.is_dir():
            raise InputError(f"{label} is a directory; runs show takes one artifact file")
        if not path.is_file():
            raise InputError(f"{label} is not a regular file")
    except OSError as exc:
        raise InputError(f"{label}: {read_error_reason(exc)}") from exc
    spelled, base = path.absolute(), root.absolute()  # not abspath: `link/..` must stay as opened
    if ".." not in spelled.parts + base.parts and inside(root, spelled):  # the name list prints
        return spelled, base
    return Path(os.path.realpath(path)), Path(real_root)


def _version(path: Path, address: str) -> str:
    label = plain(address)
    try:
        version = head_version(path)
    except OSError as exc:
        raise InputError(f"{label}: {read_error_reason(exc)}") from exc
    if version is None:
        raise InputError(f"{label} is not an artifact: it does not open with a schema_version")
    if version not in VERSION_KINDS:
        raise InputError(f"{label} is not an artifact of a registered kind: "
                         f"schema_version {plain(version)}")
    return version


def _siblings(shown: Shown, path: Path, real_root: str) -> None:
    if not inside(real_root, os.path.realpath(path.parent)):  # reached through a link out and back
        path = Path(os.path.realpath(path))  # list the artifact's own directory, inside the root
    try:
        with os.scandir(path.parent) as listing:
            entries = sorted((e for e in listing if e.name != path.name), key=lambda e: e.name)
    except OSError as exc:
        shown.siblings_error = f"cannot list: {exc.strerror or exc}"
        return
    shown.siblings_total = len(entries)
    for entry in entries[:MAX_SIBLINGS]:
        try:
            if entry.is_dir():
                shown.siblings.append(Sibling(entry.name + "/"))
                continue
            readable = (entry.name.lower().endswith(".json") and entry.is_file()  # not a FIFO
                        and (not entry.is_symlink()
                             or inside(real_root, os.path.realpath(entry.path))))
            version = head_version(Path(entry.path)) if readable else None
        except OSError:  # a link loop, or an entry it may not stat: named, not read
            version = None
        shown.siblings.append(Sibling(entry.name, version))


def show(root: Path, address: str) -> Shown:
    """The artifact ``address`` names under ``root``, checked against its schema.

    Raises ``InputError`` when ``address`` is outside the root, missing, a directory, not an
    artifact of a registered kind, or not readable as one.
    """
    path, base = resolve(root, address)
    version = _version(path, address)
    kind = VERSION_KINDS[version]
    try:
        data = load_document(path, version)
        artifact = read_artifact(base, path, version, data)
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"{plain(address)}: {read_error_reason(exc)}") from exc
    schema = schema_version_for(kind, data)
    shown = Shown(artifact=artifact, schema=schema, errors=schema_errors(schema, data),
                  contents=contents(kind, data))
    _siblings(shown, path, os.path.realpath(root))
    return shown


@dataclass
class ShownRun:
    """What ``runs show RUN_ID`` prints: the artifacts that carry the ``run_id``, newest first,
    and the files under the root that could not be read (one of them may belong to the run)."""

    run_id: str
    artifacts: list[Artifact]
    unreadable: list[Unreadable]


def names_a_run(root: Path, address: str) -> bool:
    """Whether ``address`` may mean a run: a run id that names no file ``runs show`` could open.

    A file of that name under the root (or reached from the working directory and inside the
    root) is the artifact, and so is a name that cannot be looked up, so the path check names
    the reason. A directory of that name (a run's own, say), a path outside the root and a
    missing one do not stop it.
    """
    if not is_run_id(address):
        return False
    real_root = os.path.realpath(root)
    for path in (root / address, Path(address)):
        try:
            mode = os.stat(path).st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:  # no permission to look: the path check reports it
            return False
        if not stat.S_ISDIR(mode) and inside(real_root, os.path.realpath(path)):
            return False
    return True


def show_target(root: Path, address: str) -> Shown | ShownRun:
    """The run ``address`` names when it is a run id that names no openable file and some
    artifact under ``root`` has it; otherwise the artifact, as :func:`show` finds it.

    Raises ``InputError`` as :func:`show` does, and when ``address`` names neither a run nor
    anything on disk.
    """
    if not names_a_run(root, address):
        return show(root, address)
    result = scan(root)
    found = [a for a in result.artifacts if a.run_id == address]
    if found:
        return ShownRun(run_id=address, artifacts=found, unreadable=result.unreadable)
    if any(os.path.lexists(p) for p in (root / address, address)):
        return show(root, address)  # a directory, a path outside the root: say which
    unread = len(result.unreadable)
    note = (f"; {unread} {'file' if unread == 1 else 'files'} under the root could not be read"
            if unread else "")
    raise InputError(f"no file {plain(address)} under the runs root {plain(root.as_posix())}/, "
                     f"and no artifact there has that run_id{note}")

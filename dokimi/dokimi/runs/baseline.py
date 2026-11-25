# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Baselines: named pointers to a run, like git tags (#94 §2.3, R3).

A pointer is a file under ``<runs root>/.baselines/`` that holds one line, the ``run_id`` it
points to. The file is named by the baseline (``/`` is stored as ``%2F``, so ``skill/a@main``
is one file) and replaced in one step (:func:`dokimi.atomic_write.write_text_atomic`). Saving
and removing hold ``.baselines/.lock``, so ``--expect-previous`` is a compare-and-swap: of two
saves that expect the same run, one wins and the other finds the pointer moved. Nothing here
prints or exits.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dokimi.artifacts import is_run_id
from dokimi.atomic_write import write_text_atomic
from dokimi.runs.scan import BASELINE_POINTERS, Unreadable, inside, plain, read_error_reason, scan
from dokimi.utils import InputError

LOCK = ".lock"
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._@-]*"
_NAME = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*")
#: The run id's limit; with at most 63 ``/`` stored as ``%2F`` the file name stays under 255 bytes.
MAX_NAME = 128
_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"{p}{d}" for p in ("COM", "LPT") for d in range(10))})
_HEAD = 256  # a pointer is one run id (at most 128 characters) and a newline


def check_name(name: str) -> str:
    """``name`` when it can name a baseline: segments of letters, digits, ``.``, ``_``, ``@`` or
    ``-`` joined by ``/`` (``skill/a@main``), each starting with a letter or a digit and not
    ending in ``.``, 1-128 characters. Raises ``InputError`` otherwise."""
    if not (len(name) <= MAX_NAME and _NAME.fullmatch(name)):
        raise InputError(f"{plain(name)!r} is not a baseline name: 1-{MAX_NAME} letters, digits, "
                         "'.', '_', '@' or '-' in segments joined by '/' (skill/a@main), each "
                         "starting with a letter or a digit")
    if any(part.endswith(".") for part in name.split("/")):  # Windows drops a trailing dot
        raise InputError(f"{plain(name)!r} is not a baseline name: a segment ends in '.'")
    if _file_name(name).split(".")[0].upper() in _DEVICES:
        raise InputError(f"{plain(name)!r} is not a baseline name: Windows reserves "
                         f"{_file_name(name).split('.')[0]!r} for a device")
    return name


def _file_name(name: str) -> str:
    return name.replace("/", "%2F")


def _name_of(file_name: str) -> str:
    return file_name.replace("%2F", "/")


def store(root: Path) -> Path:
    """``<root>/.baselines``, which every read and write of the baselines goes through. Raises
    ``InputError`` when it resolves outside the root (a link out of it): nothing outside is touched."""
    directory = root / BASELINE_POINTERS
    if os.path.lexists(directory) and not inside(os.path.realpath(root), os.path.realpath(directory)):
        raise InputError(f"{plain(directory.as_posix())} leads outside the runs root "
                         f"{plain(root.as_posix())}/, so the baselines are not read or changed there")
    return directory


@contextlib.contextmanager
def locked(root: Path) -> Iterator[None]:
    """Hold ``<root>/.baselines/.lock`` (made, with the directory, if missing; ``root`` must
    exist), waiting while another process holds it. Raises ``InputError`` when the store or its
    lock cannot be made or taken (a file at ``.baselines``, no permission)."""
    directory = store(root)
    with contextlib.ExitStack() as stack:
        try:
            directory.mkdir(exist_ok=True)
            fh = stack.enter_context(open(directory / LOCK, "a+b"))
            _lock(fh.fileno())  # ENOLCK on a file system that does not lock
        except OSError as exc:
            reason = "not a directory" if isinstance(exc, FileExistsError) else exc.strerror or str(exc)
            raise InputError(f"{plain(directory.as_posix())}/: cannot lock: {reason}") from exc
        try:
            yield
        finally:
            _unlock(fh.fileno())


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:  # LK_LOCK gives up after 10 tries a second apart
                if exc.errno != errno.EDEADLOCK:
                    raise
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read(path: Path) -> str | None:
    """What the pointer at ``path`` holds, stripped; ``None`` when there is none. Raises ``OSError``
    and ``UnicodeDecodeError`` as reading does, and ``OSError`` for what is not a regular file."""
    read = _read_with_mtime(path)
    return None if read is None else read[0]


def _read_with_mtime(path: Path) -> tuple[str, float] | None:
    """:func:`_read`, and the mtime of the file it read: one open file, so both are of one save."""
    try:  # O_NONBLOCK: opening a FIFO with no writer would wait for one
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        found = os.fstat(fd)
        if not stat.S_ISREG(found.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        data = fh.read(_HEAD + 1)
        if len(data) > _HEAD:  # what follows the first 256 bytes would not be read
            raise OSError(errno.EFBIG, f"longer than a pointer ({_HEAD} bytes)")
        return data.decode("utf-8").strip(), found.st_mtime


@dataclass(frozen=True)
class Pointer:
    name: str
    run_id: str
    saved_at: float  # the pointer file's mtime: it is replaced on every save

    @property
    def saved_at_iso(self) -> str:
        return datetime.fromtimestamp(self.saved_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Listing:
    """The baselines under a root, by name, and the entries of the store that are not one."""

    pointers: list[Pointer] = field(default_factory=list)
    unreadable: list[Unreadable] = field(default_factory=list)


def _entry(directory: Path, file_name: str) -> Pointer | Unreadable:
    where = Path(BASELINE_POINTERS) / file_name
    name = _name_of(file_name)
    try:
        check_name(name)
    except InputError:
        return Unreadable(where, "not a baseline name")
    try:
        read = _read_with_mtime(directory / file_name)
        if read is None:  # listed, but gone or a link to nothing
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
    except (OSError, UnicodeDecodeError) as exc:
        return Unreadable(where, read_error_reason(exc))
    held, mtime = read
    if not is_run_id(held):
        return Unreadable(where, "holds no run id")
    return Pointer(name, held, mtime)


def _names(directory: Path) -> list[str]:
    """The entries of the store. Raises ``FileNotFoundError`` when there is none, and ``InputError``
    when it cannot be listed (no read permission): a check that needs the names cannot pass then."""
    try:
        return os.listdir(directory)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise InputError(f"{plain(directory.as_posix())}/: cannot list: {exc.strerror or exc}") from exc


def read_all(root: Path) -> Listing:
    """Every baseline under ``root``, by name. Raises ``InputError`` when the store cannot be listed."""
    directory = store(root)
    try:
        names = sorted(n for n in _names(directory) if not n.startswith("."))  # the lock, a save's tmp file
    except FileNotFoundError:
        return Listing()
    listing = Listing()
    for file_name in names:
        found = _entry(directory, file_name)
        if isinstance(found, Pointer):
            listing.pointers.append(found)
        else:
            listing.unreadable.append(found)
    listing.pointers.sort(key=lambda p: p.name)  # %2F sorts before '-', '/' after it
    return listing


def held_runs(root: Path) -> dict[str, str]:
    """The ``run_id`` each baseline points to -> the baseline's name (the first by name).

    Raises ``InputError`` when an entry of the store cannot be read, since the run it holds is
    then not known (``runs gc --keep-baselines`` removes nothing then)."""
    listing = read_all(root)
    if listing.unreadable:
        first = listing.unreadable[0]
        raise InputError(f"{plain(first.path.as_posix())} under the runs root: {plain(first.reason)}, "
                         "so the run it holds is not known")
    held: dict[str, str] = {}
    for pointer in listing.pointers:
        held.setdefault(pointer.run_id, pointer.name)
    return held


class Conflict(Exception):
    """``--expect-previous`` named a run the pointer does not hold (``found``: ``None`` when there
    is no such baseline); the pointer is left as it was."""

    def __init__(self, name: str, expected: str, found: str | None) -> None:
        self.name, self.expected, self.found = name, expected, found
        holds = ("does not exist" if found is None else f"holds {found}" if is_run_id(found)
                 else "holds no run id")
        super().__init__(f"baseline {name} {holds}, not {plain(expected)} as --expect-previous "
                         "says: another save moved it, so nothing was saved")


@dataclass(frozen=True)
class Saved:
    name: str
    run_id: str
    previous: str | None  # what the pointer held before, as read; None for a new baseline
    artifacts: int  # the run's artifacts under the root when it was saved
    path: Path  # the pointer, relative to the root


def _same_but_case(directory: Path, name: str) -> str | None:
    """A baseline whose name differs from ``name`` only in case: on Windows and macOS they would be
    one file, so they are refused everywhere."""
    names = _names(directory)  # locked() has made it
    folded = name.casefold()
    return next((n for n in map(_name_of, names) if n != name and n.casefold() == folded), None)


def _artifacts_of(root: Path, run_id: str) -> int:
    """How many artifacts under ``root`` carry ``run_id``. Raises ``InputError`` for none."""
    result = scan(root)
    found = sum(1 for a in result.artifacts if a.run_id == run_id)
    if found:
        return found
    unread = len(result.unreadable)
    note = (f"; {unread} {'entry' if unread == 1 else 'entries'} under the root could not be read"
            if unread else "")
    raise InputError(f"no artifact under the runs root {plain(root.as_posix())}/ has the run_id "
                     f"{plain(run_id)}{note}")


def save(root: Path, name: str, run_id: str, expect_previous: str | None) -> Saved:
    """Point ``name`` at ``run_id``, a run some artifact under ``root`` carries; with
    ``expect_previous``, only while the pointer holds that run.

    Raises ``InputError`` for a bad name or run id, or a run no artifact carries, and
    :class:`Conflict` when the pointer does not hold ``expect_previous``.
    """
    check_name(name)
    for flag, value in (("RUN", run_id), ("--expect-previous", expect_previous)):
        if value is not None and not is_run_id(value):
            raise InputError(f"{flag} {plain(value)!r} is not a run id")
    path = store(root) / _file_name(name)
    with locked(root):
        other = _same_but_case(store(root), name)
        if other is not None:
            raise InputError(f"baseline {name} differs from baseline {plain(other)} only in case, "
                             "which Windows and macOS read as one name")
        artifacts = _artifacts_of(root, run_id)
        try:
            previous = _read(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise InputError(f"baseline {name}: {read_error_reason(exc)}") from exc
        if expect_previous is not None and previous != expect_previous:
            raise Conflict(name, expect_previous, previous)
        write_text_atomic(path, run_id + "\n")
    return Saved(name, run_id, previous, artifacts, path.relative_to(root))


def remove(root: Path, name: str) -> str | None:
    """Remove the baseline ``name``; the run stays. Returns what it held (``None`` when that was
    not a run id). Raises ``InputError`` when there is no such baseline, and ``OSError`` when the
    pointer cannot be removed."""
    check_name(name)
    path = store(root) / _file_name(name)
    missing = InputError(f"no baseline {name} under the runs root {plain(root.as_posix())}/")
    if not store(root).is_dir():
        raise missing
    with locked(root):
        if _file_name(name) not in _names(store(root)):  # Windows and macOS would open main for MAIN
            other = _same_but_case(store(root), name)
            raise InputError(str(missing) + (f" (baseline {plain(other)} differs only in case)"
                                             if other else ""))
        try:  # None: a link to nothing, or a pointer another process removed since the listing
            held = _read(path) or ""
        except (OSError, UnicodeDecodeError):
            held = ""  # removed all the same: a pointer that cannot be read points nowhere
        try:
            path.unlink()  # a link to nothing goes: unlink does not follow it
        except FileNotFoundError:
            raise missing from None
    return held if is_run_id(held) else None

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Files the CLI reads and walks: :class:`InputError` and the reads that raise it, the device refusal before
a read, the symlink-loop test a walker needs, and the rmtree retry a cleanup needs.
"""

import errno
import json
import os
import stat
from pathlib import Path
from typing import Any


def is_symlink_loop(exc: OSError) -> bool:
    """A symlink loop: ``ELOOP``, or on Windows EINVAL with WinError 1921 (ERROR_CANT_RESOLVE_FILENAME)."""
    return exc.errno == errno.ELOOP or getattr(exc, "winerror", None) == 1921


def retry_writable(func: Any, path: str, _exc: Any) -> None:
    """``shutil.rmtree`` ``onerror`` hook: set the write bit on ``path`` and retry once.

    Windows will not delete a read-only file, and git leaves its object files
    read-only. A symlink is left alone, since chmod would follow it out of the
    tree. A retry that still fails is ignored; the caller checks what is left.
    Only the delete calls are retried: on POSIX ``rmtree`` also reports a failed
    ``os.open`` or ``os.scandir`` (an unreadable dir), which ``func(path)`` would
    call with the wrong arguments and raise ``TypeError``.
    """
    import stat

    try:
        mode = os.lstat(path).st_mode
        if not stat.S_ISLNK(mode):
            os.chmod(path, stat.S_IMODE(mode) | stat.S_IWRITE)
            if func in (os.unlink, os.remove, os.rmdir):
                func(path)
    except OSError:
        pass


class InputError(ValueError):
    """A user-supplied input that cannot be used: missing, unreadable, not UTF-8 or JSON, or the wrong shape.

    Library loaders raise it (or a subclass) with a message that names the file, and do not
    print or exit. ``cli.main`` prints ``Error: <message>`` and exits 2, the code for bad input;
    exit 1 stays the verdict of ``--strict`` gates and ``eval judge-validity``. A ``ValueError``,
    so code that already catches bad values keeps catching these.
    """


def refuse_device(path: str | os.PathLike[str], flag: str | None = None) -> None:
    """:class:`InputError` ``<flag> <path>: not a file`` for a device or a socket, before it is read whole.

    A regular file, a pipe (``<(cat x.json)``) and a directory pass; so does a path that cannot be
    stat'ed, left to the read to report. ``/dev/zero`` would otherwise be read until memory runs out
    (https://github.com/intel/dokimi/issues/504). The path is stat'ed as :class:`~pathlib.Path` spells it,
    the way the callers read it: ``/dev/zero/`` and ``/dev/zero/.`` are ``/dev/zero`` to ``Path``.
    """
    try:
        mode = Path(path).stat().st_mode  # not os.stat(path): ENOTDIR on /dev/zero/, which Path opens
    except (OSError, ValueError):  # missing, a path under a file, an embedded null byte
        return
    if not (stat.S_ISREG(mode) or stat.S_ISFIFO(mode) or stat.S_ISDIR(mode)):
        raise InputError(f"{flag} {path}: not a file" if flag else f"{path}: not a file")


def read_json_input(path: str | os.PathLike[str], *, flag: str | None = None) -> Any:
    """The parsed JSON at ``path``: :func:`read_json_input_bytes` without the bytes."""
    return read_json_input_bytes(path, flag=flag)[1]


def read_json_input_bytes(path: str | os.PathLike[str], *, flag: str | None = None) -> tuple[bytes, Any]:
    """The bytes at ``path``, read once, and the JSON they hold, or :class:`InputError` naming the file and why:
    missing or unreadable, a device (:func:`refuse_device`), not UTF-8, not JSON or nested too deeply to parse.
    With ``flag`` (the option the file was given to) the message is ``<flag> <path>: <reason>``. An empty path is
    refused first, quoted (``--answers "": no file given``), as ``Path("")`` would read the current directory."""
    if os.fspath(path) == "":
        raise InputError(f'{flag} "": no file given' if flag else 'cannot read "": no file given')
    refuse_device(path, flag)
    try:
        raw = Path(path).read_bytes()
        return raw, json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise InputError(f"{flag} {path}: {exc}" if flag else f"{path} is not UTF-8 text: {exc}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:  # deep nesting is named as bad JSON, as on main
        raise InputError(f"{flag} {path}: {exc}" if flag else f"{path} is not valid JSON: {exc}") from exc
    except (OSError, ValueError) as exc:  # ValueError: e.g. "embedded null byte" in the path, from pathlib
        reason = getattr(exc, "strerror", None) or exc
        raise InputError(f"{flag} {path}: {reason}" if flag else f"cannot read {path}: {reason}") from exc

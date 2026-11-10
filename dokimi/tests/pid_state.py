# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether a pid still runs, and what it is, read from /proc (Linux), for tests that signal a process tree."""

from __future__ import annotations

import os
from pathlib import Path


def pid_state(pid: int) -> str | None:
    """The state letter of a pid in /proc (``R``, ``S``, ``Z``, ``X``...), ``"?"`` where /proc
    cannot say, None when the pid does not exist (any more)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except OSError:
        return "?"
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:  # reaped between kill() and the read, or no /proc here (macOS): ask kill() again
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except OSError:
            pass
        return "?"


def pid_alive(pid: int) -> bool:
    """A killed child nobody reaped yet is a zombie (Z), one being reaped is dead (X): not running."""
    return pid_state(pid) not in (None, "Z", "X")


def describe(pid: int) -> str:
    """State, ppid, pgrp and cmdline of a pid, for the message of a failed assertion."""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return f"pid {pid} state {stat[0]} ppid {stat[1]} pgrp {stat[2]} cmdline {cmd!r}"
    except OSError:
        return f"pid {pid} state {pid_state(pid)}"

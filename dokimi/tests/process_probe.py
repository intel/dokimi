# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Look at a process by pid through /proc, for tests that check a kill reached it."""

from __future__ import annotations

import os
from pathlib import Path


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a killed child nobody reaped yet is a zombie: not running
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def describe_pid(pid: int) -> str:
    """The pid with its /proc stat (state, ppid, pgrp) and cmdline, for a failure message."""
    try:
        stat, cmdline = (Path(f"/proc/{pid}/{name}").read_bytes() for name in ("stat", "cmdline"))
    except OSError as exc:
        return f"pid {pid} (no /proc entry: {exc.__class__.__name__})"
    args = cmdline.replace(b"\0", b" ").strip()  # no backslash inside an f-string before Python 3.12
    return f"pid {pid} stat={stat.strip()!r} cmdline={args!r}"

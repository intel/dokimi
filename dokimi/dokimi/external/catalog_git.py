# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Local source and NUL-delimited impact inputs for a static catalog check."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dokimi import process


def _git(root: Path, *args: str) -> process.Completed:
    # The checkout is data: even a locally configured fsmonitor must not run.
    return process.git(
        "--no-pager", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
        *args, cwd=root, env=process.Env.inherit({
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }),
    )


def _paths(done: process.Completed) -> list[str]:
    if done.returncode:
        raise ValueError(f"cannot read changed paths: {process.git_failure(done)}")
    # Git's bytes are filesystem names, not shell commands or line-delimited text.
    return [os.fsdecode(path) for path in done.stdout_bytes.split(b"\0") if path]


def source_and_changes(root: Path, base: str | None, out: Path) -> tuple[dict[str, Any], list[str] | None, str | None]:
    """Source identity, working-tree changes, and explicit conservative-fallback reason.

    An absent/unresolvable base selects everything; a failed diff is an input error.
    Tracked edits/deletions and untracked files are included, so the receipt describes
    the bytes checked even before the caller commits them. Output files are excluded.
    """
    filters = _git(root, "config", "--includes", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$")
    if filters.returncode == 0:
        raise ValueError("the checkout configures git content filters, which would run on inspection: "
                         + ", ".join(sorted({line.split(".")[1] for line in filters.stdout.splitlines()})))
    if filters.returncode != 1:
        raise ValueError(f"cannot read git config: {process.git_failure(filters)}")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    source: dict[str, Any] = {"commit": None, "dirty": None}
    if head.returncode == 0:
        source["commit"] = head.stdout.strip()
        status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
        if status.returncode:
            raise ValueError(f"cannot read source state: {process.git_failure(status)}")
        source["dirty"] = bool(status.stdout_bytes)
    if base is None:
        return source, None, None
    resolved = _git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    if resolved.returncode:
        return source, None, f"Base {base!r} unavailable; conservatively checking every skill."
    tracked = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z",
                   "--no-renames", "--relative", resolved.stdout.strip(), "--", ".")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", ".")
    paths = sorted(set(_paths(tracked) + _paths(untracked)))
    return source, [path for path in paths if not (root / path).is_relative_to(out)], None

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Local source and NUL-delimited impact inputs for a static catalog check."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dokimi import process
from dokimi.skills.loader import VENDORED_DIR_NAMES


def _git(root: Path, *args: str) -> process.Completed:
    # The checkout is data: even a locally configured fsmonitor must not run.
    # A caller's GIT_DIR (as in a hook) must not swap in another repository.
    return process.git(
        "--no-pager", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
        *args, cwd=root, env=process.Env.only([k for k in os.environ if k not in process.GIT_REPO_OVERRIDES], {
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }),
    )


def _paths(done: process.Completed) -> list[str]:
    if done.returncode:
        raise ValueError(f"cannot read changed paths: {process.git_failure(done)}")
    # Git's bytes are filesystem names, not shell commands or line-delimited text.
    return [os.fsdecode(path) for path in done.stdout_bytes.split(b"\0") if path]


def _others(root: Path) -> list[str]:
    """Untracked files, ignored ones included, since bundle digests hash them; vendored directories are not hashed."""
    done = _git(root, "ls-files", "--others", "-z", "--", ".")
    return [path for path in _paths(done) if not VENDORED_DIR_NAMES.intersection(Path(path).parts[:-1])]


def source_and_changes(root: Path, base: str | None, out: Path) -> tuple[dict[str, Any], list[str] | None, str | None]:
    """Source identity, working-tree changes, and explicit conservative-fallback reason.

    An absent/unresolvable base selects everything; a failed diff is an input error.
    Tracked edits/deletions and untracked files are included, so the receipt describes
    the bytes checked even before the caller commits them. Output files are excluded.
    """
    filters = _git(root, "config", "--includes", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$")
    if filters.returncode == 0:
        raise ValueError("the checkout configures git content filters, which would run on inspection: "
                         + ", ".join(sorted({key.removeprefix("filter.").rsplit(".", 1)[0] for key in filters.stdout.splitlines()})))
    if filters.returncode != 1:
        raise ValueError(f"cannot read git config: {process.git_failure(filters)}")
    gitlinks = _git(root, "ls-files", "-z", "--stage", "--", ".")
    if gitlinks.returncode == 0 and any(entry.startswith(b"160000 ") for entry in gitlinks.stdout_bytes.split(b"\0")):
        raise ValueError("the checkout contains git submodules, whose filters cannot be ruled out")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    source: dict[str, Any] = {"commit": None, "dirty": None}
    if head.returncode == 0:
        source["commit"] = head.stdout.strip()
        status = _git(root, "status", "--porcelain=v1", "-z", "--ignore-submodules=all", "--untracked-files=all", "--", ".")
        if status.returncode:
            raise ValueError(f"cannot read source state: {process.git_failure(status)}")
        source["dirty"] = bool(status.stdout_bytes) or bool(_others(root))
    if base is None:
        return source, None, None
    resolved = _git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    if resolved.returncode:
        return source, None, f"Base {base!r} unavailable; conservatively checking every skill."
    tracked = _git(root, "diff", "--ignore-submodules=all", "--no-ext-diff", "--no-textconv", "--name-only", "-z",
                   "--no-renames", "--relative", resolved.stdout.strip(), "--", ".")
    paths = sorted(set(_paths(tracked) + _others(root)))
    return source, [path for path in paths if not (root / path).is_relative_to(out)], None

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reward a task's verifier leaves in ``/logs/verifier``: ``reward.json`` or ``reward.txt``.

Harbor's rules, as of Harbor 0.24.0 (``harbor/verifier/verifier.py``, ``Verifier.verify``):
``reward.json`` is read when it exists, else ``reward.txt``. ``reward.json`` is a JSON object
whose values are all finite numbers, one per named reward; the trial's reward is its
``reward`` key, or its only key (``harbor/cli/jobs.py`` ``_primary_reward``,
``harbor/metrics/base.py`` ``aggregate_reward_dicts``). A reward outside [0, 1] is no reward
here, as in a Harbor trial file (:func:`dokimi.harnesses.command._harbor_reward`).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional

from dokimi import process
from dokimi.docker_cmd import Docker
from dokimi.utils import retry_writable

#: Harbor's order: ``reward.json`` first, ``reward.txt`` only when there is none.
REWARD_FILES = ("reward.json", "reward.txt")

_REWARD_RE = re.compile(r"(?:reward|score|pass[_ -]?rate)\D+([01](?:\.\d+)?)", re.IGNORECASE)


def reward_in_file(name: str, text: str) -> float:
    """The reward in the verifier's reward file *name*; :class:`ValueError` says why there is none."""
    if name == "reward.json":
        return _json_reward(text)
    reward = _parse_reward_text(text, reward_file=True)
    if reward is None:
        raise ValueError("has no reward between 0 and 1 in it")
    return reward


def primary_reward(rewards: Mapping[str, Any]) -> Any:
    """The value Harbor takes as a trial's reward: its ``reward`` key, else its only key, else None."""
    if "reward" in rewards:
        return rewards["reward"]
    return next(iter(rewards.values())) if len(rewards) == 1 else None


def _json_reward(text: str) -> float:
    try:
        rewards = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"is not JSON ({exc})") from None
    if not isinstance(rewards, dict):
        raise ValueError("is not a JSON object of rewards")
    # Harbor refuses the whole file over one bad value, so a cell here is errored for it too.
    for key, value in rewards.items():
        if not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"has a reward {key!r} that is not a finite number: {value!r}")
    value = primary_reward(rewards)
    if value is None:
        raise ValueError(f"has no 'reward' key among {len(rewards)} rewards")
    # bool is an int subclass: true is not a reward.
    if isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValueError(f"has a reward that is not between 0 and 1: {value!r}")
    return float(value)


def pull_reward(docker: Docker, container: str, output_dir: Path, log: Any) -> tuple[Optional[float], Optional[str]]:
    """The reward in the first reward file the verifier wrote, in Harbor's order, copied into *output_dir*.

    ``(None, None)`` when it wrote none; ``(None, why)`` when a copy failed, which stops the search:
    a later file may not stand in for one that could not be read. Raises :class:`RuntimeError` when
    the first file there is not a regular file or holds no reward.
    """
    clear_reward_files(output_dir)
    for name in REWARD_FILES:
        dest = output_dir / name
        pulled = docker(["cp", f"{container}:/logs/verifier/{name}", str(dest)], log, check=False)
        # A copied link would be read on the host (is_file() follows it), its target not the container's.
        if pulled.returncode == 0 and dest.is_file() and not dest.is_symlink():
            return reward_file_verdict(name, dest.read_text(encoding="utf-8", errors="replace")), None
        if pulled.returncode == 0 and (dest.is_symlink() or dest.exists()):
            kind = "symbolic link" if dest.is_symlink() else "directory"
            clear_reward_files(output_dir)
            raise RuntimeError(f"/logs/verifier/{name} is not a file but a {kind}; see run.log")
        failed = _copy_error(pulled, name)
        if failed:
            return None, failed
    return None, None


def clear_reward_files(output_dir: Path) -> None:
    """Remove the cell's copies of the reward files: one left by an earlier run is not this verdict."""
    for name in REWARD_FILES:
        path = output_dir / name
        if path.is_dir() and not path.is_symlink():
            _remove_tree(path)
        else:
            path.unlink(missing_ok=True)


def _remove_tree(path: Path) -> None:
    """``shutil.rmtree`` after making each directory writable: docker cp keeps a verifier's ``chmod 555``."""
    path.chmod(0o700)
    for root, dirs, _files in os.walk(path):  # top-down: a directory is writable before it is entered
        for name in dirs:
            if not os.path.islink(os.path.join(root, name)):
                os.chmod(os.path.join(root, name), 0o700)
    shutil.rmtree(path, onerror=retry_writable)  # a read-only file on Windows
    if path.exists():  # retry_writable ignores a retry that fails, and a stale tree would take the next copy
        raise RuntimeError(f"could not remove {path}, left by an earlier run; see run.log")


def exit_code_verdict(returncode: int) -> float:
    """The verdict of a reward-file verifier that wrote no reward file.

    Exit 0 is not a verdict: a verifier that wrote no reward file passed nothing,
    so the cell is errored, as a verifier that did not run is. Non-zero stays 0.0.
    """
    if returncode == 0:
        raise RuntimeError(
            "verifier exited 0 but wrote no /logs/verifier/reward.txt or reward.json; see run.log")
    return 0.0


def reward_file_verdict(name: str, text: str) -> float:
    """The reward in the verifier's reward file *name*; one with no reward in it is no verdict.

    As in :func:`dokimi.harnesses.command._exit_code_reward`, whatever the exit code: the cell is errored, not failed.
    """
    try:
        return reward_in_file(name, text)
    except ValueError as exc:
        raise RuntimeError(f"/logs/verifier/{name} {exc}; see run.log") from None


# What docker says when the file is not there (v20+, older CLIs, podman); a daemon error says none.
_CP_NO_FILE_RE = re.compile(
    r"could not find the file|no such container:path|could not be found on container", re.I)


def _copy_error(pulled: process.Completed, name: str) -> Optional[str]:
    """Why ``docker cp`` of reward file *name* failed; None when it worked or found no file there.

    A copy that failed or hung says nothing about the verifier, so it is not "no reward file".
    """
    if pulled.timed_out:
        return f"docker cp of /logs/verifier/{name} timed out; see run.log"
    if pulled.returncode != 0 and not _CP_NO_FILE_RE.search(pulled.stderr or ""):
        return f"docker cp of /logs/verifier/{name} failed (exit {pulled.returncode}); see run.log"
    return None


def _parse_reward_text(text: str, *, reward_file: bool = False) -> float | None:
    """A reward from a ``reward.txt`` (``reward_file``) or from command output.

    In a reward file a bare number is the whole reward, so partial credit in
    [0, 1] is accepted (SkillsBench's dialogue-parser writes the passing-test
    fraction). On stdout/stderr only an exact 0/1 or a labelled
    ``reward: x`` counts: a command that prints ``0.5`` for some other reason
    keeps falling back to its exit code, as it always has.
    """
    stripped = text.strip()
    if stripped in {"0", "0.0", "1", "1.0"}:
        return float(stripped)
    if reward_file:
        try:
            value = float(stripped)
        except ValueError:
            value = None
        if value is not None and 0.0 <= value <= 1.0:
            return value
    match = _REWARD_RE.search(text)
    if match:
        return float(match.group(1))
    return None

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The batch ledger of ``repo run``: what identifies a cell, and what a batch directory already holds.

A batch directory holds the cells of one or more invocations of ``repo run``. The rules that decide which
cells belong together live here:

- the **execution id** of a cell (:func:`execution_id`): how its agent is started, which is the harness
  profile's command line, environment, config and prompt suffix, plus the executable's digest and version.
  A finished cell is reused only by the same execution (:func:`reused_result`), and a batch holds the cells
  of one execution only (:func:`check_batch_executions`);
- the **cell name** (:func:`cell_name`) and the ``execution`` record that ``meta.json`` and ``result.json``
  carry (:func:`execution_record`);
- the **pins** a batch keeps in ``_eval/``: the trees of the skills a skill arm installs
  (:func:`pin_skills`), so a resume cannot change what an arm means; :func:`pin_arms` resolves each arm
  once per run and records nothing;
- the **replace check** (:func:`check_batch_executions`): ``--force`` rewrites the cells of its plan and no
  other cell; the plan itself is chosen by the runner.

Changing any of these changes an existing batch's identity: a batch written before the change is refused
or re-run. The golden tests in ``tests/test_batch_ledger_golden.py`` pin the current values.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .arms import resolve_skills, slug, tree_sha256
from .bases import NOT_UTF8
from .errors import PrepError
from .harness_profile import HarnessProfile
from .source import RepoSource
from .workspace import Arm, parse_arm, pin_arm

RERUN_HINT = "remove it to run the cell again, or rerun with --force, which replaces every cell of this run's plan"
SKILL_PINS = "skills.json"  # in the batch's ``_eval``
PLAN = "plan.json"  # in the batch's ``_eval``


class ExecutionMismatch(PrepError):
    """A finished cell of the batch ran with another execution than the one asked for."""


class UnreadableResult(PrepError):
    """A finished cell's ``result.json`` is not a JSON object: it is reported, not overwritten or rerun."""


def cell_name(task: str, arm: str, model: str, rep: int) -> str:
    return f"{task}__{slug(arm.replace(':', '-'))}__{slug(model)}__r{rep}"


def harness_sha256(path: Path) -> str:
    """Digest of the harness executable's bytes: the same file gives the same id wherever it lives."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execution_id(profile: HarnessProfile, tools: str, delegation: str = "native", harness_sha256: str | None = None,
                 harness_version: str | None = None) -> str:
    """Short hash of how the agent is started: the profile's ``invoke`` template with its tool arguments,
    environment, config, prompt suffix, egress and bridge.

    Two runs with the same id ran the same command line up to the per-cell values (prompt, model,
    caps); ``repo run`` reuses a finished cell only when its id matches. Includes the harness
    executable's digest and version to detect changes to the effective harness, wherever it lives.
    """
    key = {"profile": profile.name, "invoke": list(profile.invoke), "tools": list(profile.tool_args(tools, delegation)),
           "args_if_present": {n: list(a) for n, a in sorted(profile.args_if_present.items())},
           "env_set": dict(sorted(profile.env_set.items())), "env_allow": list(profile.env_allow),
           "config_dir_env": profile.config_dir_env, "config_files": dict(sorted(profile.config_files.items())),
           "egress": list(profile.egress), "bridge": profile.bridge,
           "prompt_suffix": profile.prompt_suffix, "harness_sha256": harness_sha256,
           "harness_version": harness_version}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def execution_record(profile: HarnessProfile, tools: str, delegation: str, harness_sha256: str,
                     harness_version: str | None) -> dict[str, str]:
    """How a cell's agent was started, as ``meta.json`` and ``result.json`` record it."""
    return {"id": execution_id(profile, tools, delegation, harness_sha256, harness_version),
            "tools": tools, "delegation": profile.effective_delegation(tools, delegation)}


def recorded_execution(result: Any) -> dict[str, Any]:
    """The ``execution`` record of a parsed ``result.json``; empty when there is none or it is not an object."""
    ran = result.get("execution") if isinstance(result, dict) else None
    return ran if isinstance(ran, dict) else {}


def finished_result(path: Path, recover: str = RERUN_HINT) -> dict[str, Any]:
    """The parsed ``result.json`` of a finished cell, or :class:`UnreadableResult` naming the file and ``recover``."""
    try:
        done = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        problem = NOT_UTF8
    except ValueError as exc:
        problem = f"not valid JSON ({exc})"
    else:
        if isinstance(done, dict):
            return done
        problem = "not a JSON object"
    raise UnreadableResult(f"{path}: {problem}; {recover}")


def is_done(cell_dir: Path) -> bool:
    """A cell with a ``result.json`` is finished; one without is missing (it never finished), not failed."""
    return (cell_dir / "result.json").is_file()


def reused_result(cell_dir: Path, execution: dict[str, str], force: bool) -> dict[str, Any] | None:
    """The finished record this run keeps for ``cell_dir``, or None when the cell runs.

    A finished cell runs again only with ``force``. Otherwise it must have been finished by the same
    execution: a result from before execution ids carries none, so nothing says how it ran, and it is refused."""
    if force or not is_done(cell_dir):
        return None
    done = finished_result(cell_dir / "result.json")
    ran = recorded_execution(done)
    if ran.get("id") != execution["id"]:
        raise ExecutionMismatch(
            f"the cell finished with another execution ({ran.get('id', 'unrecorded')}, delegation "
            f"{ran.get('delegation', 'native')}) than this run ({execution['id']}, delegation "
            f"{execution['delegation']}); use another --batch, or --force to replace it")
    return done


def check_batch_executions(batch_dir: Path, wanted: set[str], replacing: set[str] | None = None) -> None:
    """Refuse, before anything runs, a batch dir whose finished cells ran with another execution than ``wanted``.

    ``replacing`` (the cells of a ``--force`` run) are skipped: ``--force`` rewrites those, but not the others,
    which would stay in the report next to the new ones. A cell whose result cannot be read has no execution
    to compare; a cell of this run's plan reports it when it runs, so it is not an error here."""
    found: set[str] = set()
    for path in sorted(batch_dir.glob("*/result.json")):
        if replacing is not None and path.parent.name in replacing:
            continue
        try:
            ran = recorded_execution(finished_result(path))
        except (OSError, UnreadableResult):
            # No execution to compare. A cell of this run's plan reports it when it runs; one outside the plan
            # is not read again.
            continue
        found.add(ran["id"] if isinstance(ran.get("id"), str) else "unrecorded")
    other = found - wanted
    if other:
        raise ExecutionMismatch(
            f"{batch_dir} holds cells of another execution ({', '.join(sorted(other))}) than this run "
            f"({', '.join(sorted(wanted))}); use another --batch, or --force to replace them"
            + (" (--force replaces only the cells of this run's plan)" if replacing is not None else ""))


def record_plan(batch_dir: Path, names: Iterable[str]) -> None:
    """Union of every cell any invocation planned, so the report can count cells that never started."""
    path = batch_dir / "_eval" / PLAN
    path.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(path.read_text(encoding="utf-8")).get("cells", []) if path.is_file() else []
    write_json(path, {"cells": sorted(set(old) | set(names))})


def pin_skills(batch_dir: Path, arms: list[str], content_dir: Path) -> dict[str, list[list[str]]] | None:
    """The batch's skill pins with this run's skill arms added (``None``: nothing to add): arm name ->
    [skill name, tree sha256] of each skill. A skill arm is named by its directories, so a resume with another
    path or edited content would add cells of other skills to the arm's row; that is refused."""
    now = {str(a): [[s.name, tree_sha256(s.dir)] for s in resolve_skills(a, content_dir)]
           for a in map(parse_arm, arms) if a.skills}
    path = batch_dir / "_eval" / SKILL_PINS
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    for arm, skills in now.items():
        if old.get(arm, skills) != skills:
            was, new = (", ".join(f"{name} {sha[:12]}" for name, sha in pin) for pin in (old[arm], skills))
            raise ValueError(f"batch {batch_dir.name} ran arm {arm} with skills {was}, not {new}; "
                             f"use another --batch")
    return {**old, **now} if now.keys() - old.keys() else None


def write_skill_pin(batch_dir: Path, pin: dict[str, list[list[str]]]) -> None:
    (batch_dir / "_eval" / SKILL_PINS).write_text(json.dumps(pin, sort_keys=True) + "\n", encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    """Through a new file and a rename: a link a grader left at ``path`` is replaced, never followed."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def pin_arms(arms: list[Arm], git_dir: Path, source: str, log: Callable[[str], None] = _stderr) -> list[str]:
    """Each arm under the name its cells get, resolved once for the whole batch (a revision to its
    full SHA, so a branch that moves later names another arm); two spellings of one arm run once."""
    parsed = RepoSource.parse(source)
    remote = parsed.remote
    worktree = None if remote else Path(parsed.location)  # HEAD of a linked worktree is not the main one's
    pinned: list[str] = []  # Arm objects: run_batch hands each to prepare_workspace
    first_path: dict[str, str | None] = {}
    first_skills: dict[str, tuple[tuple[str, str], ...]] = {}
    for arm in arms:
        named = pin_arm(arm, git_dir, fetch=remote, worktree_dir=worktree)
        if first_skills.setdefault(named, named.skills) != named.skills:  # skill arms are named by directory
            raise ValueError(f"arms {named.spec!r} and another spelling of {named!r} name different skill paths "
                             f"({named.skills} vs {first_skills[named]}) but would share one cell; "
                             f"rename a directory or spell the path alike")
        if named != named.spec:
            detail = f"{named.patch_path}, sha256 {named.patch_sha256}" if named.patch is not None else named.spec
            log(f"arm {named} = {detail}")
        first = first_path.setdefault(named, named.patch_path)
        if first != named.patch_path:  # same bytes under another path: one cell, recorded under the first path
            log(f"arm {named}: {named.patch_path} and {first} are the same patch and share one cell; "
                f"meta.json names {first}")
        pinned.append(named)
    return list(dict.fromkeys(pinned))

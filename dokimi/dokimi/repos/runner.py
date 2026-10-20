# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo run``: one agent per (task, arm, model, rep) cell, then ``grade`` (contract §1.1, §4).

A cell is a run directory under the batch directory:

- the workspace is prepared on the host (:mod:`.workspace`) and ``meta.json``
  written;
- the agent runs in its own container, which mounts only the workspace, the
  per-run ``home``, ``config`` and scratch directories (those ``image.env``
  places under ``{run_dir}``), the caches, the harness executable and the
  framework's helper scripts, all at the fixed paths. ``{eval_dir}`` and
  ``{source_dir}`` are never mounted;
- after it exits, its trace becomes ``events.jsonl`` and ``answer.txt``, and
  ``task.json`` is copied in;
- ``grade`` runs in a separate container and the framework's verdict goes to
  ``result.json``. A cell with ``result.json`` is done and is skipped unless
  ``force``; a cell without one is *missing* (it never finished), not failed.
- a cell whose task's ``host`` requirements this host does not meet
  (:mod:`.host`) is not run: its directory holds only ``unsupported.json``
  with the reason, and it is neither failed nor missing. A later run on a host
  that meets them runs it.

The agent must reach its model. The contract gives it one route, a proxy that
allows only the harness profile's ``egress`` hosts; until that proxy exists a
run needs an explicit ``agent_network`` (``host`` or ``bridge``), which is
recorded in ``meta.json`` and flagged by the report.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .container import (
    HARNESS_DIR,
    RUN_DIR,
    SUPPORT_DIR,
    WORKSPACE,
    ContainerError,
    ContainerSpec,
    EgressNotImplemented,
    Mount,
    docker_network,
)
from .events import convert
from .grading import EvalContext, failed_outcome, outcome, run_entry
from .harness_profile import HarnessProfile
from .host import probe_host, unmet, validate_tasks
from .manifest import scratch_name
from .workspace import PrepError, Task, prepare_workspace

SUPPORT_HOST = Path(__file__).resolve().parent / "support"
CONFIG_DIR = f"{RUN_DIR}/config"
# Host values that mean nothing inside the image: the image's PATH and the per-run HOME win.
_HOST_ONLY_ENV = frozenset({"PATH", "HOME", "SYSTEMROOT"})
_MOUNTED_ENTRIES = frozenset({"workspace", "home", "config"})  # mounted whatever image.env says


@dataclass
class AgentOptions:
    profile: HarnessProfile
    model: str
    harness_bin: Path              # host executable, mounted read-only at /eval/harness/<name>
    agent_network: str | None = None  # explicit opt-in while the egress proxy is missing
    harness_version: str | None = None
    tools: str = "all"
    timeout_s: float = 2700
    max_turns: int = 200
    budget_usd: float = 2.0


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "x"


def cell_name(task: str, arm: str, model: str, rep: int) -> str:
    return f"{task}__{slug(arm.replace(':', '-'))}__{slug(model)}__r{rep}"


def agent_network(opts: AgentOptions, ctx: EvalContext, task: Task) -> str:
    """The agent container's network: offline only when nothing needs egress, else the explicit opt-in."""
    needs = tuple(opts.profile.egress) + task.egress
    network = ctx.manifest.image.network if ctx.manifest.image else "none"
    if not needs or network == "public":
        return opts.agent_network or docker_network(network)
    if opts.agent_network:
        return opts.agent_network
    raise EgressNotImplemented(
        f"egress proxy not implemented: the {opts.profile.name} agent must reach {', '.join(needs)} "
        "through the contract §2 proxy. Pass --agent-network host (or bridge) to give the agent's "
        "container that network instead; it is recorded in meta.json and flagged in the report.")


def agent_layout(ctx: EvalContext, run_dir: Path, task: Task) -> tuple[list[Mount], dict[str, str]]:
    """Mounts and environment of the agent's container, without the harness itself."""
    run_dir = run_dir.resolve()
    mounts = [Mount(run_dir / "workspace", WORKSPACE)]
    for sub in ("home", "config", *scratch_dirs(ctx)):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
        mounts.append(Mount(run_dir / sub, f"{RUN_DIR}/{sub}"))
    return mounts + ctx.cache_mounts(), ctx.base_env(task)


def scratch_dirs(ctx: EvalContext) -> list[str]:
    """Top-level run-dir entries ``image.env`` points at (``{run_dir}/ob`` -> ``ob``): per-run state."""
    out: list[str] = []
    for key, value in (ctx.manifest.image.env if ctx.manifest.image else {}).items():
        try:
            name = scratch_name(value)
        except ValueError as exc:  # parse_manifest refuses it too; this guards a manifest built another way
            raise PrepError(f"image.env {key}: {exc}") from exc
        if name and name not in _MOUNTED_ENTRIES and name not in out:
            out.append(name)
    return out


def task_caps(task: Task, opts: AgentOptions) -> dict[str, float]:
    """The agent's timeout, turn and USD caps: ``task.json``'s, else the run's. Each must be finite and > 0."""
    caps = {}
    for key, default in (("timeout_s", opts.timeout_s), ("max_turns", opts.max_turns),
                         ("budget_usd", opts.budget_usd)):
        value = task.raw.get(key)
        value = default if value is None else value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            # NaN would make charged_usd, and so the batch total, NaN: --max-batch-usd would never trip
            raise PrepError(f"task {task.id}: {key} must be a positive, finite number, got {value!r}")
        caps[key] = float(value)
    caps["max_turns"] = int(caps["max_turns"])
    return caps


def run_cell(ctx: EvalContext, task: Task, arm: str, rep: int, batch_dir: Path, opts: AgentOptions,
             force: bool = False, host: dict[str, Any] | None = None) -> dict[str, Any]:
    """Prepare, run the agent, grade. Returns the cell's ``result.json`` (``skipped`` when it existed).

    ``host`` (the probed facts of :func:`.host.probe_host`) is recorded in ``meta.json``."""
    rd = batch_dir / cell_name(task.id, arm, opts.model, rep)
    if (rd / "result.json").is_file() and not force:
        return {**json.loads((rd / "result.json").read_text(encoding="utf-8")), "skipped": True}
    network = agent_network(opts, ctx, task)
    grade_network = docker_network(ctx.manifest.image.network if ctx.manifest.image else "none", task.egress)
    caps = task_caps(task, opts)
    if rd.exists():
        _rmtree(rd)
    rd.mkdir(parents=True)
    meta = prepare_workspace(ctx.git_dir, ctx.manifest, task, arm, rd, opts.profile)
    meta.update(harness=opts.profile.name, harness_version=opts.harness_version, model=opts.model, rep=rep,
                batch=batch_dir.name, agent_network=network, egress_proxy=False, host=host)
    _write_json(rd / "meta.json", meta)

    mounts, env = agent_layout(ctx, rd, task)
    bin_in = f"{HARNESS_DIR}/{opts.harness_bin.name}"
    mounts += [Mount(opts.harness_bin.resolve(), bin_in, read_only=True),
               Mount(SUPPORT_HOST, SUPPORT_DIR, read_only=True)]
    harness_env = opts.profile.env(Path(CONFIG_DIR)).resolve()
    # Allow-listed host values (credentials among them) go by name only; the rest are the framework's.
    passed = tuple(k for k in opts.profile.env_allow
                   if k in harness_env and k not in _HOST_ONLY_ENV and harness_env[k] == os.environ.get(k))
    env.update({k: v for k, v in harness_env.items() if k not in _HOST_ONLY_ENV and k not in passed})
    env["DOKIMI_HOOK_LOG"] = f"{CONFIG_DIR}/hook.log"
    opts.profile.write_config(rd / "config", {"config_dir": CONFIG_DIR, "support_dir": SUPPORT_DIR})
    argv = opts.profile.argv(prompt=opts.profile.prompt(str(task.raw["prompt"])), model=opts.model,
                             tools=opts.tools, max_turns=int(caps["max_turns"]), budget_usd=caps["budget_usd"],
                             bin_path=bin_in)
    spec = ContainerSpec(image=ctx.image, argv=argv, workdir=WORKSPACE, mounts=mounts, env=env, pass_env=passed,
                         network=network, name=container_name(rd.name, "agent"))
    result = ctx.runner.run(spec, stdout=rd / "agent.stdout", stderr=rd / "agent.stderr",
                            timeout_s=caps["timeout_s"])

    # The agent has run and may have spent its cap: from here on the cell is graded, never dropped.
    agent: dict[str, Any] = {
        "rc": result.returncode, "timed_out": result.timed_out, "wall_s": result.wall_s, "cost_usd": None,
        # A killed harness reports no cost; the batch budget counts the cell's cap instead of $0.
        "charged_usd": caps["budget_usd"], "num_turns": None, "is_error": None, "result_subtype": None,
        "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
        "cache_write_tokens": None, "usage_scope": None, "subagent_calls": None,
        "plugins": [], "over_budget": False, "caps": caps, "network": network, "trace_errors": []}
    try:
        raw = (rd / "agent.stdout").read_text(encoding="utf-8", errors="replace")
        transcript = convert(opts.profile.trace, raw, WORKSPACE)
        (rd / "events.jsonl").write_text(transcript.events_jsonl(), encoding="utf-8")
        (rd / "answer.txt").write_text(transcript.answer, encoding="utf-8")
        _write_json(rd / "task.json", task.raw)
        meta["harness_version"] = transcript.harness_version or opts.harness_version
        _write_json(rd / "meta.json", meta)
        cost = transcript.cost_usd
        agent.update(
            cost_usd=cost, charged_usd=cost if cost is not None else caps["budget_usd"],
            num_turns=transcript.num_turns, is_error=transcript.is_error,
            result_subtype=transcript.result_subtype, input_tokens=transcript.input_tokens,
            output_tokens=transcript.output_tokens, cache_read_tokens=transcript.cache_read_tokens,
            cache_write_tokens=transcript.cache_write_tokens, usage_scope=transcript.usage_scope,
            subagent_calls=transcript.subagent_calls, plugins=transcript.plugins,
            over_budget=cost is not None and cost > caps["budget_usd"], trace_errors=transcript.errors[:5])
        graded = run_entry(ctx, "grade", task, rd, grade_network, container_name(rd.name, "grade"))
        grade = outcome(task, rd, graded)
    except Exception as exc:  # noqa: BLE001  # the grade run failed to start, or the host failed around it
        # What the agent left behind (a read-only .git, a hostile trace) can break any host step after it.
        # Excluding the run would favour arms that break workspaces (contract §4.2): it fails the task.
        what = "grade run failed" if isinstance(exc, ContainerError) else f"host error after the agent ran ({type(exc).__name__})"
        grade = failed_outcome(f"{what}: {str(exc)[:300]}")
    record = {"task": task.id, "family": task.family, "arm": arm, "model": opts.model, "rep": rep,
              "harness": opts.profile.name, "harness_version": meta["harness_version"],
              "base_date": meta["base_date"], "score_task": bool(task.score), "agent": agent, "grade": grade}
    try:
        _own_dir(rd)  # the grader had the run dir read-write, and may have taken write access away
        _write_json(rd / "result.json", record)
    except OSError as exc:
        raise CellCrash(f"cannot write result.json: {exc}", float(agent["charged_usd"])) from exc
    return record


class CellCrash(Exception):
    """A cell whose agent ran but whose ``result.json`` could not be written: its cost still counts."""

    def __init__(self, message: str, charged_usd: float):
        super().__init__(message)
        self.charged_usd = charged_usd


def _own_dir(path: Path) -> None:
    mode = os.lstat(path).st_mode
    if stat.S_ISDIR(mode) and mode & stat.S_IRWXU != stat.S_IRWXU:
        os.chmod(path, stat.S_IMODE(mode) | stat.S_IRWXU)


def run_batch(ctx: EvalContext, tasks: list[Task], arms: list[str], options: list[AgentOptions], reps: int,
              batch_dir: Path, jobs: int = 1, force: bool = False, max_batch_usd: float | None = None,
              log=print) -> dict[str, Any]:
    """Every (task, arm, model, rep) cell; returns counts. Cells that crash are reported, not raised.

    The host is probed once, before any cell (``_eval/host.json``); a cell of a task whose ``host``
    requirements it does not meet is marked unsupported instead of run. One that already has a
    ``result.json`` (from a host that met them) keeps it, even with ``force``."""
    validate_tasks(tasks)
    cells = [(t, a, o, r) for t in tasks for a in arms for o in options for r in range(1, reps + 1)]
    _record_plan(batch_dir, [cell_name(t.id, a, o.model, r) for t, a, o, r in cells])
    host = probe_host(ctx, batch_dir / "_eval")
    _write_json(batch_dir / "_eval" / "host.json", host)
    verdicts = {t.id: unmet(t, host) for t in tasks}
    unsupported = {task_id: why for task_id, (why, _) in verdicts.items() if why}
    # A probe failure only costs the run when a cell of the task is left unrun: a finished cell keeps its result.
    probe_failed = [task_id for task_id, (_, by) in verdicts.items() if by == "host-probe"
                    and any(not _done(batch_dir / cell_name(t.id, a, o.model, r))
                            for t, a, o, r in cells if t.id == task_id)]
    for task_id, why in unsupported.items():
        log(f"{task_id}: UNSUPPORTED, its cells are not run: {why}")
    for t, a, o, r in cells:  # this host meets them: an old marker would hide a cell this run never reaches
        if t.id not in unsupported:
            (batch_dir / cell_name(t.id, a, o.model, r) / UNSUPPORTED_FILE).unlink(missing_ok=True)
    lock = threading.Lock()
    state: dict[str, Any] = {"spent": 0.0, "ran": 0, "skipped": 0, "errors": [], "budget_stopped": 0,
                             "unsupported": 0}

    def one(cell: tuple[Task, str, AgentOptions, int]) -> None:
        task, arm, opts, rep = cell
        name = cell_name(task.id, arm, opts.model, rep)
        if task.id in unsupported:
            done = _done(batch_dir / name)
            if not done and verdicts[task.id][1] != "host-probe":  # a failed probe is no verdict on the cell
                _mark_unsupported(batch_dir / name, cell, unsupported[task.id], host)
            with lock:
                state["skipped" if done else "unsupported"] += 1
            return
        with lock:
            if max_batch_usd is not None and state["spent"] >= max_batch_usd and not _done(batch_dir / name):
                state["budget_stopped"] += 1
                return
        try:
            rec = run_cell(ctx, task, arm, rep, batch_dir, opts, force=force, host=host)
        except CellCrash as exc:
            with lock:
                state["spent"] += exc.charged_usd
                state["errors"].append({"cell": name, "error": str(exc)})
            log(f"✗ {name}: {exc}")
            return
        except (PrepError, ContainerError, OSError, subprocess.SubprocessError) as exc:
            with lock:
                state["errors"].append({"cell": name, "error": str(exc)})
            log(f"✗ {name}: {exc}")
            return
        with lock:
            if rec.get("skipped"):
                state["skipped"] += 1
                return
            state["ran"] += 1
            state["spent"] += rec["agent"].get("charged_usd", rec["agent"]["cost_usd"]) or 0.0
        g, a = rec["grade"], rec["agent"]
        verdict = ("GRADER FAILURE" if g["status"] == "grader_failure"
                   else f"score {g['score']}" if g["score_only"] else "pass" if g["success"] else "fail")
        log(f"{name}: {verdict}; ${a['cost_usd'] or 0:.4f}, {a['num_turns']} turns, {a['wall_s']}s"
            + (" (agent timed out)" if a["timed_out"] else ""))

    with ThreadPoolExecutor(max(1, jobs)) as pool:
        list(pool.map(one, cells))
    return {"cells": len(cells), "ran": state["ran"], "skipped": state["skipped"],
            "errors": state["errors"], "budget_stopped": state["budget_stopped"],
            "unsupported": state["unsupported"], "unsupported_tasks": unsupported, "probe_failed": probe_failed,
            "spent_usd": round(state["spent"], 6)}


UNSUPPORTED_FILE = "unsupported.json"


def _mark_unsupported(rd: Path, cell: tuple[Task, str, AgentOptions, int], reason: str,
                      host: dict[str, Any]) -> None:
    """Leave only ``unsupported.json`` in the cell's directory: what an earlier, unfinished run left goes."""
    task, arm, opts, rep = cell
    if rd.exists():
        _rmtree(rd)
    rd.mkdir(parents=True)
    _write_json(rd / UNSUPPORTED_FILE, {"task": task.id, "family": task.family, "arm": arm, "model": opts.model,
                                        "rep": rep, "harness": opts.profile.name, "reason": reason, "host": host})


class BatchBusy(ContainerError):
    """Another ``repo run`` holds the batch directory."""


@contextlib.contextmanager
def batch_lock(batch_dir: Path) -> Iterator[None]:
    """Hold ``<batch>/_eval/lock`` for one invocation: a second one on a live batch would replace its
    cells' run directories while their agents run, and grade them."""
    path = batch_dir / "_eval" / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        try:
            _lock_file(f)
        except OSError as exc:
            raise BatchBusy(f"batch {batch_dir} is in use by another `repo run`; wait for it or use "
                            "another --batch") from exc
        yield


def _lock_file(f: Any) -> None:
    if sys.platform == "win32":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _done(rd: Path) -> bool:
    return (rd / "result.json").is_file()


def _record_plan(batch_dir: Path, names: list[str]) -> None:
    """Union of every cell any invocation planned, so the report can count cells that never started."""
    path = batch_dir / "_eval" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(path.read_text(encoding="utf-8")).get("cells", []) if path.is_file() else []
    _write_json(path, {"cells": sorted(set(old) | set(names))})


def harness_version(ctx: EvalContext, opts: AgentOptions, work: Path) -> str:
    """Run the profile's ``version_cmd`` in the image with the mounted harness: it must start there."""
    work.mkdir(parents=True, exist_ok=True)
    bin_in = f"{HARNESS_DIR}/{opts.harness_bin.name}"
    spec = ContainerSpec(image=ctx.image, argv=opts.profile.version_argv(bin_in), workdir="/",
                         mounts=[Mount(opts.harness_bin.resolve(), bin_in, read_only=True)],
                         # HOME is the probe container's own /tmp, never a host path.
                         env={"HOME": "/tmp"}, network="none",  # nosec B108
                         name=container_name("version", "probe"))
    result = ctx.runner.run(spec, stdout=work / "version.out", stderr=work / "version.err", timeout_s=120)
    text = (work / "version.out").read_text(encoding="utf-8", errors="replace").strip()
    if not result.ok:
        err = (work / "version.err").read_text(encoding="utf-8", errors="replace").strip()
        raise ContainerError(f"{opts.harness_bin} does not run in {ctx.image}: exit {result.returncode}: "
                             f"{(err or text)[-300:]}")
    match = re.search(r"\d+\.\d+\.\d+", text)
    return match.group(0) if match else (text.splitlines()[0] if text else "unknown")


def container_name(cell: str, kind: str) -> str:
    return f"dokimi-{slug(cell)[:90]}-{kind}-{uuid.uuid4().hex[:8]}"


def _rmtree(path: Path) -> None:
    """``shutil.rmtree`` after making every directory writable again: an agent may leave ``chmod 555``."""
    pending = [path]
    while pending:
        here = pending.pop()
        with contextlib.suppress(OSError):
            here.chmod(0o700)
            pending.extend(p for p in here.iterdir() if p.is_dir() and not p.is_symlink())
    shutil.rmtree(path)


def _write_json(path: Path, data: Any) -> None:
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

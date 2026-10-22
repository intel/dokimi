# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo tasks check``: the self-check every task passes before any paid run (contract §4.3). No LLM.

Per task, on workspaces prepared exactly as for an agent (arm ``guidance:raw``,
empty ``answer.txt`` and ``events.jsonl``):

- **null**: ``grade`` on the untouched workspace must not pass the gate (and a
  score-only task must not reach ``score.max``);
- **oracle**: ``oracle`` then ``grade`` must pass the gate, or for a score-only
  task reach ``score.max`` on ``score.metric``;
- **isolation**: a container laid out like the agent's sees neither
  ``{eval_dir}`` nor ``{source_dir}``, holds no directory named like the task,
  and has no harness guidance file in an ancestor of the workspace.

A grader failure on either workspace is a grader bug: the task is ``broken``
and stays out of paid runs. A task whose network needs the egress proxy is
``unsupported`` until the proxy exists, and so is a task whose ``host``
requirements (:mod:`.host`) the evaluation's containers do not meet: its bug
cannot show here, so null and oracle would say nothing. ``--warm`` runs null
and oracle with network so the caches fill before offline runs (§2).
"""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .container import CONTENT_DIR, RUN_DIR, SOURCE_DIR, ContainerError, ContainerSpec, docker_network
from .grading import EvalContext, outcome, run_entry
from .host import probe_host, shortfall_kinds, unmet, validate_tasks
from .runner import agent_layout, container_name
from .workspace import PrepError, Task, prepare_workspace

ANCESTOR_NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENT.md", ".claude", "GEMINI.md")
_ISOLATION_SH = r"""
bad=0
for p in "$EVAL_CONTENT" "$EVAL_SOURCE"; do
  if [ -e "$p" ]; then echo "visible to the agent: $p"; bad=1; fi
done
for d in / /eval "$EVAL_RUN"; do
  for n in "$@"; do
    if [ -e "$d/$n" ]; then echo "guidance above the workspace: $d/$n"; bad=1; fi
  done
done
names=$(mktemp) || { echo "probe: mktemp failed"; exit 1; }
trap 'rm -f "$names"' EXIT
printf '%s\n' $EVAL_TASKS > "$names"
hits=$(find / -xdev \( -path /proc -o -path /sys -o -path /dev \) -prune -o -print 2>/dev/null \
  | awk -F/ '{print $NF}' | grep -Fx -f "$names" | sort -u | head -5)
if [ -n "$hits" ]; then echo "named like a task, in the image: $hits"; bad=1; fi
exit $bad
"""

def check_task(ctx: EvalContext, task: Task, work_dir: Path, warm: bool = False,
               guidance_names: tuple[str, ...] = ANCESTOR_NAMES,
               host: dict[str, Any] | None = None) -> dict[str, Any]:
    """Null, oracle and isolation checks for one task; ``verdict`` is ok, broken or unsupported.
    An unsupported result says why in ``unsupported_by``: ``egress``, ``host`` (a hardware miss, which
    ``--allow-unsupported`` may pass) or ``host-probe`` (the probe could not run or gave no CPU count,
    which it never passes).

    A task the host cannot show still has its workspace prepared and isolation probed, and its oracle run
    when only CPUs are short; only the null check, which depends on the hardware, is skipped. ``--warm``
    judges the host the same way: network changes neither the CPU view nor the hardware.

    ``host`` is :func:`.host.probe_host`'s facts, probed once per check; without it a task with
    ``host`` requirements probes for itself."""
    network = ctx.manifest.image.network if ctx.manifest.image else "none"
    res: dict[str, Any] = {"task": task.id, "family": task.family, "warm": warm, "problems": []}
    try:
        net = "bridge" if warm else docker_network(network, task.egress)
    except ContainerError as exc:
        res.update(verdict="unsupported", unsupported_by="egress", problems=[str(exc)])
        return res
    # The probe sees what every container of this runner sees (affinity, cgroup quota), with or without
    # --warm's network: an unmet host requirement is unsupported either way, never a passing null run.
    reason, unsupported_by, facts = _host_reason(ctx, task, work_dir, host)
    # Only what the hardware decides is skipped: the null check (it passes where the bug cannot show) and,
    # unless the miss is CPUs alone, the oracle. Preparing the workspace and isolation hold anywhere.
    run_oracle = not reason or (unsupported_by == "host" and shortfall_kinds(task, facts) <= {"cpus"})
    for kind, run_it in (("null", not reason), ("oracle", run_oracle)):
        _check_step(ctx, task, work_dir / f"{task.id}__{kind}", kind, run_it, net, res)
    if not res["problems"]:
        res["isolation"] = isolation_probe(ctx, [task], work_dir / f"{task.id}__null", guidance_names)
        res["problems"] += [f"isolation: {p}" for p in res["isolation"]]
    if reason and not res["problems"]:
        res.update(verdict="unsupported", unsupported_by=unsupported_by or "host", problems=[f"host: {reason}"])
    else:
        res["verdict"] = "broken" if res["problems"] else "ok"
    return res


def _host_reason(ctx: EvalContext, task: Task, work_dir: Path,
                 host: dict[str, Any] | None) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """``(reason, unsupported_by, facts)``; reason and unsupported_by are ``None`` when the host suffices."""
    if host is None and task.raw.get("host") is not None:
        host = probe_host(ctx, work_dir / f"{task.id}__host")
    return (*unmet(task, host), host)


def _check_step(ctx: EvalContext, task: Task, rd: Path, kind: str, run_it: bool, net: str,
                res: dict[str, Any]) -> None:
    """Prepare ``rd`` and, when ``run_it``, run the null or oracle step into ``res``."""
    try:
        _prepare(ctx, task, rd)
        if not run_it:
            return
        if kind == "oracle":
            run = run_entry(ctx, "oracle", task, rd, net, container_name(rd.name, "oracle"))
            res["oracle_run"] = {"rc": run.returncode, "timed_out": run.timed_out, "wall_s": run.wall_s}
            if not run.ok:
                res["problems"].append(f"oracle {'timed out' if run.timed_out else f'exited {run.returncode}'}"
                                       f"; see {rd / 'oracle.log'}")
                return
        res[kind] = outcome(task, rd, run_entry(ctx, "grade", task, rd, net, container_name(rd.name, "grade")))
    except (PrepError, ContainerError) as exc:
        res["problems"].append(f"{kind}: {exc}")
        return
    res["problems"] += _verdict_problems(task, kind, res[kind], rd)


def _prepare(ctx: EvalContext, task: Task, rd: Path) -> None:
    if rd.exists():
        shutil.rmtree(rd)
    rd.mkdir(parents=True)
    meta = prepare_workspace(ctx.git_dir, ctx.manifest, task, "guidance:raw", rd)
    meta.update(harness="none", harness_version=None, model="none", rep=0)
    for name, data in (("meta.json", meta), ("task.json", task.raw)):
        (rd / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    (rd / "answer.txt").write_text("", encoding="utf-8")
    (rd / "events.jsonl").write_text("", encoding="utf-8")


def _verdict_problems(task: Task, kind: str, grade: dict[str, Any], rd: Path) -> list[str]:
    if grade["status"] == "grader_failure":
        return [f"{kind}: grader failure ({'; '.join(grade['reasons'])}); see {rd / 'grade.log'}"]
    top = task.score["max"] if task.score else None
    reached = top is not None and grade["score"] is not None and grade["score"] >= top
    if kind == "null":
        out = ["null: the untouched workspace passes the gate"] if grade["success"] else []
        return out + ([f"null: the untouched workspace reaches score.max {top}"] if reached else [])
    if grade["score_only"] and task.score:  # outcome() fails pass: null when the task has no `score`
        return [] if reached else [f"oracle: {task.score['metric']} = {grade['score']}, below score.max {top}"]
    out = [] if grade["success"] else ["oracle: the reference solution does not pass the gate"]
    if task.score and not reached:
        out.append(f"oracle: {task.score['metric']} = {grade['score']}, below score.max {top}")
    return out


def isolation_probe(ctx: EvalContext, tasks: list[Task], rd: Path,
                    names: tuple[str, ...] = ANCESTOR_NAMES) -> list[str]:
    """Problems seen by a container with the agent's mounts (no harness) in ``rd``; empty when isolated.

    The image itself is searched (``find -xdev``: the bind mounts are other file systems) for an
    entry named like any of ``tasks``, which is how a Dockerfile that copied the tasks shows up.
    """
    (rd / "workspace").mkdir(parents=True, exist_ok=True)
    mounts, env = agent_layout(ctx, rd, tasks[0])
    env = {**env, "EVAL_CONTENT": CONTENT_DIR, "EVAL_SOURCE": SOURCE_DIR, "EVAL_RUN": RUN_DIR,
           "EVAL_TASKS": " ".join(t.id for t in tasks)}
    spec = ContainerSpec(image=ctx.image, argv=["sh", "-c", _ISOLATION_SH, "sh", *names],
                         mounts=mounts, env=env, network="none", name=container_name(rd.name, "isolation"))
    out, err = rd / "isolation.log", rd / "isolation.err"
    result = ctx.runner.run(spec, stdout=out, stderr=err, timeout_s=600)
    if result.ok:
        return []
    found = [line for line in out.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return found or [f"probe exited {result.returncode}: {err.read_text(encoding='utf-8', errors='replace').strip()[-300:]}"]


def run_check(ctx: EvalContext, tasks: list[Task], work_dir: Path, warm: bool = False, jobs: int = 1,
              guidance_names: tuple[str, ...] = ANCESTOR_NAMES, log=print) -> list[dict[str, Any]]:
    """Every task's :func:`check_task`; ``check.json`` also records the probed host."""
    validate_tasks(tasks)  # a bad `host` value is refused before any container starts
    work_dir.mkdir(parents=True, exist_ok=True)
    host = probe_host(ctx, work_dir)

    def one(task: Task) -> dict[str, Any]:
        res = check_task(ctx, task, work_dir, warm, guidance_names, host)
        log(f"{task.id}: {res['verdict'].upper()}" + "".join(f"\n  - {p}" for p in res["problems"]))
        return res

    with ThreadPoolExecutor(max(1, jobs)) as pool:
        results = list(pool.map(one, tasks))
    (work_dir / "check.json").write_text(json.dumps({"warm": warm, "host": host, "tasks": results}, indent=1)
                                         + "\n", encoding="utf-8")
    return results

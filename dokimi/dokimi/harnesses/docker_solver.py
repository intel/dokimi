# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Standalone Docker harness — solve an executable task and verify it.

It builds, solves and verifies a task against local Docker with **no Harbor or
BenchFlow dependency**, and captures full LLM telemetry (cost, tokens, cache,
latency, turns) into the ``task_runs.v1`` artifact so task experiments are
tracked the same way the Q&A ``arms run`` path already is.

Three solver modes:

``oracle``
    Apply the task's own oracle ``solve.sh`` (no LLM), in either layout:
    Harbor ``task.toml`` or BenchFlow/SkillsBench ``task.md`` (see
    :mod:`.tasks`). Validates that the task image + verifier work end to end.
    Free; no telemetry.
``claude``
    Run ``claude -p --output-format stream-json`` to solve from ``instruction.md``.
    The final result event carries ``total_cost_usd`` / ``usage`` / ``duration_ms``
    / ``ttft_ms`` / ``num_turns``, which we parse into a :class:`UsageRecord` and
    fold into ``HarnessResult.metrics``, with tool calls counted by name. An
    optional skill is installed as a plugin (the with-skill treatment arm, see
    :mod:`.skill_treatment`); with ``skill_notice`` off the prompt does not
    announce it (the discovery arm).
``probe``
    No LLM: run the isolation probe from :mod:`.agent_sandbox` in exactly the
    cwd and env ``claude`` would get, and score 1.0 only when nothing the agent
    must not see (grader files, the content repo's skills, the operator's
    ``~/.claude``, the network, host env) is reachable. Free; run it before a
    paid sweep to know what the arms could have read.

By default the agent runs inside the task container (:mod:`.container_agent`):
it edits ``/app`` in place, behind an egress proxy that reaches only the LLM.
The ``-host`` variants run it on the host instead (:mod:`.agent_sandbox`): we
snapshot ``/app`` into a scratch tree outside any checkout, let the agent edit
it, and copy it back. ``/app`` is the only tree either agent gets: a declared
``environment.workdir`` or an image ``WORKDIR`` outside ``/app`` is refused
(an image with no absolute ``WORKDIR`` runs in ``/app``), and so is a
``task.md`` task for the host agent. Either way the documented compile
commands are re-run in the container before the verifier. What a launch
seals is recorded per run in ``metrics.isolation``.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from dokimi import docker_cmd, process, process_io
from dokimi.container_policy import task_container_policy
from dokimi.metrics.usage import UsageRecord

from .agent_sandbox import (
    LEAK_CLASSES,
    AgentWorkspace,
    credential_values,
    default_forbidden,
    operator_source_env,
    probe_reward,
    run_probe,
    solver_cli_flags,
)
from .claude_json import _last_json_object, _parse_claude_json  # noqa: F401
from .container_agent import AGENT_BIN, DOCKER_STEP_TIMEOUT, FILES_DIR, ContainerSandbox, Docker
from .models import HarnessResult, OperationRecord
from .refusal import _AGENT_TREE, _refuse_unrunnable, _require_agent_workdir
from .network_policy import (DEFAULTS, NetworkPlan, PhasePolicy, build_args, enter_phase, needs_sandbox,
                             resolve_recorded, skill_needs, start_network, redact as _redact)
from .null_check import NULL_FAIL, null_status, pytest_counts
from .skill_treatment import Treatment, build_plugin, check_skill, skill_listing, treatment_notice
from .solver_stream import record_solver, run_solver
from .task_image import (ignore_top_level_skills, ready_image, upload_environment, warn_credential_build_args,
                         workdir_args)
from .task_env import _exec_env, exec_env_file, unresolved_env  # noqa: F401 (_exec_env re-exported)
from .tbench_v1 import reward as _pytest_reward
from .verifier_reward import clear_reward_files, exit_code_verdict, pull_reward
from .tasks import (  # noqa: F401  (timeouts re-exported)
    _DEFAULT_BUILD_TIMEOUT,
    _DEFAULT_VERIFY_TIMEOUT,
    ContainerStep,
    TaskSpec,
    _task_build_timeout,
    _task_verify_timeout,
)

# Recompile commands embedded in instruction.md, e.g. `g++ -O3 ... -o /app/bench`.
# C tasks (missing-restrict) use gcc, C++ tasks use g++.
_RECOMPILE_RE = re.compile(r"(g\+\+|gcc) [^`\n]*-o /app/[^ `\n]*")
_DEFAULT_SOLVE_TIMEOUT = 600.0
# Root-owned, outside /app and the agent's HOME. The skill is loaded with --plugin-dir: a
# skill in $CLAUDE_CONFIG_DIR/skills is not offered under the launch's --setting-sources "".
_CONTAINER_PLUGIN = "/opt/claude-plugins"


@dataclass(frozen=True)
class DockerSolverHarness:
    """Build → solve → verify a task with local Docker; emit reward + telemetry.

    ``mode`` is ``oracle``, ``null`` (no solve: the tests must fail), ``claude`` or ``probe``. ``skill_path`` points at a
    skill directory or its ``SKILL.md``; in claude mode the skill tree, less its
    top-level ``evals/``, is installed as a plugin for the with-skill treatment
    arm (see :mod:`.skill_treatment`), in probe mode it is a path the probe must
    not reach.
    ``skill_notice`` opens the prompt with the treatment notice; off, the skill
    is installed the same way and the agent must find it by its description
    (the ``-skill-discover`` arms).
    ``agent_location`` is ``container`` (the agent runs in the task container,
    see :mod:`.container_agent`) or ``host`` (see :mod:`.agent_sandbox`).
    """

    harness_id: str
    mode: str = "claude"
    skill_path: Optional[str] = None
    model_default: Optional[str] = None
    kind: str = "docker"
    env: dict[str, str] = field(default_factory=dict)
    agent_location: str = "container"
    network: tuple[str, ...] = ()  # --network PHASE=MODE overrides (see :mod:`.network_policy`)
    runner: process.Runner = field(default=process.run, compare=False, repr=False)
    skill_notice: bool = True

    def run(
        self,
        task: TaskSpec,
        *,
        model: Optional[str] = None,
        output_dir: Optional[Path] = None,
        timeout_sec: Optional[float] = None,
        dry_run: bool = False,
    ) -> HarnessResult:
        _refuse_unrunnable(task, self.mode, self.agent_location)
        output_dir = Path(output_dir) if output_dir else None
        plan = self._plan(task, record=None if dry_run else output_dir)
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        model = model or self.model_default

        operations = [
            OperationRecord(
                type="harness",
                name=self.harness_id,
                metadata={"kind": self.kind, "task": task.name, "model": model, "mode": self.mode},
            )
        ]

        # Steps that would run with a host-env value we do not forward. The cell
        # is missing data whatever its reward, so it is decided from the task
        # file alone, before the build and before a paid solve. A probe runs no
        # verifier or oracle, so it has nothing to miss.
        steps = {"oracle": ("verifier", "oracle"), "claude": ("verifier",), "null": ("verifier",)}.get(self.mode, ())
        unresolved = {t: names for t in steps if (names := unresolved_env(task, t))}
        if unresolved:
            return HarnessResult(
                harness=self.harness_id,
                task=task.name,
                command=self._describe_command(task, model),
                returncode=0,
                elapsed_sec=0.0,
                reward=None,
                passed=False,
                output_dir=str(output_dir) if output_dir else None,
                operations=operations,
                metrics={
                    "dry_run": dry_run, "mode": self.mode, "unresolved_env": unresolved,
                    "network": self._network_metrics(plan),
                    "error": "task needs host env not forwarded: "
                    + "; ".join(f"{t}.env {', '.join(n)}" for t, n in unresolved.items()),
                },
            )

        if dry_run:
            return HarnessResult(
                harness=self.harness_id,
                task=task.name,
                command=self._describe_command(task, model),
                returncode=0,
                elapsed_sec=0.0,
                reward=None,
                passed=False,
                output_dir=str(output_dir) if output_dir else None,
                operations=operations,
                metrics={"dry_run": True, "mode": self.mode, "network": self._network_metrics(plan)},
            )

        if output_dir is None:
            raise ValueError("DockerSolverHarness requires an output_dir")
        skill_dir = self._resolve_skill_dir()
        if self.mode == "claude" and skill_dir is not None:
            check_skill(skill_dir)  # a bad skill fails here, not after the image and network

        log_path = output_dir / "run.log"
        log = log_path.open("w", encoding="utf-8")
        usage: Optional[UsageRecord] = None
        container = f"dokimi-run-{task.name}-{os.getpid()}-{os.urandom(3).hex()}"  # two cells in one process
        solve_timeout = timeout_sec or task.timeout_sec or _DEFAULT_SOLVE_TIMEOUT
        start = time.time()
        returncode = 0
        extra_metrics: dict[str, Any] = {}
        network = self._network_metrics(plan)
        reward: Optional[float] = None
        verifier: dict[str, Any] = {}  # the null check reads the pytest counts of the verifier
        sandbox: Optional[ContainerSandbox] = None
        image_record: dict[str, Any] = {}
        try:
            image, image_record = self._ready_image(task, log, plan.build)
            sandbox = self._open_sandbox(plan, container, log)
            try:
                bridged = self._start_container(task, image, container, log, sandbox, plan)
                try:
                    upload_environment(task, container, log, self._docker)
                    if self.mode == "oracle":
                        self._solve_oracle(task, container, log, plan.solve, timeout=solve_timeout)
                    elif self.mode == "claude":
                        usage = self._solve_claude(
                            task, container, output_dir, model, solve_timeout,
                            operations, log, extra_metrics, sandbox,
                        )
                    elif self.mode == "probe":
                        reward = self._solve_probe(task, container, output_dir, log, extra_metrics,
                                                   sandbox)
                    else:
                        self._require_null_mode()
                    if self.mode != "probe":  # the probe's reward is its own verdict
                        reward = self._verify_phase(task, plan.verify, container, output_dir, log,
                                                    sandbox, bridged, network, verifier)
                finally:
                    self._docker(["rm", "-f", container], log, check=False)
            finally:
                if sandbox is not None:  # also when the container never started
                    sandbox.close(self._docker, log, output_dir)
                    extra_metrics["egress"] = sandbox.egress_summary()
        except subprocess.TimeoutExpired as exc:
            log.write(f"\nTIMEOUT: {_describe_timeout(exc)}\n")
            returncode = 124
            reward = 0.0
        except Exception as exc:
            # A build failure: the runner errors the cell. After a solve, keep its cost and isolation record.
            if not extra_metrics:
                raise
            log.write(f"\nERROR: {type(exc).__name__}: {exc}\n")
            extra_metrics["error"] = f"{type(exc).__name__}: {exc}"
            returncode = 1
            reward = None
        finally:
            log.close()
            (output_dir / "network.json").write_text(json.dumps(network, indent=1), encoding="utf-8")

        elapsed = time.time() - start
        passed, verdict_metrics = self._verdict(reward, verifier)
        metrics: dict[str, Any] = {"dry_run": False, "mode": self.mode, "network": network,
                                   "image": image_record, **verdict_metrics}
        metrics.update({**_usage_metrics(usage), **extra_metrics})
        return HarnessResult(
            harness=self.harness_id,
            task=task.name,
            command=self._describe_command(task, model),
            returncode=returncode,
            elapsed_sec=elapsed,
            reward=reward,
            passed=passed,
            stdout_tail=_tail(log_path.read_text(encoding="utf-8", errors="replace")),
            output_dir=str(output_dir),
            operations=operations,
            metrics=metrics,
        )

    def _require_null_mode(self) -> None:
        if self.mode != "null":
            raise ValueError(f"unknown mode {self.mode!r}")

    def _verdict(self, reward: Optional[float], verifier: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Whether the cell passed, and the metrics that say why (the null check's status)."""
        if self.mode == "null":  # the check passes when the tests fail on the untouched start
            status = null_status(reward, verifier.get("pytest"), explicit_reward=verifier.get("reward_file", False))
            return status == NULL_FAIL, {"null_check": status}
        return reward is not None and reward >= 1.0, {}

    def _docker(self, args: list[str], log, **kwargs: Any) -> process.Completed:
        """:func:`_docker` through this harness's runner."""
        return _docker(args, log, runner=self.runner, **kwargs)

    # ── pipeline steps ──────────────────────────────────────────────────────

    def _ready_image(self, task: TaskSpec, log, net: PhasePolicy) -> tuple[str, dict[str, Any]]:
        """The task image, built or pulled (:mod:`.task_image`). Running out of time errors the cell:
        no agent ran, so there is no verdict (a solve or verify timeout is a reward-0 failure)."""
        step = "pull" if task.docker_image else "build"
        try:
            return ready_image(task, log, self._docker, lambda tag: self._build_image(task, tag, log, net))
        except subprocess.TimeoutExpired as exc:
            log.write(f"\n{step.upper()} TIMEOUT: {_describe_timeout(exc)}\n")
            raise RuntimeError(f"docker {step} timed out after {exc.timeout}s; see run.log") from exc

    def _build_image(self, task: TaskSpec, image: str, log, net: Optional[PhasePolicy] = None) -> None:
        """Build the task image under *net*: ``public`` passes the host proxy through.

        Task-bundled skills (``environment/skills``) are agent capabilities, not
        image content: a ``COPY . /x`` in the Dockerfile would otherwise bake
        them into the no-skill arm too. When a task ships them, the image is
        built from a copy of ``environment/`` without that directory — the
        same rule BenchFlow applies to its no-skill runs.
        """
        env_dir = task.build_context
        flags = build_args(net or DEFAULTS["build"])
        # Honor the task's own build_timeout_sec (some images need >10 min);
        # _DEFAULT_BUILD_TIMEOUT is only the fallback when the task omits it.
        timeout = _task_build_timeout(task)
        with tempfile.TemporaryDirectory(prefix="dokimi-build-") as tmp:
            context = env_dir
            if task.skills_dir is not None:
                context = Path(tmp) / "environment"
                # symlinks=True: copy links as links, as `docker build` sees them;
                # following them would pull host files into the image, and a
                # dangling one would fail the copy.
                shutil.copytree(env_dir, context, symlinks=True,
                                ignore=ignore_top_level_skills(env_dir))
                # Left empty rather than absent: some Dockerfiles `COPY skills
                # ...` (radar-vital-signs copies it into seven agent paths), and
                # a missing source fails the build in every arm.
                (context / "skills").mkdir()
            warn_credential_build_args(context, task.dockerfile, net or DEFAULTS["build"], log)
            self._docker(
                ["build", "-q", *flags, "-t", image,
                 "-f", str(context / task.dockerfile), str(context)],
                log, timeout=timeout,
            )

    def _start_container(self, task: TaskSpec, image: str, container: str, log,
                         sandbox: Optional[ContainerSandbox] = None,
                         plan: Optional[NetworkPlan] = None) -> bool:
        """Start the task container on the solve phase's network; returns whether it is on bridge."""
        plan = plan or self._plan(task)
        inside = self._solve_in_container()
        cpus_raw = task.environment.get("cpus")
        cpus = str(cpus_raw) if cpus_raw is not None else "4"

        # A malformed external task.toml (e.g. memory_mb as a string with a
        # unit already attached) must not crash the whole run -- fall back
        # to the historical default instead.
        memory_mb_raw = task.environment.get("memory_mb")
        try:
            memory = f"{int(memory_mb_raw)}m" if memory_mb_raw is not None else "2g"
        except (TypeError, ValueError):
            memory = "2g"
        # PID 1 must outlive the solve and the verifier: when it exits, an
        # in-container agent dies mid-solve and the verifier finds no container.
        lifetime = int(max(1800.0, (task.timeout_sec or _DEFAULT_SOLVE_TIMEOUT)
                           + _task_verify_timeout(task) + 600))
        self._docker(["rm", "-f", container], log, check=False)
        network = sandbox.network if sandbox else start_network(plan, solve_in_container=inside)
        sandbox_flags = sandbox.run_args() if sandbox else []
        self._docker(docker_cmd.run_spec(
            task_container_policy(network=network, cpus=cpus, memory=memory),
            [["-d"], ["--name", container], workdir_args(task), "init", "cap_drop", "security", "pids", "cap_add",
             "network", sandbox_flags, docker_cmd.env_args(task.container_env), "resource"],
            image, ["sleep", str(lifetime)]), log)
        return enter_phase(plan.solve if inside else DEFAULTS["solve"], container, self._docker, log,
                           bridged=network == "bridge")

    def _solve_oracle(self, task: TaskSpec, container: str, log, net: Optional[PhasePolicy] = None,
                      timeout: float = _DEFAULT_SOLVE_TIMEOUT) -> None:
        """Apply the oracle's solve.sh as root (some images set a non-root USER), within the
        agent's ``timeout``: an oracle that runs out of time is a failed cell, like an agent.

        Copies the whole oracle directory, not just ``solve.sh`` -- our own
        tasks' solve.sh is always self-contained, but an external task's
        solve.sh can be a thin script that `cp`s a sibling source file into
        place (e.g. uxlfoundation/skills' oneTBB tasks). It is mounted at both
        ``/solution`` (Harbor) and ``/oracle`` (BenchFlow), because a script
        written for one layout refers to its siblings by that layout's path.
        """
        step = task.oracle_step
        _copy_into(step, container, log, self._docker)
        with exec_env_file(task, "oracle", net) as (flags, values):
            self._docker(["exec", *_user(step), *flags, container, "bash", "-lc", step.command],
                         log, check=False, timeout=timeout, env=values)

    def _solve_claude(
        self, task: TaskSpec, container: str, output_dir: Path,
        model: Optional[str], timeout: float, operations: list[OperationRecord], log,
        extra_metrics: dict[str, Any], sandbox: Optional[ContainerSandbox] = None,
    ) -> Optional[UsageRecord]:
        """Solve with ``claude -p`` in the task container (*sandbox*) or on the host.

        Returns a :class:`UsageRecord` parsed from the ``claude`` result event
        (cost/tokens/cache/latency/turns), or ``None`` if the JSON was missing.
        """
        # A rerun must not find its own previous tree or verdict during the solve.
        _rmtree(output_dir / "work")
        clear_reward_files(output_dir)
        if sandbox is not None and self.agent_location == "container":  # a host arm stays on the host
            return self._solve_claude_in_container(task, container, output_dir, model, timeout,
                                                   operations, log, extra_metrics, sandbox)
        workdir = _require_agent_workdir(task, container, log, self._docker)
        workspace, treatment = self._prepare_workspace(task, container, log, extra_metrics, output_dir=output_dir)
        extra_metrics["isolation"] = workspace.isolation()
        try:
            work = workspace.work
            plugin = str(_host_plugin_dir(workspace)) if treatment else None
            cmd = ["claude", *self._claude_args(task, treatment, plugin, model, output_dir)]
            before = workspace.state()
            t0 = time.time()
            env = workspace.env(operator_source_env(), self.env)
            secrets = credential_values(env)
            proc = run_solver(cmd, output_dir, extra_metrics, treatment, secrets, cwd=str(work),
                              timeout=timeout, env=env, runner=self.runner)
            usage = record_solver(proc, task.name, model, time.time() - t0, output_dir,
                                  operations, log, extra_metrics, treatment, secrets,
                                  edited=workspace.state() != before)
            # The agent sees all of /app but writes back only its working tree: a
            # read-only baseline beside it (/app/baseline) stays the image's.
            back = work / posixpath.relpath(workdir, _AGENT_TREE)
            self._docker(["cp", f"{back}/.", f"{container}:{workdir}/"], log)
        finally:
            # Keep the agent's tree with the run (also after a timeout); the plugin is beside it, not in it.
            _rmtree(output_dir / "work")
            try:
                shutil.copytree(workspace.work, output_dir / "work", symlinks=True)
            except OSError as exc:  # never mask the solve's own outcome
                log.write(f"\n[keep work tree] {exc}\n")
            workspace.cleanup()
        failed = self._recompile(task, container, log)
        if failed:
            extra_metrics["recompile_failed"] = failed
        return usage

    def _solve_claude_in_container(
        self, task: TaskSpec, container: str, output_dir: Path,
        model: Optional[str], timeout: float, operations: list[OperationRecord], log,
        extra_metrics: dict[str, Any], sandbox: ContainerSandbox,
    ) -> Optional[UsageRecord]:
        """The agent edits its work tree in place; the grader files are not in the container yet."""
        # Before the chown, which hands it this tree only: nothing is spent yet.
        sandbox.workdir = _require_agent_workdir(task, container, log, self._docker)
        extra_metrics["isolation"] = sandbox.isolation()
        treatment = self._install_plugin_in_container(container, output_dir, log, extra_metrics)
        sandbox.prepare(container, self._docker, log)
        env = sandbox.env(operator_source_env(), self.env)
        secrets = credential_values(env)
        argv, client_env = sandbox.exec_argv(container, env, [AGENT_BIN, *self._claude_args(
            task, treatment, _CONTAINER_PLUGIN if treatment else None, model, output_dir)])
        t0 = time.time()
        try:
            proc = run_solver(argv, output_dir, extra_metrics, treatment, secrets, timeout=timeout,
                              env=client_env, runner=self.runner)
            # Before the cleanup: a stuck cleanup step must not lose the solve's stream and cost.
            usage = record_solver(proc, task.name, model, time.time() - t0, output_dir,
                                  operations, log, extra_metrics, treatment, secrets)
        finally:  # also on a timeout: stop the agent, keep its tree for debugging
            stuck = self._stop_container_agent(container, output_dir, sandbox, log)
        if stuck is not None:  # the agent may still run, or its credentials remain: no verdict
            raise stuck
        failed = self._recompile(task, container, log)
        if failed:
            extra_metrics["recompile_failed"] = failed
        return usage

    def _stop_container_agent(self, container: str, output_dir: Path, sandbox: ContainerSandbox,
                              log) -> Optional[subprocess.TimeoutExpired]:
        """End the in-container agent's side of the run; every step runs, whatever the others do.

        Returns the first step that timed out (the caller must not verify then): a
        survivor could read ``/tests``, and the verifier runs the agent's ``/app``
        code as root, so leftover credential files are reachable by it.
        """
        stuck: Optional[subprocess.TimeoutExpired] = None

        def note(proc: process.Completed) -> None:
            nonlocal stuck
            if proc.timed_out and stuck is None:
                stuck = subprocess.TimeoutExpired(proc.argv, DOCKER_STEP_TIMEOUT)

        try:
            sandbox.reap(container, self._docker, log)  # nothing the agent backgrounded sees /tests
        except subprocess.TimeoutExpired as exc:
            stuck = exc
        # Not on the verifier's side of the run.
        note(self._docker(["exec", "-u", "root", container, "rm", "-rf", _CONTAINER_PLUGIN], log, check=False))
        _rmtree(output_dir / "work")
        (output_dir / "work").mkdir(parents=True)
        self._docker(["cp", f"{container}:/app/.", str(output_dir / "work")], log, check=False)
        # The verifier runs the agent's /app code as root, on bridge for a public task.
        note(self._docker(["exec", "-u", "root", container, "rm", "-rf", FILES_DIR], log, check=False))
        return stuck

    def _prepare_workspace(self, task: TaskSpec, container: str, log, extra_metrics: Optional[dict[str, Any]] = None,
                           output_dir: Optional[Path] = None) -> tuple[AgentWorkspace, Optional[Treatment]]:
        """Snapshot ``/app`` into a scratch work tree; install the arm's skill beside it.

        In probe mode the skill is not offered: it joins the paths the probe must not reach.
        """
        skill_dir = self._resolve_skill_dir()
        probing = self.mode == "probe" and skill_dir is not None
        forbidden = default_forbidden(task.path, [skill_dir] if probing and skill_dir else [], output_dir=output_dir)
        workspace = AgentWorkspace.create(forbidden=forbidden)
        try:
            self._docker(["cp", f"{container}:/app/.", str(workspace.work)], log)
            treatment = None
            if skill_dir is not None and not probing:
                treatment = build_plugin(skill_dir, _host_plugin_dir(workspace))
                if extra_metrics is not None:
                    self._record_treatment(treatment, skill_dir, extra_metrics, log)
        except BaseException:  # also Ctrl-C: don't leave the snapshot behind
            workspace.cleanup()
            raise
        return workspace, treatment

    def _install_plugin_in_container(self, container: str, output_dir: Path, log,
                                     extra_metrics: dict[str, Any]) -> Optional[Treatment]:
        """Copy the arm's skill, as a plugin, to ``_CONTAINER_PLUGIN`` (outside ``/app``)."""
        skill_dir = self._resolve_skill_dir()
        if skill_dir is None:
            return None
        staged = output_dir / "plugin"
        _rmtree(staged)
        try:
            treatment = build_plugin(skill_dir, staged)
            self._record_treatment(treatment, skill_dir, extra_metrics, log)
            # Create the target first and copy its contents: `docker cp dir path` nests
            # dir inside an existing path, and fails where /opt does not exist.
            self._docker(["exec", "-u", "root", container, "mkdir", "-p", _CONTAINER_PLUGIN], log)
            self._docker(["cp", f"{staged}/.", f"{container}:{_CONTAINER_PLUGIN}"], log)
            # Root-owned, read-only to the agent. u=rwX,go=rX also clears setuid/setgid and
            # group/other write that copy2 and docker cp carry over (a+rX only adds bits).
            self._docker(["exec", "-u", "root", container, "chmod", "-R", "u=rwX,go=rX",
                     _CONTAINER_PLUGIN], log)
        finally:  # also when a docker step fails: the copy is not kept with the run
            _rmtree(staged)
        return treatment

    def _record_treatment(self, treatment: Treatment, skill_dir: Path,
                          extra_metrics: dict[str, Any], log) -> None:
        """The arm's skill, whether the prompt announced it, and what its listing shows.

        A discovery arm whose description the listing cuts measures part of a
        description: kept a measured cell, with a warning in ``run.log``.
        """
        extra_metrics["treatment"] = treatment.as_metrics()
        extra_metrics["skill_notice"] = self.skill_notice
        listing = skill_listing(skill_dir)
        extra_metrics["skill_listing"] = listing
        if listing["truncated"] and not self.skill_notice:
            log.write(f"\n[skill] WARNING: description is {listing['description_chars']} chars; "
                      f"the listing shows the first {listing['max_desc_chars']}\n")

    def _claude_args(self, task: TaskSpec, treatment: Optional[Treatment],
                     plugin_dir: Optional[str], model: Optional[str],
                     output_dir: Path) -> list[str]:
        """``claude`` argv after the executable; the prompt is also saved as ``prompt.txt``."""
        # The task instruction behind the treatment notice, with a lone surrogate written as "?".
        prompt = process_io.utf8_bytes(f"{treatment_notice(treatment) if self.skill_notice else ''}{task.instruction}").decode()
        (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        args = ["-p", prompt, "--output-format", "stream-json", "--verbose", *solver_cli_flags()]
        args += ["--plugin-dir", plugin_dir] if plugin_dir else []
        return args + (["--model", model] if model else [])

    def _solve_probe(self, task: TaskSpec, container: str, output_dir: Path, log,
                     extra_metrics: dict[str, Any],
                     sandbox: Optional[ContainerSandbox] = None) -> float:
        """Run the isolation probe where the agent would run; reward 1.0 only if sealed."""
        untested = {k: "untested" for k in LEAK_CLASSES}
        # Where docker-claude is refused, its probe would vouch for an arm that never runs.
        workdir = _require_agent_workdir(task, container, log, self._docker)
        if sandbox is not None and self.agent_location == "container":  # not for a verify allowlist
            sandbox.workdir = workdir
            sandbox.prepare(container, self._docker, log)
            skill_dir = self._resolve_skill_dir()  # docker-probe:<skill>: forbidden, never offered
            try:
                result = sandbox.run_probe(container, task.path, operator_source_env(), self.env,
                                           self._docker, log, [skill_dir] if skill_dir else [])
            except subprocess.TimeoutExpired as exc:  # a hung check: nothing was shown closed
                log.write(f"\n[probe] TIMEOUT: {exc}\n")
                result = untested
            isolation = sandbox.isolation()
        else:
            workspace, _ = self._prepare_workspace(task, container, log, output_dir=output_dir)
            try:
                result = run_probe(workspace, operator_source_env(), self.env, runner=self.runner)
            except subprocess.TimeoutExpired as exc:
                log.write(f"\n[probe] TIMEOUT: {exc}\n")
                result = untested
            finally:
                workspace.cleanup()
            isolation = workspace.isolation()
        log.write(f"\n[probe] {json.dumps(result)}\n")
        (output_dir / "isolation_probe.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        extra_metrics["isolation"] = isolation
        extra_metrics["isolation_probe"] = result
        return probe_reward(result)

    def _recompile(self, task: TaskSpec, container: str, log) -> list[str]:
        """Re-run the compile command(s) documented in instruction.md, in the container.

        A host-launched agent builds against the host's (newer) glibc, which may
        not run in the older task image; an in-container agent may simply not
        have built. Matches both ``g++`` and ``gcc``.

        Returns the compile commands that failed. A recompile failure is a
        *tooling* failure, not a solve failure: the caller records it in
        ``metrics`` so such cells can be told apart from genuine model misses.
        """
        failed: list[str] = []
        for match in _RECOMPILE_RE.finditer(task.instruction):
            cc = match.group(0).strip()
            log.write(f"\n[recompile] {cc}\n")
            proc = self._docker(["exec", "-u", "root", container, "bash", "-lc", cc], log, check=False)
            if proc.timed_out:  # no verdict on a binary that may be stale or half-built
                raise subprocess.TimeoutExpired(proc.argv, DOCKER_STEP_TIMEOUT)
            if proc.returncode != 0:
                log.write(f"[recompile] FAILED (exit {proc.returncode}): {cc}\n")
                failed.append(cc)
        return failed

    def _verify(self, task: TaskSpec, container: str, output_dir: Path, log,
                net: Optional[PhasePolicy] = None, verifier: Optional[dict[str, Any]] = None) -> Optional[float]:
        """Run the verifier in-container; pull its reward.json or reward.txt back out of /logs/verifier.

        Mounted at both ``/tests`` (Harbor) and ``/verifier`` (BenchFlow): a
        SkillsBench ``test.sh`` runs ``pytest /verifier/test_outputs.py`` even
        after ``bench tasks export`` has renamed the directory to ``tests/``.
        ``verifier``, when given, gets the pytest counts of its output (``pytest``).
        """
        step = task.verifier_step
        _copy_into(step, container, log, self._docker)
        # After an in-container agent the verifier runs its /app code as root: no
        # proxy credentials then (the sidecar kept them from the agent; see :func:`exec_env_file`).
        agent_code = self.mode == "claude" and self.agent_location == "container"
        with exec_env_file(task, "verifier", net, credentials=not agent_code) as (flags, values):
            proc = self._docker(["exec", *_user(step), *flags, container, "bash", "-lc", step.command],
                                log, check=False, timeout=_task_verify_timeout(task), env=values)
        if verifier is not None:
            verifier["pytest"] = pytest_counts((proc.stdout or "") + (proc.stderr or ""))
        if step.parser != "reward-file":  # terminal-bench: the pytest summary is the verdict
            verdict, tests = _pytest_reward((proc.stdout or "") + (proc.stderr or ""))
            (output_dir / "verifier_tests.json").write_text(json.dumps(
                {"parser": step.parser, "exit_code": proc.returncode, "tests": tests}, indent=2), encoding="utf-8")
            return verdict
        reward, copy_error = pull_reward(self._docker, container, output_dir, log)
        if reward is not None:
            if verifier is not None:
                verifier["reward_file"] = True
            return reward
        if proc.returncode != 0:  # no verdict at all if the container is gone, not a 0
            running = self._docker(["inspect", "-f", "{{.State.Running}}", container], log, check=False)
            if running.timed_out:  # no answer is not "stopped": say so, the cell is errored either way
                raise RuntimeError(f"task container {container} state inspect timed out at verify time; see run.log")
            if (getattr(running, "stdout", "") or "").strip() != "true":
                raise RuntimeError(f"task container {container} is not running at verify time; see run.log")
        if copy_error:  # a failed or hung copy errors the cell: it is not "no reward file"
            raise RuntimeError(copy_error)
        return exit_code_verdict(proc.returncode)

    def _plan(self, task: TaskSpec, record: Optional[Path] = None) -> NetworkPlan:
        return resolve_recorded(task, self.network, agent=self.mode not in ("oracle", "null"), record=record)

    def _solve_in_container(self) -> bool:
        return self.mode == "oracle" or (self.mode != "null" and self.agent_location == "container")

    def _network_metrics(self, plan: NetworkPlan) -> dict[str, Any]:  # a host agent's solve is not ours
        skill = skill_needs(self._resolve_skill_dir(), plan) if self.mode == "claude" else None
        return {**plan.as_metrics(), "solve_enforced": self._solve_in_container(), "skill": skill}

    def _open_sandbox(self, plan: NetworkPlan, container: str, log) -> Optional[ContainerSandbox]:
        """The internal network and sidecar: always for an in-container agent, else when a phase needs one."""
        agent = self.mode not in ("oracle", "null") and self.agent_location == "container"
        if not (agent or needs_sandbox(plan, solve_in_container=self._solve_in_container())):
            return None
        return ContainerSandbox.open(container, self._docker, log, source_env=operator_source_env(),
                                     task_egress=list(plan.solve.hosts) if self.mode != "null" else [],
                                     with_agent=agent and self.mode == "claude",
                                     include_llm=agent, runner=self.runner)

    def _verify_phase(self, task: TaskSpec, net: PhasePolicy, container: str, output_dir: Path, log,
                      sandbox: Optional[ContainerSandbox], bridged: bool, network: dict[str, Any],
                      verifier: Optional[dict[str, Any]] = None) -> Optional[float]:
        """Stop the solve's sidecar, give the container the verifier's network, verify."""
        side = None
        if sandbox is not None:
            sandbox.stop_proxy(self._docker, log)  # nothing the solve was allowed carries over
        enter_phase(net, container, self._docker, log, bridged=bridged)
        if sandbox is not None and net.mode == "allowlist":
            side = sandbox.verifier_sidecar(self._docker, log, list(net.hosts))
        try:
            return self._verify(task, container, output_dir, log, net, verifier)
        finally:
            if side is not None:
                side.stop_proxy_quietly(self._docker, log)
                network["verify_egress"] = side.egress_summary()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _resolve_skill_dir(self) -> Optional[Path]:
        if not self.skill_path:
            return None
        # Relative to the operator's cwd, not the agent's. absolute(), not resolve():
        # a symlinked SKILL.md must not swap in the link target's directory.
        p = Path(self.skill_path).absolute()
        if p.is_file() and p.name != "SKILL.md":  # Claude Code loads SKILL.md only
            raise ValueError(f"skill path must be a skill directory or its SKILL.md: {p}")
        return p.parent if p.is_file() else p

    def _describe_command(self, task: TaskSpec, model: Optional[str]) -> list[str]:
        parts = ["docker-solver", self.mode, task.name]
        if model:
            parts += ["--model", model]
        if self.skill_path:
            parts += ["--skill", self.skill_path]
            if not self.skill_notice and self.mode == "claude":
                parts.append("--no-skill-notice")
        return parts


def _copy_into(step: ContainerStep, container: str, log, docker: "Docker") -> None:
    """Copy a step's files in. A directory's contents go to its dest: ``docker cp src c:dest``
    nests ``src`` inside a dest the image already has (``RUN mkdir -p /tests/...``), so the
    entry script would be missing. Same as BenchFlow: ``mkdir -p``, then ``src/.``."""
    dirs = [d if src.is_dir() else posixpath.dirname(d) for src, d in step.copies]
    docker(["exec", "-u", "root", container, "mkdir", "-p", *dict.fromkeys(dirs)], log)
    for src, dest in step.copies:
        docker(["cp", f"{src}/." if src.is_dir() else str(src), f"{container}:{dest}"], log)


def _user(step: ContainerStep) -> list[str]:
    return ["-u", step.user] if step.user else []


def _describe_timeout(exc: subprocess.TimeoutExpired) -> str:
    """``TimeoutExpired`` for run.log: its ``str()`` holds the raw argv."""
    cmd = exc.cmd if isinstance(exc.cmd, (list, tuple)) else [str(exc.cmd)]
    return (f"after {exc.timeout}s: "
            + " ".join(shlex.quote(_redact(str(a))) for a in cmd))


def _docker(args: list[str], log, *, check: bool = True, timeout: Optional[float] = None,
            runner: process.Runner = process.run, env: Optional[Mapping[str, str]] = None) -> process.Completed:
    """Run a ``docker`` subcommand, tee-ing output (also a killed step's) to the run log.

    ``timeout`` is a step's own budget (build, solve, verify): running out raises
    :class:`subprocess.TimeoutExpired`, and the caller decides what the cell is.
    Any other step gets :data:`DOCKER_STEP_TIMEOUT`; running out is exit 124,
    which ``check`` raises like any failure (an errored cell). The CLI keeps the
    caller's environment: its context, ``DOCKER_HOST`` and ``~/.docker``. Its output
    is kept whole, in memory: callers parse it (the sidecar's decision log), and it is
    redacted before run.log gets all of it. ``env`` joins the CLI's environment (see ``exec_env_file``).
    """
    argv = docker_cmd.argv(*args)
    budget = timeout if timeout is not None else DOCKER_STEP_TIMEOUT
    proc = runner(argv, timeout=budget, env=process.Env.inherit(env), capture_limit=None)
    log.write(f"$ docker {' '.join(shlex.quote(_redact(a)) for a in args)}\n")
    # The output too: a verifier or a build step can echo a proxy URL it was given.
    if proc.stdout:
        log.write(_redact(proc.stdout))
    if proc.stderr:
        log.write(_redact(proc.stderr))
    if proc.timed_out:
        log.write(f"[killed after {budget:g}s]\n")
        if timeout is not None:
            raise subprocess.TimeoutExpired(argv, timeout, proc.stdout_bytes, proc.stderr_bytes)
    if check and proc.returncode != 0:
        why = f"timed out after {budget:g}s" if proc.timed_out else f"exit {proc.returncode}"
        raise RuntimeError(f"docker {args[0]} failed ({why}); see run.log")
    return proc


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _host_plugin_dir(workspace: AgentWorkspace) -> Path:
    """The ``-host`` arm's plugin directory: under the workspace root, beside its work tree."""
    return workspace.root / "plugin"


def _usage_metrics(usage: Optional[UsageRecord]) -> dict[str, Any]:
    """The LLM usage of a claude solve as metrics; none for a cell without one."""
    if usage is None:
        return {}
    metrics: dict[str, Any] = dict(usage.as_metrics_dict())
    if usage.model:
        metrics["model"] = usage.model
    return metrics


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text

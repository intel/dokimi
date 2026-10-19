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
    :mod:`.skill_treatment`).
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
from typing import Any, Optional

from dokimi import process
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
from .command import _parse_reward_text
from .container_agent import AGENT_BIN, DOCKER_STEP_TIMEOUT, FILES_DIR, ContainerSandbox, Docker
from .models import HarnessResult, OperationRecord
from .skill_treatment import Treatment, build_plugin, check_skill, treatment_notice
from .solver_stream import record_solver, run_solver
from .tasks import (  # noqa: F401  (timeouts re-exported)
    _DEFAULT_BUILD_TIMEOUT,
    _DEFAULT_VERIFY_TIMEOUT,
    FORMAT_TASK_MD,
    TaskSpec,
    _is_public,
    _task_build_timeout,
    _task_verify_timeout,
)

# Recompile commands embedded in instruction.md, e.g. `g++ -O3 ... -o /app/bench`.
# C tasks (missing-restrict) use gcc, C++ tasks use g++.
_RECOMPILE_RE = re.compile(r"(g\+\+|gcc) [^`\n]*-o /app/[^ `\n]*")
_PROXY_VARS = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
_DEFAULT_SOLVE_TIMEOUT = 600.0
_DOCKER_STEP_TIMEOUT = DOCKER_STEP_TIMEOUT
_CONTAINER_PLUGIN = "/opt/abskill"
# The only tree a docker-* agent edits: the host agent's snapshot and copy-back,
# the in-container agent's chown and `-w`. Anything else (an exported
# SkillsBench task works in WORKDIR /root) would hand it root's home: .profile
# & co., which the root verifier's login shell sources.
_AGENT_TREE = "/app"
_IN_CONTAINER = "use docker-claude, whose agent runs in the task container"
_NO_ARM = "no docker-* arm runs such a task yet"


@dataclass(frozen=True)
class DockerSolverHarness:
    """Build → solve → verify a task with local Docker; emit reward + telemetry.

    ``mode`` is ``oracle``, ``claude`` or ``probe``. ``skill_path`` points at a
    skill directory or its ``SKILL.md``; in claude mode the skill tree, less its
    top-level ``evals/``, is installed as a plugin for the with-skill treatment
    arm (see :mod:`.skill_treatment`), in probe mode it is a path the probe must
    not reach.
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
    runner: process.Runner = field(default=process.run, compare=False, repr=False)

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
        steps = {"oracle": ("verifier", "oracle"), "claude": ("verifier",)}.get(self.mode, ())
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
                metrics={"dry_run": True, "mode": self.mode},
            )

        if output_dir is None:
            raise ValueError("DockerSolverHarness requires an output_dir")
        skill_dir = self._resolve_skill_dir()
        if self.mode == "claude" and skill_dir is not None:
            check_skill(skill_dir)  # a bad skill fails here, not after the image and network

        log_path = output_dir / "run.log"
        log = log_path.open("w", encoding="utf-8")
        usage: Optional[UsageRecord] = None
        image = f"dokimi/{task.name}:latest"
        container = f"dokimi-run-{task.name}-{os.getpid()}"
        solve_timeout = timeout_sec or task.timeout_sec or _DEFAULT_SOLVE_TIMEOUT
        start = time.time()
        returncode = 0
        extra_metrics: dict[str, Any] = {}
        reward: Optional[float] = None
        sandbox: Optional[ContainerSandbox] = None
        try:
            try:
                self._build_image(task, image, log)
            except subprocess.TimeoutExpired as exc:
                # The image never got built, so no agent ran and there is no
                # verdict: raise so the cell is errored like a build that fails
                # fast. Only a solve or verify timeout below is a reward-0 failure.
                log.write(f"\nBUILD TIMEOUT: {_describe_timeout(exc)}\n")
                raise RuntimeError(
                    f"docker build timed out after {exc.timeout}s; see run.log"
                ) from exc
            if self.mode in ("claude", "probe") and self.agent_location == "container":
                sandbox = ContainerSandbox.open(container, self._docker, log, source_env=operator_source_env(),
                                                task_egress=task.environment.get("egress"),
                                                with_agent=self.mode == "claude", runner=self.runner)
            try:
                self._start_container(task, image, container, log, sandbox)
                try:
                    if self.mode == "oracle":
                        self._solve_oracle(task, container, log, timeout=solve_timeout)
                    elif self.mode == "claude":
                        usage = self._solve_claude(
                            task, container, output_dir, model, solve_timeout,
                            operations, log, extra_metrics, sandbox,
                        )
                    elif self.mode == "probe":
                        reward = self._solve_probe(task, container, output_dir, log, extra_metrics,
                                                   sandbox)
                    else:
                        raise ValueError(f"unknown mode {self.mode!r}")
                    if sandbox is not None:
                        sandbox.stop_proxy(self._docker, log)  # the verifier gets no route out either
                        if _is_public(task):  # "public" is the verifier's, after the agent exits
                            self._docker(["network", "connect", "bridge", container], log)
                    if self.mode != "probe":  # the probe's reward is its own verdict
                        reward = self._verify(task, container, output_dir, log)
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
            # Nothing recorded yet (a build failure): let the runner mark it errored.
            # After a solve (a failed copy back), keep its cost and isolation record.
            if not extra_metrics:
                raise
            log.write(f"\nERROR: {type(exc).__name__}: {exc}\n")
            extra_metrics["error"] = f"{type(exc).__name__}: {exc}"
            returncode = 1
            reward = None
        finally:
            log.close()

        elapsed = time.time() - start
        metrics: dict[str, Any] = {"dry_run": False, "mode": self.mode}
        passed = reward is not None and reward >= 1.0
        if usage is not None:
            metrics.update(usage.as_metrics_dict())
            if usage.model:
                metrics["model"] = usage.model
        metrics.update(extra_metrics)
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

    def _docker(self, args: list[str], log, **kwargs: Any) -> process.Completed:
        """:func:`_docker` through this harness's runner."""
        return _docker(args, log, runner=self.runner, **kwargs)

    # ── pipeline steps ──────────────────────────────────────────────────────

    def _build_image(self, task: TaskSpec, image: str, log) -> None:
        """Build the task image; pass host proxy through (corporate networks).

        Task-bundled skills (``environment/skills``) are agent capabilities, not
        image content: a ``COPY . /x`` in the Dockerfile would otherwise bake
        them into the no-skill arm too. When a task ships them, the image is
        built from a copy of ``environment/`` without that directory — the
        same rule BenchFlow applies to its no-skill runs.
        """
        env_dir = task.path / "environment"
        build_args: list[str] = ["--network", "host"]
        for var in _PROXY_VARS:
            val = os.environ.get(var)
            if val:
                build_args += ["--build-arg", f"{var}={val}"]
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
                                ignore=_ignore_top_level_skills(env_dir))
                # Left empty rather than absent: some Dockerfiles `COPY skills
                # ...` (radar-vital-signs copies it into seven agent paths), and
                # a missing source fails the build in every arm.
                (context / "skills").mkdir()
            self._docker(
                ["build", "-q", *build_args, "-t", image,
                 "-f", str(context / "Dockerfile"), str(context)],
                log, timeout=timeout,
            )

    def _start_container(self, task: TaskSpec, image: str, container: str, log,
                         sandbox: Optional[ContainerSandbox] = None) -> None:
        # --network none unless the task declares environment.network_mode =
        # "public"; then bridge (NAT'd internet), not host, so host-local
        # services stay out of reach. Build always gets host network. An
        # in-container agent brings its own internal network + egress proxy,
        # and a "public" task joins bridge only for its verifier.
        net_args = (sandbox.run_args() if sandbox is not None
                    else ["--network", "bridge" if _is_public(task) else "none"])

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
        self._docker(
            ["run", "-d", "--name", container, *net_args,
             "--cpus", cpus, "--memory", memory, image, "sleep", str(lifetime)],
            log,
        )

    def _solve_oracle(self, task: TaskSpec, container: str, log,
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
        _copy_dir_into(task.solution_dir, container, ("/solution", "/oracle"), log, self._docker)
        self._docker(
            ["exec", "-u", "root", *_exec_env(task, "oracle"), container, "bash", "-lc",
             "chmod +x /solution/solve.sh && /solution/solve.sh"],
            log, check=False, timeout=timeout,
        )

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
        (output_dir / "reward.txt").unlink(missing_ok=True)
        if sandbox is not None:
            return self._solve_claude_in_container(task, container, output_dir, model, timeout,
                                                   operations, log, extra_metrics, sandbox)
        workdir = _require_agent_workdir(task, container, log, self._docker)
        workspace, treatment = self._prepare_workspace(task, container, log, extra_metrics,
                                                       output_dir=output_dir)
        extra_metrics["isolation"] = workspace.isolation()
        try:
            work = workspace.work
            plugin = str(workspace.root / "plugin") if treatment else None
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
            # Keep the agent's tree with the run (also after a timeout); the plugin is
            # beside it, not in it.
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
                stuck = subprocess.TimeoutExpired(proc.argv, _DOCKER_STEP_TIMEOUT)

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

    def _prepare_workspace(self, task: TaskSpec, container: str, log,
                           extra_metrics: Optional[dict[str, Any]] = None,
                           output_dir: Optional[Path] = None,
                           ) -> tuple[AgentWorkspace, Optional[Treatment]]:
        """Snapshot ``/app`` into a scratch work tree; install the arm's skill beside it.

        In probe mode the skill is not offered: it joins the paths the probe must not reach.
        """
        skill_dir = self._resolve_skill_dir()
        probing = self.mode == "probe" and skill_dir is not None
        forbidden = default_forbidden(task.path, [skill_dir] if probing and skill_dir else [],
                                      output_dir=output_dir)
        workspace = AgentWorkspace.create(forbidden=forbidden)
        try:
            self._docker(["cp", f"{container}:/app/.", str(workspace.work)], log)
            treatment = None
            if skill_dir is not None and not probing:
                treatment = build_plugin(skill_dir, workspace.root / "plugin")
                if extra_metrics is not None:
                    extra_metrics["treatment"] = treatment.as_metrics()
        except BaseException:  # also Ctrl-C: don't leave the dokimi-run-* snapshot behind
            workspace.cleanup()
            raise
        return workspace, treatment

    def _install_plugin_in_container(self, container: str, output_dir: Path, log,
                                     extra_metrics: dict[str, Any]) -> Optional[Treatment]:
        """Copy the arm's skill, as a plugin, to ``/opt/abskill`` (outside ``/app``)."""
        skill_dir = self._resolve_skill_dir()
        if skill_dir is None:
            return None
        staged = output_dir / "plugin"
        _rmtree(staged)
        try:
            treatment = build_plugin(skill_dir, staged)
            extra_metrics["treatment"] = treatment.as_metrics()
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

    def _claude_args(self, task: TaskSpec, treatment: Optional[Treatment],
                     plugin_dir: Optional[str], model: Optional[str],
                     output_dir: Path) -> list[str]:
        """``claude`` argv after the executable; the prompt is also saved as ``prompt.txt``."""
        prompt = (
            f"{treatment_notice(treatment)}"
            "Solve this coding task. Edit files in the current directory only. "
            "When done, leave the optimized source and any required outputs in place.\n\n"
            f"{task.instruction}"
        )
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
        if sandbox is not None:
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
                raise subprocess.TimeoutExpired(proc.argv, _DOCKER_STEP_TIMEOUT)
            if proc.returncode != 0:
                log.write(f"[recompile] FAILED (exit {proc.returncode}): {cc}\n")
                failed.append(cc)
        return failed

    def _verify(self, task: TaskSpec, container: str, output_dir: Path, log) -> Optional[float]:
        """Run the verifier in-container; pull /logs/verifier/reward.txt back out.

        Mounted at both ``/tests`` (Harbor) and ``/verifier`` (BenchFlow): a
        SkillsBench ``test.sh`` runs ``pytest /verifier/test_outputs.py`` even
        after ``bench tasks export`` has renamed the directory to ``tests/``.
        """
        _copy_dir_into(task.tests_dir, container, ("/tests", "/verifier"), log, self._docker)
        # After an in-container agent the verifier runs its /app code as root: no
        # proxy credentials then (the sidecar kept them from the agent; see :func:`_exec_env`).
        agent_code = self.mode == "claude" and self.agent_location == "container"
        proc = self._docker(
            ["exec", "-u", "root", *_exec_env(task, "verifier", credentials=not agent_code),
             container, "bash", "-lc",
             "mkdir -p /logs/verifier && chmod +x /tests/test.sh && /tests/test.sh"],
            log, check=False, timeout=_task_verify_timeout(task),
        )
        reward_file = output_dir / "reward.txt"
        pulled = self._docker(
            ["cp", f"{container}:/logs/verifier/reward.txt", str(reward_file)],
            log, check=False,
        )
        if pulled.timed_out:  # not "no reward file": the exit code would pass a failing verifier
            raise subprocess.TimeoutExpired(pulled.argv, _DOCKER_STEP_TIMEOUT)
        if pulled.returncode == 0 and reward_file.is_file():
            return _parse_reward_text(
                reward_file.read_text(encoding="utf-8", errors="replace"), reward_file=True
            )
        if proc.returncode != 0:  # no verdict at all if the container is gone, not a 0
            running = self._docker(["inspect", "-f", "{{.State.Running}}", container], log, check=False)
            if (getattr(running, "stdout", "") or "").strip() != "true":
                raise RuntimeError(f"task container {container} is not running at verify time; see run.log")
        # No reward file written: fall back to verifier exit code.
        return 1.0 if proc.returncode == 0 else 0.0

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
        return parts


def _refuse_unrunnable(task: TaskSpec, mode: str, agent_location: str) -> None:
    """Refuse, from the task file alone, what a docker-* agent cannot run.

    - A ``task.md`` task, for a host-side agent: its instruction names absolute
      container paths (``/root/...``, ``/app/...``) and the agent runs on the
      host in a copy of ``/app``, so it writes to the operator's machine and the
      verifier, in the container, finds nothing: a false fail plus a host write.
      The in-container agent runs such a task when it works in ``/app``.
    - A declared ``environment.workdir`` outside ``/app``, for either agent:
      both edit ``/app`` only (see :func:`_require_agent_workdir` for the
      image's own ``WORKDIR``, which needs the built image).

    - A task without the directory a mode copies in: ``tests/`` (``verifier/``)
      for the verifier, and ``solution/`` (``oracle/``) for ``docker-oracle``.
      ``docker cp`` would fail only at that step, after the image build and,
      for ``docker-claude``, the paid agent run.

    A probe is refused where its ``claude`` arm is: it would vouch for an arm
    that never runs. It copies neither directory. Oracle mode is refused only
    for a missing directory.
    """
    if mode != "probe":
        needed = [("tests", task.tests_dir)] + ([("solution", task.solution_dir)] if mode == "oracle" else [])
        for role, directory in needed:
            if not directory.is_dir():
                raise ValueError(f"{task.name}: no {directory.name}/ directory to copy to /{role}")
    if mode not in ("claude", "probe"):
        return
    if agent_location == "host" and task.format == FORMAT_TASK_MD:
        raise ValueError(
            f"{task.name}: a task.md task cannot be solved by a host-side agent "
            f"(its instruction names absolute container paths); {_IN_CONTAINER}"
        )
    declared = _declared_workdir(task)
    if declared is not None and not _in_agent_tree(declared):
        raise ValueError(
            f"{task.name}: a docker-* agent edits only {_AGENT_TREE}; this task "
            f"declares environment.workdir = {declared!r}; {_NO_ARM}"
        )


def _require_agent_workdir(task: TaskSpec, container: str, log, docker: "Docker") -> str:
    """The task's working tree, refused before the agent starts unless it is in ``/app``.

    Both agents edit ``/app`` only: the host one a snapshot it copies back, the
    in-container one the tree it is ``chown``ed. An image that works in
    ``/root`` (77 of 101 SkillsBench images; a ``bench tasks export`` copy
    loads as Harbor) would otherwise get an agent in an ``/app`` that does not
    hold the task (a paid verified 0), or a ``chown`` that fails after the
    build. A ``WORKDIR`` inside ``/app`` (``/app/workspace``) runs, as on main;
    an image without that directory is refused with its name. This needs the
    built image, so ``--dry-run`` cannot see it.
    """
    workdir = _task_workdir(task, container, log, docker)
    if not _in_agent_tree(workdir):
        raise ValueError(
            f"{task.name}: the task works in {workdir}, but a docker-* agent edits "
            f"only {_AGENT_TREE}; {_NO_ARM}"
        )
    # Exit 1 is "no such directory"; 126/127 (no `test` in the image) leaves it to the copy.
    if docker(["exec", "-u", "root", container, "test", "-d", workdir], log,
              check=False).returncode == 1:
        raise ValueError(
            f"{task.name}: the image has no {workdir}, the tree a docker-* agent edits; {_NO_ARM}"
        )
    return workdir


def _in_agent_tree(path: str) -> bool:
    """``path`` (normalised) is ``/app`` or below it."""
    return path == _AGENT_TREE or path.startswith(_AGENT_TREE + "/")


def _declared_workdir(task: TaskSpec) -> Optional[str]:
    """``environment.workdir`` normalised (``/.``, ``/app/..``), or ``None``."""
    declared = task.environment.get("workdir")
    if not (isinstance(declared, str) and declared.startswith("/")):
        return None
    return "/" + posixpath.normpath(declared).lstrip("/")  # normpath keeps a leading "//"


def _task_workdir(task: TaskSpec, container: str, log, docker: "Docker") -> str:
    """The task's working tree: ``environment.workdir``, else the image's
    ``WORKDIR``, else ``/app`` (the Harbor convention our own tasks follow).
    The agent works there only if it is ``/app`` or below it (see
    :func:`_require_agent_workdir`)."""
    declared = _declared_workdir(task)
    if declared is not None and declared != "/":
        # A declared "/" (also "/." or "/app/..") is ignored, like an image
        # WORKDIR of "/": a snapshot would `docker cp` the whole filesystem.
        return declared
    proc = docker(["inspect", "-f", "{{.Config.WorkingDir}}", container], log, check=False)
    if proc.timed_out:  # an unknown WORKDIR is not /app: the cell is refused, not billed on a guess
        raise subprocess.TimeoutExpired(proc.argv, _DOCKER_STEP_TIMEOUT)
    found = (proc.stdout or "").strip()
    if proc.returncode != 0 or not found.startswith("/"):
        return "/app"
    found = "/" + posixpath.normpath(found).lstrip("/")
    return found if found != "/" else "/app"


def _copy_dir_into(src: Path, container: str, dests: tuple[str, ...], log, docker: "Docker") -> None:
    """Copy *src*'s contents to each dest: ``docker cp src c:dest`` nests ``src``
    inside a dest the image already has (``RUN mkdir -p /tests/...``), so the
    entry script would be missing. Same as BenchFlow: ``mkdir -p``, then ``src/.``."""
    docker(["exec", "-u", "root", container, "mkdir", "-p", *dests], log)
    for dest in dests:
        docker(["cp", f"{src}/.", f"{container}:{dest}"], log)


def _exec_env(task: TaskSpec, table: str, *, credentials: bool = True) -> list[str]:
    """``docker exec -e`` flags for the oracle or verifier step.

    A public-network task gets the host's proxy variables: behind a corporate
    proxy a verifier that installs ``uv`` or pytest at verify time (52 of 101
    SkillsBench ``test.sh`` install both, 90 install something) otherwise fails
    with reward 0 (measured on ``parallel-tfidf-search``). They go on the exec,
    not on ``docker run``, so they reach only the steps the task's
    ``network_mode`` is about. With ``credentials=False`` (the verifier after
    an in-container agent, which runs the agent's code) their ``user:pw@`` is
    dropped: the egress sidecar holds the credentials so the agent never sees
    them, and a planted ``conftest.py`` must not read them at verify time.

    Then the task's ``[verifier.env]`` / ``[oracle.env]``: only literal values
    are forwarded; BenchFlow's ``${VAR}`` host references are left to
    BenchFlow, so a secret is never read from our environment on a task
    file's say-so.
    """
    flags: list[str] = []
    if str(task.environment.get("network_mode") or "none") == "public":
        for var in _PROXY_VARS:
            val = os.environ.get(var)
            if val:
                if not credentials:
                    val = _USERINFO_RE.sub(r"\1", val)
                flags += ["-e", f"{var}={val}"]
    source = task.verifier if table == "verifier" else task.oracle
    env = source.get("env")
    if not isinstance(env, dict):
        return flags
    for key, value in env.items():
        if isinstance(value, bool):
            flags += ["-e", f"{key}={'true' if value else 'false'}"]  # YAML true, not Python True
        elif isinstance(value, (str, int, float)) and "${" not in str(value):
            flags += ["-e", f"{key}={value}"]
    return flags


def unresolved_env(task: TaskSpec, table: str) -> list[str]:
    """Names in ``[verifier.env]`` / ``[oracle.env]`` that :func:`_exec_env`
    does not forward: a ``${VAR}`` host reference (with or without a default),
    or a value that is not a scalar (list, table, null)."""
    source = task.verifier if table == "verifier" else task.oracle
    env = source.get("env")
    if not isinstance(env, dict):
        return []
    return sorted(
        str(k) for k, v in env.items()
        if not isinstance(v, (str, int, float)) or (isinstance(v, str) and "${" in v)
    )


def _ignore_top_level_skills(env_dir: Path):
    """``shutil.copytree`` ignore hook that drops only ``<env_dir>/skills``."""
    def ignore(directory: str, names: list[str]) -> list[str]:
        return ["skills"] if Path(directory) == env_dir and "skills" in names else []
    return ignore


# userinfo after a scheme or right after "VAR=" (a scheme-less proxy); greedy,
# so a raw "@" or "=" in the password (base64 "==") is covered up to the last
# "@" before the host.
_CREDENTIALS_RE = re.compile(r"(://|=)[^/\s]+@")
# The same userinfo in a bare proxy value (no "VAR="), to drop it.
_USERINFO_RE = re.compile(r"(^|://)[^/\s]+@")


def _redact(arg: str) -> str:
    """Hide ``user:password@`` in proxy URLs written to run.log.

    The pattern needs ``://`` or ``VAR=`` before the userinfo; a step that
    echoes a scheme-less proxy on its own (``u:pw@proxy:912``) is caught by
    the host's own proxy userinfo, matched literally.
    """
    out = _CREDENTIALS_RE.sub(r"\1***@", arg)
    for var in _PROXY_VARS:
        found = _USERINFO_RE.search(os.environ.get(var, ""))
        userinfo = found.group(0)[len(found.group(1)):-1] if found else ""
        if ":" in userinfo:  # a password: a bare user name is too short to match safely
            out = out.replace(userinfo + "@", "***@")
    return out


def _describe_timeout(exc: subprocess.TimeoutExpired) -> str:
    """``TimeoutExpired`` for run.log: its ``str()`` holds the raw argv."""
    cmd = exc.cmd if isinstance(exc.cmd, (list, tuple)) else [str(exc.cmd)]
    return (f"after {exc.timeout}s: "
            + " ".join(shlex.quote(_redact(str(a))) for a in cmd))


def _docker(args: list[str], log, *, check: bool = True, timeout: Optional[float] = None,
            runner: process.Runner = process.run) -> process.Completed:
    """Run a ``docker`` subcommand, tee-ing output (also a killed step's) to the run log.

    ``timeout`` is a step's own budget (build, solve, verify): running out raises
    :class:`subprocess.TimeoutExpired`, and the caller decides what the cell is.
    Any other step gets :data:`_DOCKER_STEP_TIMEOUT`; running out is exit 124,
    which ``check`` raises like any failure (an errored cell). The CLI keeps the
    caller's environment: its context, ``DOCKER_HOST`` and ``~/.docker``. Its output
    is kept whole, in memory: callers parse it (the sidecar's decision log), and it is
    redacted before run.log gets all of it.
    """
    argv = ["docker", *args]
    budget = timeout if timeout is not None else _DOCKER_STEP_TIMEOUT
    proc = runner(argv, timeout=budget, env=process.Env.inherit(), capture_limit=None)
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


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text

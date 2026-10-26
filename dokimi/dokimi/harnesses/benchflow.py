# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""BenchFlow harness — run a task through ``bench eval run`` (SkillsBench's runner).

BenchFlow runs the agent *inside* the task sandbox over ACP, injects skills
into the harness's native skill directory, and records reward, tokens, cost
and skill invocations in a per-rollout ``result.json``. This adapter shells out
to the ``bench`` CLI and folds that file into a :class:`HarnessResult`, so a
BenchFlow cell sits in the same ``task_runs.v1`` artifact (and the same McNemar
comparison) as the ``docker-*`` and Harbor cells.

Harness ids::

    benchflow:<agent>                    no-skill arm
    benchflow-skill:<agent>              with-skill, the task's own environment/skills
    benchflow-skill:<agent>:<skills_dir> with-skill, an external skills root
    benchflow-selfgen:<agent>[:<dir>]    BenchFlow's self-generated-skill arm, with an
                                         optional skill-creator dir

``<agent>`` is any BenchFlow agent id (``bench agent list``), including
``oracle`` and the ``<ns>:<id>`` shorthand (``acp:pi``, ``ai-sdk:codex``).
Both task layouts work: BenchFlow reads ``task.toml`` and ``task.md`` alike.

A cell is one attempt (``--retry-attempts 0``). It is a verdict only if the
agent attempted the task (the oracle, a tool call, completion tokens, or an
agent message/thought/tool call in the ACP trajectory) and the verifier or the
agent's own budget decided it; everything else is ``metrics.error``. See
"BenchFlow Cells" in docs/coding-harnesses.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Optional

from dokimi import process

from .command import clear_run_dir
from .harbor_export import harbor_view
from .models import HarnessResult, OperationRecord, PreflightError
from .tasks import TaskSpec

SKILL_MODE_NO_SKILL = "no-skill"
SKILL_MODE_WITH_SKILL = "with-skill"
SKILL_MODE_SELF_GEN = "self-gen"

# Agent budget for a task that declares no agent.timeout_sec. BenchFlow would
# use int(None or 0) = 0 s (rollout/__init__.py:1082), i.e. a ~1 s watchdog and
# a 0 verdict; docker-* defaults to the same 600 s.
_AGENT_DEFAULT_TIMEOUT = 600
# BenchFlow 0.7.8's per-attempt hard deadline (rollout/_deadline.py):
# agent x turns + verifier + build + install + a 1800 s margin, with these
# fallbacks for undeclared budgets. The adapter sets that deadline explicitly
# (BENCHFLOW_ROLLOUT_HARD_DEADLINE) and puts its own kill above it, so the order
# holds even for task shapes it cannot see (scenes, a user loop).
_BF_AGENT_DEADLINE_DEFAULT = 900.0  # read from the task file; the override is not
_BF_VERIFIER_DEFAULT = 600.0  # VerifierConfig.timeout_sec's field default
_BF_BUILD_DEFAULT = 600.0
_BF_INSTALL_SEC = 900.0  # claude-agent-acp, codex-acp, gemini, opencode, ...
_BF_INSTALL_SEC_BY_AGENT = {"mimo": 1200.0, "prime-agent": 1200.0}
_BF_MARGIN_SEC = 1800.0
_HARD_DEADLINE_ENV = "BENCHFLOW_ROLLOUT_HARD_DEADLINE"
# Job-level work outside the rollout: docker prune, the host LiteLLM start and
# its 180 s health wait, and the 120 s abandoned-cleanup grace.
_JOB_OVERHEAD_SEC = 600.0
# An attempt is a verdict only if the agent did something; these ACP events
# show a model response reached it.
_ATTEMPT_EVENTS = frozenset({"agent_message", "agent_thought", "tool_call"})
# Text claude-agent-acp forwards as an agent message although no model reply
# came: Claude Code's synthetic error replies (measured: "API Error: ...",
# "Failed to authenticate. API Error: 403 ...", "Credit balance is too low"; the
# rest read from the CLI) and its notices. BenchFlow does not advertise
# sessionFailure, so they arrive as ordinary agent_message chunks.
_SYNTHETIC_MESSAGE_PREFIXES = (
    "API Error", "Failed to authenticate", "Credit balance is too low",
    "Invalid API key", "Invalid auth token", "Please run /login", "Prompt is too long",
    "AWS credentials", "You've hit your", "**Auto mode unavailable",
)
_SKILL_KINDS = (None, "", "other", "tool", "skill")
# BenchFlow starts a LiteLLM proxy on the host and the in-sandbox agent calls
# it on the docker bridge. Behind a corporate HTTP proxy, calls to the bridge
# that go through the proxy are refused. The agent's own calls are governed by
# the Docker client config's noProxy (that is where the measured "Access
# Denied" came from; see docs/coding-harnesses.md). On the host side, bench's
# health check also went to the proxy (403 on /health/liveliness), but
# BenchFlow counts any status below 500 as healthy (litellm_runtime.py:830),
# so that did not fail the run. These hosts bypass the proxy for bench and
# the LiteLLM it starts all the same.
_BRIDGE_NO_PROXY = ("172.17.0.1", "host.docker.internal")
_PROXY_VARS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
_PATH_VARS = ("BENCHFLOW_SKILL_CREATOR_DIR", "DOCKER_CONFIG")  # read from the env; see _subprocess_env


@dataclass(frozen=True)
class BenchflowHarness:
    """Run one task with ``bench eval run`` and parse its ``result.json``."""

    harness_id: str
    agent: str
    skill_mode: str = SKILL_MODE_NO_SKILL
    skills_dir: Optional[str] = None
    skill_creator_dir: Optional[str] = None
    sandbox: str = "docker"
    kind: str = "benchflow"
    env: dict[str, str] = field(default_factory=dict)
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
        # bench runs with cwd=<cell>: every path absolute; the row records the dir as given.
        task = harbor_view(task)  # a terminal-bench v1 task runs from its Harbor copy
        given_dir = Path(output_dir) if output_dir else None
        output_dir = given_dir.resolve() if given_dir else None
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        jobs_dir = (output_dir / "jobs") if output_dir else Path("jobs")
        skills = creator = None
        if self.skill_mode == SKILL_MODE_WITH_SKILL:
            skills = self._skills_root(task, output_dir, stage=not dry_run)
        if self.skill_mode == SKILL_MODE_SELF_GEN and self.skill_creator_dir:
            creator = self._stage_skill_creator(output_dir, stage=not dry_run)
        command = self.build_command(task, model=model, jobs_dir=jobs_dir, skills_root=skills,
                                     creator_dir=creator)
        operations = [
            OperationRecord(
                type="harness",
                name=self.harness_id,
                metadata={"kind": self.kind, "task": task.name, "model": model,
                          "agent": self.agent, "skill_mode": self.skill_mode},
            )
        ]
        if dry_run:
            return HarnessResult(
                harness=self.harness_id, task=task.name, command=command, returncode=0,
                elapsed_sec=0.0, reward=None, passed=False,
                output_dir=str(given_dir) if given_dir else None,
                operations=operations, metrics={"dry_run": True, "skill_mode": self.skill_mode},
            )
        if output_dir is None or given_dir is None:
            raise ValueError("BenchflowHarness requires an output_dir")

        env = _subprocess_env(self.env, self.agent, runner=self.runner)
        if timeout_sec:
            timeout = timeout_sec
        else:
            # BenchFlow's own deadline, set rather than guessed: it then fires
            # first whatever the task's shape, and names itself in bench's
            # stderr ([HARD-DEADLINE]). An exported value is kept.
            computed = hard_deadline(task, self.agent, self.skill_mode)
            exported = env.get(_HARD_DEADLINE_ENV)
            if exported is None:
                env[_HARD_DEADLINE_ENV] = f"{computed:.0f}"
            deadline = max(_float(exported, 0.0), 0.0) or computed
            timeout = outer_budget(task, self.agent, self.skill_mode, deadline)
        # The cell's output dir is deterministic, so a previous run's rollout
        # would otherwise be read back when this one dies before writing its
        # own (install failure, auth, full disk) — reported as a pass.
        clear_run_dir(jobs_dir)
        start = time.time()
        # The whole stderr goes to a file, read once for the error line: the
        # capture keeps only its head and tail, and the reason can be between.
        stderr_file = output_dir / _STDERR_FILE
        try:
            returncode, stdout, stderr, timed_out = _run_bench(command, timeout, env, cwd=output_dir,
                                                               runner=self.runner, stderr_to=stderr_file)
            with open(stderr_file, encoding="utf-8", errors="replace") as lines:
                reason = _error_line(lines)
        finally:
            stderr_file.unlink(missing_ok=True)
        if timed_out:
            stderr += f"\nTIMEOUT after {timeout:.0f}s"
            removed = remove_rollout_containers(jobs_dir, runner=self.runner)
            if removed:
                stderr += f"\nremoved leftover BenchFlow containers: {', '.join(removed)}"
        elapsed = time.time() - start
        (output_dir / "bench.log").write_text(stdout + stderr, encoding="utf-8")

        results = rollout_results(jobs_dir)
        n_attempts = len(rollout_dirs(jobs_dir))
        result_path = results[-1] if results else None
        result = _load_json(result_path) if result_path else None
        reward, metrics = parse_result(result)
        treatment = (treatment_names(Path(skills)) if skills else None)
        if result_path:
            metrics.update(skill_loads(result_path.parent, metrics.get("n_skill_invocations_benchflow"),
                                       treatment=treatment))
            attempted = self.agent == "oracle" or agent_attempted(result_path.parent, metrics)
            reward = _verdict(reward, result or {}, metrics, attempted=attempted)
            budget = _float(task.timeout_sec, 0.0)
            if (self.agent == "oracle" and reward is None and budget
                    and f"Command timed out after {budget:.0f} seconds" in str((result or {}).get("error") or "")):
                # BenchFlow kills an oracle solve.sh at agent.timeout_sec as an
                # exec timeout (infra_failure) and skips the verifier. Only that
                # exec: another one timing out (a setup hook) keeps its own error.
                metrics["error"] = (
                    f"benchflow: oracle solve.sh exceeded agent.timeout_sec ({budget:.0f}s); "
                    "BenchFlow killed it and did not verify"
                )
        metrics.update({"dry_run": False, "timeout_sec": timeout, "skill_mode": self.skill_mode,
                        "n_attempts": n_attempts})
        if result_path:
            metrics["result_json"] = str(given_dir / result_path.relative_to(output_dir))
        if task.timeout_sec is None and self.agent != "oracle":
            metrics["agent_timeout_defaulted_sec"] = _AGENT_DEFAULT_TIMEOUT
        if timed_out:
            # Our kill sits above BenchFlow's own hard deadline, so reaching it
            # means bench wedged: no verdict, whatever an attempt left behind.
            reward = None
            metrics["timed_out"] = True
            metrics["error"] = f"benchflow: bench killed after {timeout:.0f}s (outer budget)"
        elif n_attempts > 1:
            # --retry-attempts 0 makes one cell one attempt; more means the
            # flag stopped working, and the row would mix attempts.
            reward = None
            metrics["error"] = f"benchflow: {n_attempts} attempts in one cell (expected 1)"
        if reward is None and "error" not in metrics:
            # No rollout verdict at all (bench crashed before writing one, or
            # timed out): missing data, which the runner keeps out of McNemar.
            if result_path:
                metrics["error"] = f"BenchFlow result.json has no rewards.reward (exit {returncode})"
            else:
                # bench failed before writing a rollout (a rubric it rejects, a
                # bad task, its own hard deadline): its stderr says why.
                metrics["error"] = (f"no BenchFlow result.json (exit {returncode})"
                                    + (f": {reason}" if reason else ""))
        if result:
            operations.append(
                OperationRecord(
                    type="loop", name="benchflow-rollout",
                    elapsed_sec=float((result.get("timing") or {}).get("total") or 0.0),
                    metadata={"n_tool_calls": metrics.get("n_tool_calls"),
                              "n_skill_invocations": metrics.get("n_skill_invocations")},
                )
            )
        return HarnessResult(
            harness=self.harness_id,
            task=task.name,
            command=command,
            returncode=returncode,
            elapsed_sec=elapsed,
            reward=reward,
            passed=reward is not None and reward >= 1.0,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            output_dir=str(given_dir),
            operations=operations,
            metrics=metrics,
        )

    def preflight(self) -> None:
        """Fail the sweep once, up front, when ``bench`` or Compose v2 is missing.

        Without this every cell errors with the same ``docker: unknown command:
        docker compose``, which reads like a run where every task broke. The
        runner calls it once per harness class, and not on a dry run.
        """
        for cmd, fix in (
            (["bench", "--version"], "install benchflow (pip install benchflow)"),
            (["docker", "compose", "version"],
             "install the Docker Compose v2 plugin (docker-compose-plugin, or the "
             "binary in $DOCKER_CONFIG/cli-plugins)"),
        ):
            try:
                proc = self.runner(cmd, timeout=_PREFLIGHT_TIMEOUT, env=process.Env.inherit())
            except OSError as exc:
                raise PreflightError(
                    f"benchflow preflight: `{' '.join(cmd)}` failed ({exc}); {fix}"
                ) from exc
            if proc.timed_out:
                raise PreflightError(
                    f"benchflow preflight: `{' '.join(cmd)}` failed (timed out after "
                    f"{_PREFLIGHT_TIMEOUT}s); {fix}"
                )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()[:1]
                raise PreflightError(
                    f"benchflow preflight: `{' '.join(cmd)}` exited {proc.returncode}"
                    f"{': ' + detail[0] if detail else ''}; {fix}"
                )

    def _skills_root(self, task: TaskSpec, output_dir: Optional[Path], *, stage: bool) -> str:
        """The ``--skills-dir`` to pass: a directory of ``<name>/SKILL.md`` packs.

        The task's own ``environment/skills`` is passed as-is, so BenchFlow
        records it as ``task_bundled``. An external path is staged as a real
        copy under the cell's output dir: BenchFlow copies the root into the
        image build context skipping symlinks (sandbox/setup.py), so a
        symlinked pack would be dropped (measured:
        ``skill_deployment_missing``), and a single skill dir (or its
        ``SKILL.md``) is not a root at all.
        """
        if not self.skills_dir:
            if task.skills_dir is None:
                raise ValueError(
                    f"{self.harness_id}: task {task.name!r} ships no environment/skills; "
                    "name a skills root as benchflow-skill:<agent>:<skills_dir>"
                )
            self._require_packs(Path(task.skills_dir))
            return str(task.skills_dir)
        src = Path(self.skills_dir).expanduser()
        if src.is_file():
            src = src.parent
        if not src.is_dir():
            raise FileNotFoundError(f"{self.harness_id}: skills dir not found: {src}")
        if not (src / "SKILL.md").is_file():
            self._require_packs(src)
        if output_dir is None:
            return str(src)
        root = output_dir / "skills"
        if not stage:
            # A dry run shows the command the real run would use.
            return str(root)
        if root.exists():
            shutil.rmtree(root)
        if (src / "SKILL.md").is_file():
            shutil.copytree(src, root / src.resolve().name)
        else:
            shutil.copytree(src, root)
        return str(root)

    def _require_packs(self, root: Path) -> None:
        """Refuse a root BenchFlow would deploy as "no skills".

        BenchFlow expects ``<root>/<name>/SKILL.md``. A root one level too high
        (a checkout instead of its ``skills/``) gives it an empty expected
        set: its fidelity check passes on two empty lists and the arm runs
        with no skill while the id says otherwise.
        """
        if not any(root.glob("*/SKILL.md")):
            raise ValueError(
                f"{self.harness_id}: {root} holds no <name>/SKILL.md pack; pass the "
                "directory whose children are skill dirs (often <checkout>/skills), "
                "a skill dir, or its SKILL.md"
            )

    def _creator_path(self) -> Path:
        # Absolute against the caller's cwd (bench runs in the cell), but not
        # symlink-resolved: BenchFlow scans the given dir's parent, and a
        # resolved link would point it at the target's neighbours instead.
        return Path(str(self.skill_creator_dir)).expanduser().absolute()

    def _creator_skill_dir(self) -> Path:
        """The one skill-creator pack the id names.

        BenchFlow's ``_resolve_skill_creator_root`` accepts a dir holding
        ``SKILL.md``, one holding ``skill-creator/SKILL.md``, or one with
        exactly one ``<name>/SKILL.md``; anything else (a typo included)
        silently falls back to ``BENCHFLOW_SKILL_CREATOR_DIR`` and the host's
        ``~/.claude/skills``, so the arm would not use the creator its id names.
        """
        path = self._creator_path()
        if (path / "SKILL.md").is_file():
            return path
        if (path / "skill-creator" / "SKILL.md").is_file():
            return path / "skill-creator"
        packs = ([c for c in path.iterdir() if c.is_dir() and (c / "SKILL.md").is_file()]
                 if path.is_dir() else [])
        if len(packs) == 1:
            return packs[0]
        raise FileNotFoundError(
            f"{self.harness_id}: no skill-creator SKILL.md in {path} (BenchFlow would "
            "fall back to the host's skill-creator); pass the skill-creator dir itself"
        )

    def _stage_skill_creator(self, output_dir: Optional[Path], *, stage: bool) -> str:
        """The ``--skill-creator-dir`` to pass: the named creator, alone.

        BenchFlow treats the given dir's parent as a skills root and prefers a
        ``skill-creator`` pack there (``self_gen._find_skill_creator_dir``), so
        a creator with a stock ``skill-creator/`` next to it would lose to it.
        A copy under ``<cell>/creator/`` has no neighbours.
        """
        src = self._creator_skill_dir()
        if output_dir is None:
            return str(src)
        # Named after the pack itself, not a link to it: BenchFlow deploys the dir
        # under that name and prompts with the frontmatter name, which match there.
        dest = output_dir / "creator" / src.resolve().name
        if stage:
            shutil.rmtree(output_dir / "creator", ignore_errors=True)
            shutil.copytree(src, dest)
        return str(dest)

    def build_command(
        self, task: TaskSpec, *, model: Optional[str], jobs_dir: Path,
        skills_root: Optional[str] = None, creator_dir: Optional[str] = None,
    ) -> list[str]:
        cmd = [
            "bench", "eval", "run",
            "--tasks-dir", str(task.path),
            "--agent", self.agent,
            "--sandbox", self.sandbox,
            "--skill-mode", self.skill_mode,
            "--jobs-dir", str(jobs_dir),
            # BenchFlow's default max_retries=2 reruns the whole rollout (agent
            # included) after idle timeouts, ACP and verifier-infra errors, even
            # when the attempt has a reward. One cell must be one attempt, like
            # a docker-*/Harbor cell; --repeats is the only repetition.
            # (Help text says "Reserved", but cli/main.py maps it to max_retries.)
            "--retry-attempts", "0",
            # BenchFlow's default idle watchdog ends a prompt after 600 s without
            # a tool call, message or thought, long before a larger task budget;
            # docker-* has only the wall-clock budget. 0 disables it (the
            # prompt then runs under the task's wall-clock budget alone).
            "--agent-idle-timeout", "0",
            "--quiet",
        ]
        if model and self.agent != "oracle":
            cmd += ["--model", model]
        if self.agent != "oracle" and task.timeout_sec is None:
            cmd += ["--config-override",
                    json.dumps({"agent": {"timeout_sec": _AGENT_DEFAULT_TIMEOUT}})]
        if self.skill_mode == SKILL_MODE_WITH_SKILL:
            if skills_root is None:
                skills_root = self._skills_root(task, None, stage=False)
            cmd += ["--skills-dir", skills_root]
        if self.skill_mode == SKILL_MODE_SELF_GEN and self.skill_creator_dir:
            cmd += ["--skill-creator-dir", creator_dir or str(self._creator_path())]
        return cmd


def parse_result(result: Optional[dict[str, Any]]) -> tuple[Optional[float], dict[str, Any]]:
    """``(reward, metrics)`` from one BenchFlow rollout ``result.json``.

    The reward is ``rewards.reward``. A rollout that errored *before* the
    verifier produced a reward (agent install, provider auth, sandbox start)
    is reported as ``metrics.error`` so it is excluded from the paired test,
    like a ``docker build`` failure; a rollout that ran and scored 0 is a
    verdict, whatever BenchFlow's error field says.
    """

    if not result:
        return None, {}
    rewards = result.get("rewards") or {}
    raw = rewards.get("reward") if isinstance(rewards, dict) else None
    reward = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
    # BenchFlow's own extract_reward: with integrated review, a result whose
    # scoring did not complete has no reward, whatever rewards.reward says.
    scoring = result.get("scoring")
    if scoring is not None and not (isinstance(scoring, dict) and scoring.get("status") == "complete"):
        reward = None
    agent = result.get("agent_result") or {}
    metrics: dict[str, Any] = {
        # BenchFlow's n_input_tokens includes cache reads and writes (and 0.7.8
        # adds the writes twice), unlike docker-claude's uncached prompt_tokens.
        "prompt_tokens": _int(agent.get("n_input_tokens")),
        "completion_tokens": _int(agent.get("n_output_tokens")),
        "cache_read_tokens": _int(agent.get("n_cache_read_tokens")),
        "cache_write_tokens": _int(agent.get("n_cache_creation_tokens")),
        "total_tokens": _int(agent.get("total_tokens")),
        "n_tool_calls": _int(result.get("n_tool_calls")),
        # BenchFlow's own count (every skill call it recognised); the row's
        # n_skill_invocations is the treatment-only count from skill_loads.
        "n_skill_invocations_benchflow": _int(result.get("n_skill_invocations")),
        "skill_source": result.get("skill_source"),
        "usage_source": agent.get("usage_source"),
        "benchflow_task_digest": result.get("task_digest"),
        "verifier_error": (str(result["verifier_error"])[:500]
                           if result.get("verifier_error") else None),
        "verifier_error_category": result.get("verifier_error_category"),
        "partial_trajectory": result.get("partial_trajectory"),
    }
    cost = agent.get("cost_usd")
    if isinstance(cost, (int, float)):
        metrics["cost_usd"] = float(cost)
    if agent.get("usage_source") == "unavailable":
        # BenchFlow writes zeros when it captured no usage; those are unknown,
        # not 0 tokens, and must not enter per_harness.cost as such.
        for key in _USAGE_KEYS:
            metrics.pop(key, None)
    error = result.get("error")
    if error:
        metrics["benchflow_error"] = str(error)[:500]
        metrics["benchflow_error_category"] = result.get("error_category")
        if reward is None:
            metrics["error"] = f"benchflow: {result.get('error_category') or 'error'}: {str(error)[:200]}"
    elif reward is None and metrics.get("verifier_error"):
        # e.g. "verifier timed out after 20.0s", rather than "no rewards.reward"
        metrics["error"] = (f"benchflow: {metrics.get('verifier_error_category') or 'verifier_error'}: "
                            f"{metrics['verifier_error'][:200]}")
    elif reward is None and scoring is not None:
        metrics["error"] = "benchflow: scoring incomplete"
    return reward, {k: v for k, v in metrics.items() if v is not None}


_TIMEOUT_CATEGORIES = frozenset({"timeout", "idle_timeout"})
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "cache_read_tokens",
               "cache_write_tokens", "total_tokens", "cost_usd")


def _verdict(reward: Optional[float], result: dict[str, Any], metrics: dict[str, Any], *,
             attempted: bool) -> Optional[float]:
    """Apply the cell rule to one attempt: a verdict iff the agent attempted the
    task and the verifier (or the agent's own budget) decided it.

    * BenchFlow backfills reward 0.0 on an agent timeout. If no model response
      ever reached the agent (a provider that never answers), that is a
      provider failure, not the arm's: error, like ``solver_error`` in
      docker-claude.
    * An agent timeout after an attempt is a 0, as for docker-*, whatever the
      verifier that BenchFlow still runs then does: a reward it reads is kept
      as ``metrics.reward_after_timeout``, and a verifier that fails (no
      reward) stays in ``metrics.verifier_error``.
    * A verifier timeout after an attempt is a 0, as for docker-*
      (``docs/coding-harnesses.md``); BenchFlow leaves the reward empty.
    """
    category = result.get("error_category")
    if category in _TIMEOUT_CATEGORIES:
        if reward:
            metrics["reward_after_timeout"] = reward
        if not attempted:
            metrics["error"] = (
                f"benchflow: {category} before any model response (no attempt): "
                f"{str(result.get('error') or '')[:150]}"
            )
            return None
        # docker-* scores a solve timeout 0 without verifying, so neither the
        # verifier's figure nor its failure changes the verdict here.
        metrics.pop("error", None)
        return 0.0
    if reward is None and attempted and metrics.get("verifier_error_category") == "verifier_timeout":
        metrics.pop("error", None)
        metrics["verifier_timeout"] = True
        return 0.0
    return reward


def agent_attempted(rollout_dir: Path, metrics: dict[str, Any]) -> bool:
    """Whether any model response reached the agent in this attempt."""
    if (metrics.get("n_tool_calls") or 0) > 0 or (metrics.get("completion_tokens") or 0) > 0:
        return True
    for event in _trajectory_events(rollout_dir):
        if event.get("type") not in _ATTEMPT_EVENTS:
            continue
        # A synthetic error reply or notice is not a model response.
        if (event.get("type") == "agent_message"
                and str(event.get("text") or "").lstrip().startswith(_SYNTHETIC_MESSAGE_PREFIXES)):
            continue
        return True
    return False


def hard_deadline(task: TaskSpec, agent: str, skill_mode: str) -> float:
    """BenchFlow 0.7.8's per-attempt hard deadline for a one-turn task (two for
    self-gen), computed as ``rollout/_deadline.py`` does: an undeclared agent
    budget counts 900 s there, since it re-reads the task file and not the
    ``--config-override``, and the install budget is the agent's own."""
    agent_sec = float(task.timeout_sec or _BF_AGENT_DEADLINE_DEFAULT)
    turns = 2 if skill_mode == SKILL_MODE_SELF_GEN else 1
    verifier = _float(task.verifier.get("timeout_sec"), _BF_VERIFIER_DEFAULT)
    build = _float(task.environment.get("build_timeout_sec"), _BF_BUILD_DEFAULT)
    install = (0.0 if agent == "oracle"
               else _BF_INSTALL_SEC_BY_AGENT.get(agent.split()[0], _BF_INSTALL_SEC))
    return turns * agent_sec + verifier + build + install + _BF_MARGIN_SEC


def outer_budget(task: TaskSpec, agent: str, skill_mode: str,
                 deadline: Optional[float] = None) -> float:
    """Wall-clock budget for the ``bench`` subprocess: BenchFlow's hard deadline
    (the one the adapter sets, or ``deadline``) plus job overhead."""
    if deadline is None:
        deadline = hard_deadline(task, agent, skill_mode)
    return deadline + _JOB_OVERHEAD_SEC


def _float(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


_TIMEOUT_RC = process.TIMEOUT_RC
_PREFLIGHT_TIMEOUT = 30
_STDERR_FILE = "bench.stderr"
_DOCKER_PS_TIMEOUT = 10.0
_DOCKER_RM_TIMEOUT = 60.0
# How long bench gets to exit after SIGTERM, and after SIGKILL.
_KILL_GRACE_SEC = 15.0


class BenchRun(NamedTuple):
    """One ``bench`` invocation. ``timed_out`` is set only by our own kill; a
    ``bench`` that exits 124 by itself is not a timeout."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _run_bench(command: list[str], timeout: float, env: dict[str, str],
               cwd: Optional[Path] = None, runner: process.Runner = process.run,
               stderr_to: Optional[Path] = None) -> BenchRun:
    """Run ``bench`` with exactly ``env``; on a timeout stop its whole process group.

    The group gets ``SIGTERM`` and :data:`_KILL_GRACE_SEC` to exit, then ``SIGKILL``:
    a kill of ``bench`` alone would leave its ``docker compose`` children (and
    their output pipes) behind. ``bench`` reads ``./.env`` ahead of the exported
    env (agents/env.py), so it runs in ``cwd`` (the cell), where there is none,
    not in the directory cli.py was started from.
    """
    done = runner(command, timeout=timeout, env=process.Env.only((), env), cwd=cwd,
                  term_grace=_KILL_GRACE_SEC, stderr_to=stderr_to)
    return BenchRun(done.returncode, done.stdout, done.stderr, done.timed_out)


def remove_rollout_containers(jobs_dir: Path, runner: process.Runner = process.run) -> list[str]:
    """Remove containers of the rollouts this cell started, after a timeout.

    Killing ``bench`` does not stop its sandboxes: they belong to the Docker
    daemon and keep their disk. BenchFlow names each rollout's compose project
    after its rollout directory (``<task>__<id>``, measured), so only projects
    whose directory is under this cell's ``jobs/`` are touched — never another
    run of the same task.
    """
    removed: list[str] = []
    if not jobs_dir.is_dir():
        return removed
    for rollout in rollout_dirs(jobs_dir):
        project = _compose_project_name(rollout.name)
        # Bounded like BenchFlow's own _force_kill_project: a wedged daemon
        # must not hang the sweep after we already gave up on bench.
        try:
            found = runner(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                           timeout=_DOCKER_PS_TIMEOUT, env=process.Env.inherit())
            ids = found.stdout.split() if not found.timed_out else []
            if ids:
                gone = runner(["docker", "rm", "-f", *ids], timeout=_DOCKER_RM_TIMEOUT,
                              env=process.Env.inherit())
                if not gone.timed_out:
                    removed.append(project)
        except OSError:
            continue
    return removed


def _compose_project_name(name: str) -> str:
    """BenchFlow's ``_sanitize_docker_compose_project_name`` (sandbox/docker.py)."""
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9_-]", "-", name)


def _trajectory_events(rollout_dir: Path) -> list[dict[str, Any]]:
    path = next(
        (p for p in (rollout_dir / "trajectory" / "acp_trajectory.jsonl",
                     rollout_dir / "agent" / "acp_trajectory.jsonl") if p.is_file()),
        None,
    )
    if path is None:
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def treatment_names(skills_root: Path) -> Optional[frozenset[str]]:
    """Names the arm's treatment skills answer to: each pack's dir name and
    its frontmatter ``name``. ``None`` when the root holds no pack."""
    from dokimi.utils import parse_frontmatter

    names: set[str] = set()
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        names.add(skill_md.parent.name)
        try:
            meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 — a bad frontmatter still has its dir name
            meta = {}
        if isinstance(meta.get("name"), str) and meta["name"]:
            names.add(meta["name"])
    return frozenset(names) or None


def skill_loads(rollout_dir: Path, reported: Optional[int], *,
                treatment: Optional[frozenset[str]] = None) -> dict[str, Any]:
    """Skill loads observed in the rollout's ACP trajectory.

    benchflow 0.7.8 counts a claude-agent-acp skill call only when its title is
    ``Skill``; the claude-agent-acp it pins (0.73.0) titles it ``Load skill``
    and carries the name in ``raw_input.skill``, so BenchFlow reports
    ``n_skill_invocations = 0`` for runs that did load the skill. When BenchFlow
    does recognise a call it rewrites ``kind`` to ``"skill"``.

    ``skills_loaded`` lists every load; ``n_skill_invocations`` counts loads of
    the *treatment* skills only: every pack in the deployed root, a distractor
    placed there included, but not skills outside it or the self-gen creator.
    It is omitted when the treatment is not known.
    BenchFlow's own figure is kept as ``n_skill_invocations_benchflow``.
    """
    events = _trajectory_events(rollout_dir)
    if not events:
        return {}
    names: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.get("type") != "tool_call":
            continue
        # Same gate BenchFlow puts on its own content sniffing (plus its own
        # "skill" rewrite): a failed call (unknown skill) loaded nothing.
        if event.get("kind") not in _SKILL_KINDS or event.get("status") == "failed":
            continue
        # kind "skill" is BenchFlow's own verdict (gemini, opencode, ...): trust
        # it, and its tool-specific "name". Any other kind is claude-agent-acp's
        # "Load skill" shape: "skill" in the input, "Launching skill" output.
        recognised = event.get("kind") == "skill"
        raw = event.get("raw_input")
        name = (raw.get("skill") or (raw.get("name") if recognised else None)
                ) if isinstance(raw, dict) else None
        output = event.get("raw_output")
        launched = isinstance(output, str) and output.startswith("Launching skill")
        if not recognised and not (str(event.get("title") or "").startswith("Load skill") or launched):
            # Not claude-agent-acp's shape: some other tool with a "skill" input.
            continue
        if not recognised and isinstance(output, str) and output and not launched:
            # claude-agent-acp's shape, but nothing launched ("Unknown skill: ...").
            continue
        call_id = event.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            if call_id in seen:
                continue
            seen.add(call_id)
        if isinstance(name, str) and name:
            names.append(name)
    out: dict[str, Any] = {"skills_loaded": names}
    if reported is not None:
        out["n_skill_invocations_benchflow"] = reported
    if treatment:
        out["n_skill_invocations"] = sum(1 for n in names if n in treatment)
    return out


def rollout_dirs(jobs_dir: Path) -> list[Path]:
    """Every rollout dir (``<job>/<task>__<id>``) under a jobs dir, whether or not
    it got as far as a ``result.json`` (a kill or a hard deadline leaves none)."""
    if not jobs_dir.is_dir():
        return []
    return sorted(p for p in jobs_dir.glob("*/*__*") if p.is_dir())


def rollout_results(jobs_dir: Path) -> list[Path]:
    """Every rollout ``result.json`` under a BenchFlow jobs dir, oldest first."""
    if not jobs_dir.is_dir():
        return []
    return sorted(jobs_dir.glob("*/*/result.json"), key=lambda p: p.stat().st_mtime)


def _subprocess_env(extra: dict[str, str], agent: Optional[str] = None,
                    runner: process.Runner = process.run) -> dict[str, str]:
    """The env for ``bench``: behind a proxy, both ``no_proxy`` spellings get one
    deduplicated union of the user's two lists plus the bridge hosts (urllib,
    requests and httpx read the lowercase one first, Go tools the uppercase)."""
    env = {**os.environ, **extra}
    # rich wraps bench's stderr at 80 columns, which cuts the reason off the
    # line metrics.error quotes; and `oracle` is not in bench's local agent
    # registry, so bench first clones benchflow-ai/agents into <cell>/.cache
    # (4 MB, one per cell) and prints a banner. Neither is wanted in a cell.
    # COLUMNS is set, not defaulted: an exported COLUMNS=80 would wrap again.
    env["COLUMNS"] = "400"
    if agent == "oracle":
        env.setdefault("BENCHFLOW_AGENTS_SOURCE", "off")
    # bench runs in the cell, so a relative path in these would be read against
    # the cell: a self-gen arm would then fall back to the host's skill-creator,
    # and docker would lose the Compose plugin. Anchor them at the caller's cwd.
    for var in _PATH_VARS:
        value = env.get(var)
        if value and not Path(value).expanduser().is_absolute():
            env[var] = str(Path(value).expanduser().absolute())
    # BENCHFLOW_AGENTS_SOURCE is a local dir or an owner/repo@ref spec.
    source = env.get("BENCHFLOW_AGENTS_SOURCE")
    if source and not Path(source).expanduser().is_absolute() and Path(source).expanduser().is_dir():
        env["BENCHFLOW_AGENTS_SOURCE"] = str(Path(source).expanduser().absolute())
    if any(env.get(var) for var in _PROXY_VARS):
        own = {var: [h.strip() for h in (env.get(var) or "").split(",") if h.strip()]
               for var in ("no_proxy", "NO_PROXY")}
        hosts: list[str] = []
        for h in [*own["no_proxy"], *own["NO_PROXY"], _bridge_gateway(runner) or "", *_BRIDGE_NO_PROXY]:
            if h and h != "*" and h not in hosts:
                hosts.append(h)
        for var, other in (("no_proxy", "NO_PROXY"), ("NO_PROXY", "no_proxy")):
            # urllib and requests honour "*" only as the whole value. Keep it
            # where the user set it (a spelling left unset follows the other),
            # rather than spreading one spelling's "*" to the other's list.
            entries = own[var] if env.get(var) is not None else own[other]
            env[var] = "*" if "*" in entries else ",".join(hosts)
    return env


def _bridge_gateway(runner: process.Runner = process.run) -> Optional[str]:
    """The docker bridge gateway, as BenchFlow finds it (``docker network
    inspect bridge``); hosts with a custom ``bip`` do not use 172.17.0.1."""
    try:
        proc = runner(["docker", "network", "inspect", "bridge", "-f",
                       "{{range .IPAM.Config}}{{.Gateway}}{{end}}"],
                      timeout=10, env=process.Env.inherit())
    except OSError:
        return None
    gateway = proc.stdout.strip()
    return gateway if proc.ok and gateway else None


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError covers JSONDecodeError and the int-string limit
        return None
    return data if isinstance(data, dict) else None


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# Once a job has started, bench's stderr is progress (the job line, sandbox
# set-up) until something fails, and the reason is on a "[HARD-DEADLINE] " or
# "[ERR] " line. Before a job starts (a rubric or a task bench rejects) the
# reason is the first line after the agent-registry banner.
_REASON_PREFIXES = ("[HARD-DEADLINE] ", "[ERR] ")
_PROGRESS_PREFIXES = (
    "Cloning ", "Refreshing ", "Agent manifest auto-load", "Job: ", "Job complete:",
    "Starting environment", "Setting up sandbox", "Sandbox user", "Oracle mode",
    "Docker prune failed",
)


def _error_line(lines: Iterable[str], limit: int = 300) -> str:
    """The line of bench's stderr that says why a cell left no ``result.json``.

    One pass over ``lines`` (the stderr file, of any size): the first line of the
    earliest prefix in :data:`_REASON_PREFIXES`, else the first non-progress line.
    """
    reasons: dict[str, str] = {}
    other = ""
    for raw in lines:
        line = raw.strip()
        prefix = next((p for p in _REASON_PREFIXES if line.startswith(p)), None)
        if prefix:
            reasons.setdefault(prefix, line)
            if prefix == _REASON_PREFIXES[0]:
                break
        elif line and not other and not line.startswith(_PROGRESS_PREFIXES):
            other = line
    return next((reasons[p] for p in _REASON_PREFIXES if p in reasons), other)[:limit]


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text

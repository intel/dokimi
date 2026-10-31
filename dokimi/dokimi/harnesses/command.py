# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Command-backed coding harness adapters."""

from __future__ import annotations

import json
import math
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokimi import process
from dokimi.utils import retry_writable

from .models import HarnessResult, OperationRecord
from .operations import load_operations
from .harbor_export import harbor_view
from .tasks import TaskSpec, harbor_task_problem


_REWARD_RE = re.compile(r"(?:reward|score|pass[_ -]?rate)\D+([01](?:\.\d+)?)", re.IGNORECASE)
_PLACEHOLDERS = (
    "harness",
    "task",
    "task_path",
    "instruction",
    "model",
    "output_dir",
    "prompt",
    "prompt_escaped",
    "prompt_quoted",
)

MAX_TIMEOUT_SEC = 3600.0
# How long a timed-out `harbor` gets after SIGTERM to remove its trial container and image,
# which belong to the Docker daemon and would outlive a kill.
HARBOR_TERM_GRACE_SEC = 60.0


@dataclass(frozen=True)
class CommandHarness:
    """Run a coding task through an external CLI command."""

    harness_id: str
    command_template: str
    kind: str = "command"
    default_timeout_sec: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    agent: str | None = None  # the Harbor agent id, for kind="harbor"
    runner: process.Runner = field(default=process.run, compare=False, repr=False)

    def run(
        self,
        task: TaskSpec,
        *,
        model: str | None = None,
        output_dir: Path | None = None,
        timeout_sec: float | None = None,
        dry_run: bool = False,
    ) -> HarnessResult:
        if self.kind == "harbor":
            task = harbor_view(task)  # a terminal-bench v1 task runs from its Harbor copy
            # Harbor's own rejection of the directory exits 1, which would read
            # as a fail; refuse first, so the cell is an error (missing data).
            problem = harbor_task_problem(task, needs_solution=self.agent == "oracle")
            if problem:
                raise ValueError(f"{task.name}: Harbor-backed harness cannot run it: {problem}")
        given_dir = Path(output_dir) if output_dir else None
        # Absolute before it reaches the command or env: the agent runs with
        # cwd=task_root.parent, so a relative dir (the `tasks run` default) would
        # land its reward and operations in the task checkout, and the reward
        # read below would miss them and fall back to the exit code.
        output_dir = given_dir.absolute() if given_dir else None
        if output_dir:
            # The cell's dir is deterministic: a reward left by a previous run
            # would otherwise be read back as this run's.
            if not dry_run:
                clear_run_dir(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        command = self.build_command(task, model=model, output_dir=output_dir)
        # The command is the operator's own CLI (Harbor, a wrapper script): it runs with the
        # caller's whole environment, whose credentials are what it runs with.
        env = dict(self.env)
        if output_dir:
            env["DOKIMI_OUTPUT_DIR"] = str(output_dir)
            env["DOKIMI_OPERATIONS_FILE"] = str(output_dir / "operations.jsonl")

        operations = [
            OperationRecord(
                type="harness",
                name=self.harness_id,
                metadata={"kind": self.kind, "task": task.name, "model": model},
            )
        ]
        start = time.time()
        if dry_run:
            elapsed = time.time() - start
            return HarnessResult(
                harness=self.harness_id,
                task=task.name,
                command=command,
                returncode=0,
                elapsed_sec=elapsed,
                reward=None,
                passed=False,
                output_dir=str(given_dir) if given_dir else None,
                operations=operations,
                metrics={"dry_run": True},
            )

        timeout = timeout_sec or task.timeout_sec or self.default_timeout_sec or MAX_TIMEOUT_SEC
        task_root = task.path.parent
        if task_root.parent == task_root:
            raise ValueError(f"Task path must be inside a task root directory: {task.path}")
        # A timeout kills the command's whole process tree (an agent's tool calls too), and
        # so does its exit: no process it started outlives the cell. A docker container is
        # the daemon's, not the tree's: only a command that stops its own within
        # its SIGTERM grace (harbor) leaves none behind. The output is captured
        # whole: the reward and DOKIMI_OP markers are read from it.
        if timeout > 0:
            grace = HARBOR_TERM_GRACE_SEC if self.kind == "harbor" else 0.0
            proc = self.runner(command, cwd=task_root.parent, env=process.Env.inherit(env), timeout=timeout,
                               capture_limit=None, term_grace=grace)
        else:
            # A task budget of 0 or less has no time to run: a timeout, as on main, not an error.
            proc = process.Completed(tuple(command), process.TIMEOUT_RC, timed_out=True)
        timed_out, returncode = proc.timed_out, proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
        if timed_out:
            # The agent ran out of time: a verified failure (reward 0, rc 124 as
            # the docker harness records it), not an errored cell. Raising
            # would reach the runner's catch-all and take the cell out of the
            # pass-rate denominator, scoring an arm that hangs above one that
            # fails fast.
            stderr += f"\nTIMEOUT: {command[0]} killed after {timeout:g}s\n"
        elapsed = time.time() - start
        operations.extend(load_operations(output_dir, stdout, stderr))
        harbor_metrics: dict[str, Any] = {}
        if timed_out:
            reward: float | None = 0.0
            passed = False
        elif self.kind == "harbor":
            # Harbor exits 0 for a trial that errored, so neither its exit code
            # nor its printed summary is a verdict: its trial result.json is.
            reward, harbor_metrics = read_harbor_job(output_dir, returncode)
            if "result_json" in harbor_metrics and given_dir and output_dir:
                harbor_metrics["result_json"] = str(
                    given_dir / Path(harbor_metrics["result_json"]).relative_to(output_dir))
            passed = reward is not None and reward >= 1.0
        else:
            reward = _find_reward(output_dir, stdout, stderr)
            passed = reward is not None and reward >= 1.0
            if reward is None:
                passed = returncode == 0
                if _exit_is_verdict(returncode) and not _has_reward_file(output_dir):
                    reward = 1.0 if passed else 0.0

        metrics: dict[str, Any] = {
            "dry_run": False,
            "timeout_sec": timeout,
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
        }
        metrics.update(harbor_metrics)
        if timed_out:
            metrics["timed_out"] = True
        return HarnessResult(
            harness=self.harness_id,
            task=task.name,
            command=command,
            returncode=returncode,
            elapsed_sec=elapsed,
            reward=reward,
            passed=passed,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            output_dir=str(given_dir) if given_dir else None,
            operations=operations,
            metrics=metrics,
        )

    def build_command(
        self,
        task: TaskSpec,
        *,
        model: str | None,
        output_dir: Path | None,
    ) -> list[str]:
        context: dict[str, Any] = {
            "harness": self.harness_id,
            "task": task.name,
            # Forward slashes: shlex.split below would eat a Windows path's backslashes.
            "task_path": task.path.as_posix(),
            "instruction": task.instruction,
            "model": model or "",
            "output_dir": output_dir.as_posix() if output_dir else "",
        }
        prompt = _task_prompt(task)
        context["prompt"] = prompt
        context["prompt_escaped"] = prompt.replace("{", "{{").replace("}", "}}")
        context["prompt_quoted"] = shlex.quote(prompt)
        rendered = _render_template(self.command_template, context)
        return shlex.split(rendered)


def clear_run_dir(path: Path) -> None:
    """Delete a cell's previous run dir, or refuse to run the cell.

    The dir is deterministic, so a ``reward.txt``, ``result.json`` or
    ``operations.jsonl`` a previous run left there would be read back as this
    run's. Raising makes the cell errored (missing data) instead of a verdict
    taken from stale files. Pass only the cell's own dir, never ``--output-dir``.
    """
    cause = ""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        cause = f" ({type(exc).__name__}: {exc})"
        # A read-only file (git objects) is not deletable on Windows: retry writable.
        shutil.rmtree(path, onerror=retry_writable)
    if path.exists() or path.is_symlink():
        raise RuntimeError(
            f"could not clear {path}, left by a previous run{cause}; a reward or "
            "result in it would be read as this run's, so the cell was not run. "
            "Delete it by hand (check the owner and permissions of what is left, "
            "or on Windows whether another process holds a file open) and rerun."
        )


def read_harbor_job(jobs_dir: Path | None, returncode: int) -> tuple[float | None, dict[str, Any]]:
    """``(reward, metrics)`` for one Harbor cell, from the ``result.json`` files
    ``harbor run --jobs-dir <jobs_dir>`` wrote.

    Harbor writes ``<jobs_dir>/<job>/result.json`` (the job summary) and
    ``<jobs_dir>/<job>/<trial>/result.json`` per trial; the trial's file is the
    authoritative one. The reward is its ``verifier_result.rewards.reward``. A
    trial with ``exception_info`` (agent install, environment start, agent
    timeout) at the top or on any of its ``step_results``, a trial with no
    reward, a cell with no trial file at all, or with more than one trial, has
    no verdict: ``metrics.error`` is set and the
    reward is None, so the runner counts the cell as errored, never as a pass
    because ``harbor`` exited 0.
    """
    if jobs_dir is None:
        return None, {"error": "harbor: no output dir, so no Harbor result.json to read"}
    trials = sorted(jobs_dir.glob("*/*/result.json"))
    metrics: dict[str, Any] = {"n_trials": len(trials)}
    if not trials:
        found = "a job result.json but no trial result.json" if any(jobs_dir.glob("*/result.json")) \
            else "no Harbor result.json"
        metrics["error"] = f"harbor: {found} (exit {returncode})"
        return None, metrics
    if len(trials) > 1:
        metrics["error"] = f"harbor: {len(trials)} trials in one cell (expected 1)"
        return None, metrics
    metrics["result_json"] = str(trials[0])
    try:
        trial = json.loads(trials[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError covers bad JSON, bad UTF-8 and the int-string limit
        metrics["error"] = "harbor: trial result.json is unreadable or not valid JSON"
        return None, metrics
    if not isinstance(trial, dict):
        metrics["error"] = "harbor: trial result.json is not a JSON object"
        return None, metrics
    # A multi-step trial keeps its usage and a step's exception on step_results only;
    # its top-level reward is the mean over the steps that reached their verifier.
    raw_steps = trial.get("step_results")
    steps = [s for s in raw_steps if isinstance(s, dict)] if isinstance(raw_steps, list) else []
    agent_result = trial.get("agent_result")
    metrics.update(_step_usage(steps) if agent_result is None and steps else harbor_usage(agent_result))
    reward = _harbor_reward(trial)
    raised = [i for i in [trial.get("exception_info"), *(s.get("exception_info") for s in steps)] if i]
    if raised and all(_is_verifier_timeout(i) for i in raised):
        # The verifier overran its timeout: a verified failure, as docker-* and BenchFlow
        # score it (the report footnote says so), not missing data.
        metrics["verifier_timeout"] = True
        return 0.0, metrics
    # The label is the first exception that is not a verifier timeout, so a timeout
    # beside a real failure does not hide it.
    exception = next((i for i in raised if not _is_verifier_timeout(i)), None)
    if exception:
        info = exception if isinstance(exception, dict) else {}
        kind = str(info.get("exception_type") or "unknown")
        message = str(info.get("exception_message") or "").strip().splitlines()
        metrics["harbor_exception"] = kind
        metrics["error"] = f"harbor: {kind}" + (f": {message[0][:200]}" if message else "")
        if reward is not None:
            metrics["reward_after_exception"] = reward
        return None, metrics
    if reward is None:
        metrics["error"] = "harbor: trial result.json has no verifier_result.rewards.reward"
        return None, metrics
    return reward, metrics


def harbor_usage(agent_result: Any) -> dict[str, Any]:
    """Token and cost metrics from a Harbor trial's ``agent_result``, under the
    keys the other harnesses use. A null count is unknown and left out, not 0.

    Harbor's ``n_input_tokens`` includes the cached prompt (``n_cache_tokens``),
    as BenchFlow's does, unlike docker-claude's uncached ``prompt_tokens``.
    """
    if not isinstance(agent_result, dict):
        return {}
    prompt = _count(agent_result.get("n_input_tokens"))
    completion = _count(agent_result.get("n_output_tokens"))
    metrics: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_read_tokens": _count(agent_result.get("n_cache_tokens")),
        "total_tokens": prompt + completion if prompt is not None and completion is not None else None,
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}
    cost = _number(agent_result.get("cost_usd"))
    if cost is not None:
        try:
            metrics["cost_usd"] = float(cost)
        except OverflowError:  # beyond float range: unknown, not a value to add up
            pass
    return metrics


def _is_verifier_timeout(info: Any) -> bool:
    return isinstance(info, dict) and info.get("exception_type") == "VerifierTimeoutError"


def _step_usage(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Usage summed over the steps whose agent ran; a key one of them lacks is unknown."""
    usages = [harbor_usage(s.get("agent_result")) for s in steps if isinstance(s.get("agent_result"), dict)]
    if not usages:
        return {}
    keys = set(usages[0]).intersection(*usages[1:])
    return {key: sum(u[key] for u in usages) for key in keys}


def _harbor_reward(trial: dict[str, Any]) -> float | None:
    """Verifier result reward: must be finite and in [0, 1], else None (treated as missing)."""
    verifier = trial.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    value = rewards.get("reward") if isinstance(rewards, dict) else None
    number = _number(value)  # already finite, so inf/nan are None here
    # Reward must be in [0, 1]
    if number is not None and 0.0 <= number <= 1.0:
        return float(number)
    return None


def _number(value: Any) -> int | float | None:
    """Extract a finite number (token count or cost). Non-finite (inf/nan) or negative values return None."""
    # bool is an int subclass: True is not a token count or a reward.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    # An int is always finite, and math.isfinite() would raise OverflowError on a huge one.
    if isinstance(value, int):
        return value if value >= 0 else None
    # Reject non-finite and negative values (negative token counts or costs corrupt sums)
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _count(value: Any) -> int | None:
    """A token count: a whole, non-negative number, else None. A fractional count is
    dropped rather than truncated (0.5 would silently become 0)."""
    number = _number(value)
    if number is None or (isinstance(number, float) and not number.is_integer()):
        return None
    return int(number)


def _render_template(template: str, context: dict[str, Any]) -> str:
    escaped = template.replace("{", "{{").replace("}", "}}")
    for key in _PLACEHOLDERS:
        escaped = escaped.replace("{{" + key + "}}", "{" + key + "}")
    return escaped.format(**context)


def _task_prompt(task: TaskSpec) -> str:
    return (
        "Solve this coding task. Work only inside the task environment. "
        "When finished, leave the files ready for the verifier.\n\n"
        f"# Task: {task.name}\n\n{task.instruction}"
    )


def _exit_is_verdict(returncode: int) -> bool:
    """False for a command that never ran (126, 127) or was killed by a signal."""
    return not (returncode < 0 or returncode in (126, 127) or 128 < returncode <= 192)


def _has_reward_file(output_dir: Path | None) -> bool:
    if not output_dir:
        return False
    return any(next(output_dir.rglob(name), None) for name in ("reward.txt", "reward.json"))


def _find_reward(output_dir: Path | None, stdout: str, stderr: str) -> float | None:
    if output_dir:
        candidates = sorted(output_dir.rglob("reward.txt")) + sorted(
            output_dir.rglob("reward.json")
        )
        for path in candidates:
            parsed = _parse_reward_text(
                path.read_text(encoding="utf-8", errors="replace"), reward_file=True
            )
            if parsed is not None:
                return parsed
    parsed = _parse_reward_text(stdout)
    return parsed if parsed is not None else _parse_reward_text(stderr)


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


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text

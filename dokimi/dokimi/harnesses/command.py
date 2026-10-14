# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Command-backed coding harness adapters."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import HarnessResult, OperationRecord
from .operations import load_operations
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


@dataclass(frozen=True)
class CommandHarness:
    """Run a coding task through an external CLI command."""

    harness_id: str
    command_template: str
    kind: str = "command"
    default_timeout_sec: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    agent: str | None = None  # the Harbor agent id, for kind="harbor"

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
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

        command = self.build_command(task, model=model, output_dir=output_dir)
        env = os.environ.copy()
        env.update(self.env)
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
        timed_out = False
        try:
            proc = subprocess.run(
                command,
                cwd=str(task_root.parent),
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            # The agent ran out of time: a verified failure (reward 0, rc 124 as
            # the docker harness records it), not an errored cell. Raising
            # would reach the runner's catch-all and take the cell out of the
            # pass-rate denominator, scoring an arm that hangs above one that
            # fails fast.
            timed_out = True
            returncode = 124
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr) + f"\nTIMEOUT: {exc}\n"
        elapsed = time.time() - start
        operations.extend(load_operations(output_dir, stdout, stderr))
        if timed_out:
            reward: float | None = 0.0
            passed = False
        else:
            reward = _find_reward(output_dir, stdout, stderr)
            passed = reward is not None and reward >= 1.0
            if reward is None:
                passed = returncode == 0

        metrics = {
            "dry_run": False,
            "timeout_sec": timeout,
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
        }
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


def _text(data: str | bytes | None) -> str:
    """Captured output of a timed-out run; ``TimeoutExpired`` may carry bytes
    even when the process ran with ``text=True``."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run the ``claude -p --output-format stream-json`` solver and record what it did.

Each cell's output directory gets ``solver.jsonl`` (the whole stream) and
``solver.json`` (the result event alone, empty when there is none), and its
metrics get ``tool_calls``, ``offered_skills`` and, for the skill arm,
``n_skill_invocations``; ``solver_error`` when the tooling failed (the CLI
ended with an error, or the skill arm's plugin did not load). A timed-out
solve keeps the stream it produced before it was killed, so a cell that ran
out of time still shows what the agent did; it is marked ``solver_timeout``,
because running out of time is a model miss, and dropping those cells would
flatter the arm that times out more. A timed-out cell whose plugin did not
load carries both.

The stream holds every tool output, so a credential the agent printed (``env``,
``cat`` of a credential file) would be in it. The values of the agent's
credential variables are replaced with ``[REDACTED]`` before it is written;
the contents of credential *files* are not known here, so do not share
``solver.jsonl`` outside the people who may see those credentials.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from dokimi import process
from dokimi.metrics.usage import UsageRecord

from .claude_json import _parse_claude_json, result_event, solver_error, terminal_reason
from .models import OperationRecord
from .skill_treatment import Treatment, stream_stats, treatment_loaded

_REDACTED = "[REDACTED]"
_MIN_SECRET_LEN = 8  # shorter values ("1", a region) would mangle unrelated text


def run_solver(argv: list[str], output_dir: Path, extra_metrics: dict[str, Any],
               treatment: Optional[Treatment], secrets: Iterable[str] = (), *,
               timeout: float, env: dict[str, str], cwd: Optional[str] = None,
               runner: process.Runner = process.run) -> process.Completed:
    """Run the solver with exactly ``env``, stdin closed; its whole process tree is killed
    on a timeout, and its output is kept whole.

    A timed-out solve records the stream it produced, then raises
    :class:`subprocess.TimeoutExpired` (the cell is a reward-0 failure).
    """
    secrets = list(secrets)
    # Whole, not the runner's 1 MiB head and tail: the stream is parsed for its telemetry
    # and saved. In memory only, as before: it is redacted before it is written.
    proc = runner(argv, timeout=timeout, env=process.Env.only((), env), cwd=cwd, capture_limit=None)
    if proc.timed_out:
        record_stream(proc.stdout, output_dir, extra_metrics, treatment, timed_out=timeout,
                      secrets=secrets)
        raise subprocess.TimeoutExpired(argv, timeout, proc.stdout_bytes, proc.stderr_bytes)
    return proc


def redact(text: str, secrets: Iterable[str]) -> str:
    """*text* with each secret of at least :data:`_MIN_SECRET_LEN` chars replaced, longest first.

    The stream is JSON, which stores a newline, quote or backslash of a value as an
    escape, so the raw value never appears in it: each line of a multi-line value
    (``ANTHROPIC_CUSTOM_HEADERS``) and each piece's JSON-escaped form go too.
    """
    values: set[str] = set()
    for secret in secrets:
        for piece in (secret, *secret.splitlines()):
            values.update((piece, json.dumps(piece)[1:-1],
                           json.dumps(piece, ensure_ascii=False)[1:-1]))
    for value in sorted((v for v in values if len(v) >= _MIN_SECRET_LEN), key=len, reverse=True):
        text = text.replace(value, _REDACTED)
    return text


def record_stream(stdout: str, output_dir: Path, extra_metrics: dict[str, Any],
                  treatment: Optional[Treatment], timed_out: Optional[float] = None,
                  secrets: Iterable[str] = (), edited: bool = False) -> None:
    """Write ``solver.jsonl``/``solver.json`` and fold the stream's facts into *extra_metrics*.

    *edited*: the work tree changed during the run (see :func:`solver_error`).
    """
    stdout = redact(stdout or "", secrets)
    (output_dir / "solver.jsonl").write_text(stdout, encoding="utf-8")
    result = result_event(stdout)
    (output_dir / "solver.json").write_text(json.dumps(result) if result else "",
                                            encoding="utf-8")
    stats = stream_stats(stdout, treatment)
    extra_metrics.update(stats)
    reason = terminal_reason(stdout)
    if reason:
        extra_metrics["terminal_reason"] = reason
    # A killed solve never writes a result event; that is the timeout, not an error.
    error = None if timed_out is not None else solver_error(stdout, edited=edited)
    if timed_out is not None:
        extra_metrics["solver_timeout"] = f"killed after {timed_out:g}s, no result event"
    if not error and treatment and treatment_loaded(stats, treatment) is False:
        # The plugin did not load, so the arm ran without its treatment.
        listed = stats.get("offered_plugins", stats["offered_skills"])
        error = f"treatment not offered: plugin {treatment.plugin} not in {listed}"
    if error:  # a tooling failure, told apart from a model miss like recompile_failed
        extra_metrics["solver_error"] = error


def record_solver(proc: process.Completed, task: str, model: Optional[str],
                  latency: float, output_dir: Path, operations: list[OperationRecord], log,
                  extra_metrics: dict[str, Any], treatment: Optional[Treatment] = None,
                  secrets: Iterable[str] = (), edited: bool = False) -> Optional[UsageRecord]:
    """Log one solver run, save its stream, and fold its telemetry into the run.

    The stream itself goes to ``solver.jsonl``, not ``run.log`` (see
    :func:`record_stream`, which also sets ``solver_error``).
    """
    secrets = list(secrets)
    log.write(redact(proc.stderr or "", secrets))
    record_stream(proc.stdout, output_dir, extra_metrics, treatment, secrets=secrets,
                  edited=edited)
    usage = _parse_claude_json(proc.stdout, task, model or "", latency)
    if usage is None:
        extra_metrics.setdefault("terminal_reason", "no_result")
    by_model = (result_event(proc.stdout) or {}).get("modelUsage")
    if isinstance(by_model, dict) and by_model:  # a helper model or subagent shows up here
        extra_metrics["models_used"] = sorted(str(k) for k in by_model)
    if usage is not None:
        # Kept if a later step fails (the copy back): the solve was paid for either way.
        extra_metrics.update(usage.as_metrics_dict())
        if usage.model:
            extra_metrics["model"] = usage.model
        operations.append(
            OperationRecord(type="loop", name="claude-turns", elapsed_sec=usage.latency_sec,
                            metadata={"n_calls": usage.n_calls})
        )
    return usage

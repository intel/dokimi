# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Parse the result object ``claude -p`` prints (``json``, or the last ``type: result`` ``stream-json`` event)."""

from __future__ import annotations

import json
import re
from typing import Optional

from dokimi.metrics.usage import UsageRecord


def _parse_claude_json(stdout: str, task: str, model: str, latency: float) -> Optional[UsageRecord]:
    """Parse the ``claude -p`` result object into a :class:`UsageRecord`.

    ``--output-format json`` prints the result object alone; ``stream-json``
    prints it as the final ``type: result`` event (see :func:`result_event`).
    It carries ``total_cost_usd``, a nested ``usage`` block (input/output/cache
    tokens), ``modelUsage`` (the same, per model), ``duration_ms``, ``ttft_ms``,
    and ``num_turns``. Tokens are summed over ``modelUsage`` when it is there:
    ``usage`` covers the parent session only, while ``total_cost_usd`` and
    ``modelUsage`` also cover helper models and subagents. Returns ``None``
    when there is no result object (the elapsed wall-clock ``latency`` is used
    as a fallback for ``latency_sec``).
    """
    obj = result_event(stdout)
    if obj is None:
        return None
    if not model:  # no --model: record what the CLI actually used, not ""
        model = _main_model(obj.get("modelUsage"))
    input_tokens, output_tokens, cache_read, cache_write = _tokens(obj)
    duration_ms = obj.get("duration_ms")
    ttft_ms = obj.get("ttft_ms")
    cost = obj.get("total_cost_usd")
    return UsageRecord(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=float(cost) if cost is not None else None,
        latency_sec=(duration_ms / 1000.0) if duration_ms is not None else latency,
        ttft_sec=(ttft_ms / 1000.0) if ttft_ms is not None else None,
        model=model,
        provider="claude-code",
        n_calls=int(obj.get("num_turns", 1) or 1),
        cost_known_calls=1 if cost is not None else 0,
    )


def _tokens(obj: dict) -> tuple[int, int, int, int]:
    """Input, output, cache-read and cache-write tokens of a result object."""
    counts = token_counts(obj)
    return counts[1] if counts is not None else (0, 0, 0, 0)


def token_counts(obj: dict) -> Optional[tuple[str, tuple[int, int, int, int]]]:
    """``(scope, (input, output, cache_read, cache_write))`` of a result object, ``None`` without usage.

    ``scope`` is ``"tree"`` when summed over ``modelUsage`` (the scope of
    ``total_cost_usd``), ``"main"`` when only the parent's ``usage`` is there.
    """

    def n(block: dict, key: str) -> int:
        try:
            return int(block.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    keys = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
    by_model = obj.get("modelUsage")
    per_model = [m for m in by_model.values() if isinstance(m, dict) and any(k in m for k in keys)] \
        if isinstance(by_model, dict) else []
    if per_model:
        i, o, r, w = (sum(n(m, k) for m in per_model) for k in keys)
        return "tree", (i, o, r, w)
    usage = obj.get("usage")
    if not isinstance(usage, dict) or not usage:
        return None
    return "main", (n(usage, "input_tokens"), n(usage, "output_tokens"), n(usage, "cache_read_input_tokens"),
                    n(usage, "cache_creation_input_tokens"))


def _main_model(model_usage: object) -> str:
    """The ``modelUsage`` entry that cost the most (the first key may be a helper model)."""
    if not isinstance(model_usage, dict) or not model_usage:
        return ""

    def cost(item: tuple) -> float:
        entry = item[1] if isinstance(item[1], dict) else {}
        try:
            return float(entry.get("costUSD") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return str(max(model_usage.items(), key=cost)[0])


def _last_json_object(text: str) -> Optional[dict]:
    """Return the last top-level JSON object printed on stdout, if any."""
    if not text:
        return None
    # The result object is normally the final non-empty line.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    # Fall back to the last {...} span anywhere in the output.
    matches = re.findall(r"\{.*\}", text, re.DOTALL)
    if matches:
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError:
            return None
    return None


def result_event(stdout: str) -> Optional[dict]:
    """The result object, from ``json`` output or a ``stream-json`` stream.

    In a stream the last line is not necessarily the result: a CLI that was
    killed or crashed ends on an ``assistant`` or ``system`` event, and taking
    the last object would read a partial run as a finished one. So the last
    ``type: result`` event wins, and a stream without one has no result. An
    object with no ``type`` at all is the older bare ``json`` shape.
    """
    typed = False
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result":
            return obj
        typed = typed or "type" in obj
    return None if typed else _last_json_object(stdout)


def solver_error(stdout: str, edited: bool = False) -> Optional[str]:
    """Why the CLI made no attempt at all, or ``None`` if it made one.

    No result object on stdout is a CLI that died; it counts as no attempt only
    while the work tree is untouched (*edited* false). After real edits (a node
    out-of-memory, the kernel OOM killer) the verifier decides, as for any error
    after an attempt.

    Missing or rejected credentials, a flagged first request or an unknown model
    id end a run with ``is_error`` before any output: a tooling failure, which
    the runner leaves out like a harness error. An error *after* an attempt (a
    529 at turn 40, ``prompt_too_long`` from a skill arm reading too much) is
    the arm's outcome and is scored; :func:`terminal_reason` records it.
    """
    obj = result_event(stdout)
    if obj is None:
        return None if edited else "no result object on stdout"
    if not (obj.get("is_error") or obj.get("terminal_reason") == "api_error"):
        return None
    usage = obj.get("usage")
    output_tokens = int(usage.get("output_tokens", 0) or 0) if isinstance(usage, dict) else 0
    if output_tokens > 0 and int(obj.get("num_turns", 0) or 0) > 1:
        return None  # it tried; the error ends an attempt, it does not replace one
    errors = obj.get("errors")
    errors = errors if isinstance(errors, list) else []
    message = str(obj.get("result") or "").strip() or "\n".join(str(e) for e in errors).strip()
    first = message.splitlines()[:1]
    return f"{obj.get('terminal_reason') or obj.get('subtype') or 'error'}: {first[0] if first else ''}"[:300]


def terminal_reason(stdout: str) -> Optional[str]:
    """How the CLI ended the run (``terminal_reason``, else ``subtype``), for every row."""
    obj = result_event(stdout)
    if obj is None:
        return None
    reason = obj.get("terminal_reason") or obj.get("subtype")
    return str(reason) if reason else None

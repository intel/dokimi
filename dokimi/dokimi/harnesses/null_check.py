# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The null check: a task's untouched start must fail its tests (intel/dokimi#808, G6).

A ``docker-null`` cell builds the task image, runs no solve and runs the verifier.
Its status says what the verifier found:

- ``null_fail``: a failed pytest test with no fixture errors, unless a final ``no tests ran``,
  skip-only or deselect-only summary follows it (``no_verdict``), or a verifier with no pytest summary explicitly graded
  the start below 1. The check passes.
- ``null_pass``: the verifier passed on the untouched start, so the tests do not check the task.
- ``fixture_error``: pytest reported setup or collection errors. A test can error in setup
  because the expected solution is missing.
- ``no_verdict``: no usable grading evidence, or a verifier that timed out.

A task passes when its ``docker-oracle`` cell passes and its ``docker-null`` cell is ``null_fail``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

ORACLE = "docker-oracle"
NULL = "docker-null"
NULL_FAIL = "null_fail"
NULL_PASS = "null_pass"
FIXTURE_ERROR = "fixture_error"
NO_VERDICT = "no_verdict"

# The summary line, plain (-q) or framed by "=" rules: "2 failed, 1 passed in 0.52s".
# Pytest may open it with any count ("2 skipped, 1 error"), so the first word is not fixed.
_SUMMARY = re.compile(
    r"^=* *(no tests ran|\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected)\b[^\n]*?) in \d+(?:\.\d+)? ?(?:s|seconds)\b",
    re.M,
)
_COLOR = re.compile(r"\x1b\[[0-9;:]*m")
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected)")
_OUTCOME = re.compile(r"\b(?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")


def pytest_counts(output: str) -> Optional[dict[str, int]]:
    """Counts by outcome (``passed``, ``failed``, ``error``, ...) of the last pytest summary, or ``None``.

    A line that is not a summary (a count line the verifier prints after pytest) must not replace it: only
    a line reporting an outcome or an empty test run is taken. Errors from prior summaries are kept.
    """
    summaries = _SUMMARY.findall(_COLOR.sub("", output))
    if not summaries:
        return None
    found = [line for line in summaries if line == "no tests ran" or _OUTCOME.search(line)]
    if not found:
        return {}  # Warnings alone establish a pytest run without a failing test.
    counts: dict[str, int] = {}
    for number, word in _COUNT.findall(found[-1]):
        key = "error" if word.startswith("error") else word
        counts[key] = counts.get(key, 0) + int(number)
    for earlier in found[:-1]:
        for number, word in _COUNT.findall(earlier):
            if word.startswith("error"):
                counts["error"] = counts.get("error", 0) + int(number)
    return counts


def null_status(reward: Optional[float], counts: Optional[Mapping[str, int]], *,
                explicit_reward: bool = False) -> str:
    """The status of one null cell, from the verifier's reward and its pytest counts."""
    if reward is None:
        return NO_VERDICT
    if reward >= 1.0:
        return NULL_PASS
    if counts is None:
        return NULL_FAIL if explicit_reward else NO_VERDICT
    if counts.get("error"):
        return FIXTURE_ERROR
    if counts.get("failed"):
        return NULL_FAIL
    return NO_VERDICT


def task_verdicts(results: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """Per task, ``(oracle, null)``: the oracle reads ``pass`` or ``fail``, the null its status.

    With repeats, the null is ``null_fail`` only when every repeat is; else the first repeat
    that is not. A cell that did not run reads ``missing``.
    """
    oracle: dict[str, list[bool]] = {}
    null: dict[str, list[str]] = {}
    for row in results:
        if row.get("harness") == ORACLE:
            oracle.setdefault(row["task"], []).append(bool(row.get("passed")))
        elif row.get("harness") == NULL:
            status = (row.get("metrics") or {}).get("null_check", NO_VERDICT)
            null.setdefault(row["task"], []).append(str(status))
    verdicts: dict[str, tuple[str, str]] = {}
    for task in sorted(set(oracle) | set(null)):
        passes = oracle.get(task, [])
        statuses = null.get(task, [])
        verdicts[task] = (
            "missing" if not passes else "pass" if all(passes) else "fail",
            "missing" if not statuses else next((s for s in statuses if s != NULL_FAIL), NULL_FAIL),
        )
    return verdicts


def all_pass(verdicts: Mapping[str, tuple[str, str]]) -> bool:
    """True when there is at least one task and every task passes the oracle and the null check."""
    return bool(verdicts) and all(o == "pass" and n == NULL_FAIL for o, n in verdicts.values())

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Wait for a thread, process or timer on the condition it reaches, not on a fixed sleep.

``scripts/check_test_timing.py`` fails a new ``time.sleep`` or ``threading.Timer`` call in ``tests/``;
this is the one file it does not look at. A failed wait calls ``pytest.fail`` with the condition named.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

import pytest

T = TypeVar("T")


def poll_until(predicate: Callable[[], object], timeout: float, interval: float = 0.02) -> bool:
    """Call ``predicate`` every ``interval`` seconds until it returns a truthy value (True), or until
    ``timeout`` seconds have passed, after one last call (False). It never raises for a timeout."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def wait_until(predicate: Callable[[], T], timeout: float, *, what: str, interval: float = 0.02) -> T:
    """The first truthy value ``predicate`` returns within ``timeout`` seconds. Otherwise the test fails
    (``pytest.fail``) with ``gave up after <timeout> s waiting for <what>``."""
    found: list[T] = []

    def check() -> bool:
        value = predicate()
        if value:
            found.append(value)
        return bool(value)

    if not poll_until(check, timeout, interval):
        pytest.fail(f"gave up after {timeout} s waiting for {what}")
    return found[-1]


def wait_for_event(event: threading.Event, timeout: float, *, what: str) -> None:
    """Return once ``event`` is set; fail the test as ``wait_until`` does when it is not within ``timeout``."""
    if not event.wait(timeout):
        pytest.fail(f"gave up after {timeout} s waiting for {what}")

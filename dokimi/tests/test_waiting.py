# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The condition waits tests use instead of a fixed sleep (``tests/waiting.py``)."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from tests import waiting
from tests.waiting import poll_until, wait_for_event, wait_until


def test_wait_until_returns_the_value_of_the_call_that_turned_truthy():
    calls = []

    def predicate():
        calls.append(1)
        return "ready" if len(calls) == 3 else None

    assert wait_until(predicate, 30, what="the third call", interval=0) == "ready"
    assert len(calls) == 3


def test_wait_until_fails_at_the_deadline_naming_what_it_waited_for_and_for_how_long():
    with pytest.raises(pytest.fail.Exception) as info:
        wait_until(lambda: False, 0.01, what="the child to print 'up'", interval=0.001)
    assert str(info.value) == "gave up after 0.01 s waiting for the child to print 'up'"


def test_poll_until_returns_false_at_the_deadline_and_true_once_the_predicate_holds():
    assert poll_until(lambda: 0, 0.01, interval=0.001) is False
    assert poll_until(lambda: [1], 30) is True


def test_poll_until_never_sleeps_past_the_deadline_so_a_late_condition_is_not_accepted(monkeypatch):
    clock = [0.0]
    naps: list[float] = []

    def nap(seconds):
        naps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(waiting, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=nap))
    assert poll_until(lambda: clock[0] >= 0.5, 0.01, interval=1) is False
    assert naps == [0.01]


def test_poll_until_does_not_accept_a_condition_reached_while_a_sleep_overshot_the_deadline(monkeypatch):
    clock = [0.0]

    def nap(seconds):
        clock[0] += 0.5

    monkeypatch.setattr(waiting, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=nap))
    assert poll_until(lambda: clock[0] >= 0.5, 0.01, interval=1) is False
    clock[0] = 0.0
    assert poll_until(lambda: clock[0] >= 0.5, 0.6, interval=1) is True
    with pytest.raises(pytest.fail.Exception, match="gave up after"):
        clock[0] = 0.0
        wait_until(lambda: clock[0] >= 0.5, 0.01, what="the clock", interval=1)


def test_poll_until_does_not_accept_a_value_a_slow_later_call_returns_after_the_deadline(monkeypatch):
    clock = [0.0]
    calls = [0]

    def predicate():
        calls[0] += 1
        if calls[0] == 2:
            clock[0] += 0.1
        return calls[0] >= 2

    def nap(seconds):
        clock[0] += seconds

    monkeypatch.setattr(waiting, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=nap))
    assert poll_until(predicate, 0.01, interval=0.005) is False
    calls[0], clock[0] = 0, 0.0
    with pytest.raises(pytest.fail.Exception, match="gave up after"):
        wait_until(predicate, 0.01, what="the slow value", interval=0.005)
    clock[0] = 0.0
    assert poll_until(lambda: clock.__setitem__(0, 5.0) or True, 0.01) is True


def test_wait_for_event_returns_at_once_for_a_set_event():
    event = threading.Event()
    event.set()
    assert wait_for_event(event, 30, what="the worker to start") is None


def test_wait_for_event_fails_naming_what_it_waited_for_when_the_event_is_never_set():
    with pytest.raises(pytest.fail.Exception, match=r"waiting for the worker to start"):
        wait_for_event(threading.Event(), 0.01, what="the worker to start")

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Ctrl-C during a threaded repo batch: the containers its cells started are killed, and the charge is kept."""

from __future__ import annotations

import json
import threading

import pytest

from dokimi import process
from dokimi.repos.container import BatchInterrupted, DockerRunner
from dokimi.repos.host import PROBE
from test_repo_host import _probe_out, _repo, _run


def test_a_ctrl_c_between_a_submit_and_its_bookkeeping_still_kills_that_worker(monkeypatch):
    # The worker starts its container before submit returns, so the interrupt lands before the cell has a future.
    from dokimi.repos import container as container_mod
    from dokimi.repos import runner as runner_mod

    started, killed = threading.Event(), threading.Event()

    class InterruptedSubmit(runner_mod.ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            super().submit(fn, *args, **kwargs)
            started.wait(5)
            raise KeyboardInterrupt

    def one(cell):
        container_mod._track("dokimi-x", killed.set)
        started.set()
        killed.wait(5)  # a container runs until it is killed
        container_mod._untrack("dokimi-x")

    monkeypatch.setattr(runner_mod, "ThreadPoolExecutor", InterruptedSubmit)
    try:
        with pytest.raises(KeyboardInterrupt):
            runner_mod._run_cells([1], one, 1)
        assert killed.is_set()
    finally:
        container_mod.clear_halt()


def test_a_second_ctrl_c_during_the_kills_keeps_the_halt_until_the_worker_returns(monkeypatch):
    from dokimi.repos import container as container_mod
    from dokimi.repos import runner as runner_mod

    started, killed = threading.Event(), threading.Event()
    kills, calls, halted_on_return = [], [], []
    real_halt = runner_mod.halt_and_kill_started

    class InterruptedSubmit(runner_mod.ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            super().submit(fn, *args, **kwargs)
            started.wait(5)  # the first Ctrl-C lands once the cell has started its container
            raise KeyboardInterrupt

    def halt_then_interrupted():
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt  # the second Ctrl-C, while the first kills are under way
        real_halt()

    def one(cell):
        container_mod._track("dokimi-x", lambda: (kills.append(1), killed.set()))
        started.set()
        killed.wait(5)
        container_mod._untrack("dokimi-x")
        halted_on_return.append(container_mod._halted)

    monkeypatch.setattr(runner_mod, "ThreadPoolExecutor", InterruptedSubmit)
    monkeypatch.setattr(runner_mod, "halt_and_kill_started", halt_then_interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            runner_mod._run_cells([1], one, 1)
        assert kills and halted_on_return == [True]
        assert container_mod._halted is False
    finally:
        container_mod.clear_halt()


def test_an_interrupt_after_an_agent_ran_still_charges_what_it_spent(tmp_path, monkeypatch):
    """The agent ran and was charged; the halt then refuses its grader. The charge is saved, the cell is not."""
    from dokimi.repos import runner as runner_mod

    def answer(call):
        if call.argv[1] == "run" and PROBE in call.argv:
            call.stdout_to.write_text(json.dumps(_probe_out()) + "\n", encoding="utf-8")
        return process.reply()

    ran = []

    def run_cell(ctx, task, arm, rep, batch_dir, opts, **kw):
        ran.append(1)
        exc = BatchInterrupted("the batch was interrupted while this container ran")
        exc.charged_usd = 0.5  # what run_cell sets once the agent has charged
        raise exc

    monkeypatch.setattr(runner_mod, "run_cell", run_cell)
    root = _repo(tmp_path)
    runs = tmp_path / "runs"
    with pytest.raises(BatchInterrupted):
        _run(root, runs, DockerRunner(runner=process.FakeRunner(answer)), monkeypatch)
    spend = json.loads(next(runs.rglob("spend.json")).read_text(encoding="utf-8"))
    assert ran and spend["spent_usd"] == pytest.approx(0.5 * len(ran))
    assert not list(runs.rglob("result.json"))

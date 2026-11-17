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


def test_a_ctrl_c_as_the_kills_start_still_kills_and_waits_for_the_workers(monkeypatch):
    from dokimi.repos import container as container_mod
    from dokimi.repos import runner as runner_mod

    started, killed = threading.Event(), threading.Event()
    kills, shutdowns = [], []

    class InterruptedShutdown(runner_mod.ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            super().submit(fn, *args, **kwargs)
            started.wait(5)  # the first Ctrl-C lands once the cell has started its container
            raise KeyboardInterrupt

        def shutdown(self, wait=True, *, cancel_futures=False):
            if not wait and not shutdowns:
                shutdowns.append(1)
                raise KeyboardInterrupt  # the second Ctrl-C, before the kills have started
            return super().shutdown(wait=wait, cancel_futures=cancel_futures)

    def one(cell):
        container_mod._track("dokimi-x", lambda: (kills.append(1), killed.set()))
        started.set()
        killed.wait(5)
        container_mod._untrack("dokimi-x")

    monkeypatch.setattr(runner_mod, "ThreadPoolExecutor", InterruptedShutdown)
    try:
        with pytest.raises(KeyboardInterrupt):
            runner_mod._run_cells([1], one, 1)
        assert kills, "the running container was not killed"
        assert container_mod._halted is False
    finally:
        container_mod.clear_halt()


def test_an_error_in_a_cell_cancels_the_cells_not_started(monkeypatch):
    from dokimi.repos import runner as runner_mod

    ran = []

    def one(cell):
        ran.append(cell)
        if cell == 0:
            raise ValueError("cell 0 failed")
        threading.Event().wait(0.05)

    with pytest.raises(ValueError):
        runner_mod._run_cells(list(range(10)), one, 1)
    assert len(ran) <= 3  # a cell that was queued behind the failure does not start (pool.map cancelled them)


def test_a_ctrl_c_during_one_kill_still_kills_the_other_containers():
    from dokimi.repos import container as container_mod

    kills = []

    def first():
        kills.append("a")
        raise KeyboardInterrupt  # a second Ctrl-C lands in this kill

    def second():
        kills.append("b")

    container_mod._track("dokimi-a", first)
    container_mod._track("dokimi-b", second)
    try:
        with pytest.raises(KeyboardInterrupt):
            container_mod.halt_and_kill_started()
        assert kills == ["a", "b"]
    finally:
        container_mod._untrack("dokimi-a")
        container_mod._untrack("dokimi-b")
        container_mod.clear_halt()


def test_a_kill_that_fails_unexpectedly_is_logged_with_its_traceback(tmp_path, monkeypatch, caplog):
    import logging

    from dokimi.repos import container as container_mod

    captured = {}

    def fake_tracked(name, stop, argv, **kw):
        captured["stop"] = stop
        raise container_mod.BatchInterrupted("stopped for the test")

    def broken_kill(argv, **kw):
        raise RuntimeError("kill broke")

    monkeypatch.setattr(container_mod, "_run_tracked", fake_tracked)
    spec = container_mod.ContainerSpec(image="img", argv=["true"], name="dokimi-k")
    runner = container_mod.DockerRunner(docker="docker", runner=broken_kill)
    with pytest.raises(container_mod.BatchInterrupted):
        runner.run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=1)
    with caplog.at_level(logging.ERROR):
        captured["stop"]()  # the kill is retried by the caller: it must not raise, and must not vanish
    assert "docker kill of dokimi-k failed" in caplog.text
    assert caplog.records[-1].exc_info is not None  # with its traceback

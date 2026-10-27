# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reader ownership includes closing handles before the process runner returns."""

import sys

from dokimi import process


def test_thread_readers_close_both_streams_before_caller_cleanup(monkeypatch):
    closed_before_cleanup = []
    original_cleanup, original_spawn = process._close_pipes, process._spawn
    streams = []

    def remember_streams(*args, **kwargs):
        proc, job = original_spawn(*args, **kwargs)
        streams.extend(pipe for pipe in (proc.stdout, proc.stderr) if pipe is not None)
        return proc, job

    def observe_cleanup(proc):
        closed_before_cleanup.extend(pipe.closed for pipe in streams if pipe is not None)
        # Clean up even on the unfixed implementation so the regression itself leaks nothing.
        for pipe in streams:
            if pipe is not None:
                pipe.close()
        original_cleanup(proc)

    monkeypatch.setattr(process, '_spawn', remember_streams)
    monkeypatch.setattr(process, '_select_pipes', process._thread_pipes)
    monkeypatch.setattr(process, '_close_pipes', observe_cleanup)
    done = process.run(
        [sys.executable, '-c', "import sys; print('out'); print('err', file=sys.stderr)"],
        timeout=10, env=process.Env.inherit(),
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, 'out\n', 'err\n')
    assert closed_before_cleanup == [True, True]


def test_interrupt_before_windows_readers_start_closes_unowned_streams(monkeypatch):
    import pytest

    streams = []
    original_spawn = process._spawn

    def remember_streams(*args, **kwargs):
        proc, job = original_spawn(*args, **kwargs)
        streams.extend(pipe for pipe in (proc.stdout, proc.stderr) if pipe is not None)
        return proc, job

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(process, '_POSIX', False)
    monkeypatch.setattr(process, '_spawn', remember_streams)
    monkeypatch.setattr(process, '_collect', interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            process.run([sys.executable, '-c', 'import time; time.sleep(60)'],
                        timeout=10, env=process.Env.inherit())
        assert len(streams) == 2 and all(pipe.closed for pipe in streams)
        assert process._live == {}
    finally:
        for pipe in streams:
            pipe.close()

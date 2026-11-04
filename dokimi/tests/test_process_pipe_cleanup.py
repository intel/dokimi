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


def test_interrupt_after_reader_start_never_blocks_caller_on_owned_pipe(monkeypatch):
    import io
    import os
    import threading
    import time
    from types import SimpleNamespace

    import pytest

    entered = threading.Event()
    started = []
    original_start = threading.Thread.start

    class BlockingReader(io.BufferedReader):
        def read1(self, size=-1):
            entered.set()
            return super().read1(size)

    read_fd, write_fd = os.pipe()
    pipe = BlockingReader(io.FileIO(read_fd, 'rb'))
    proc = SimpleNamespace(stdout=pipe, stderr=None, wait=lambda **_: 0)

    def start_then_interrupt(reader):
        original_start(reader)
        started.append(reader)
        assert entered.wait(5), 'reader never entered its blocking read'
        raise KeyboardInterrupt

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, 'start', start_then_interrupt)
            with pytest.raises(KeyboardInterrupt):
                process._thread_pipes(proc, [(pipe, process._Capture(16))],
                                      time.monotonic() + 10, None)
        cleanup = threading.Thread(target=process._close_pipes, args=(proc,))
        cleanup.start()
        cleanup.join(2)
        assert not cleanup.is_alive(), 'caller blocked closing a reader-owned stream'
    finally:
        os.close(write_fd)
        for reader in started:
            reader.join(5)
        if 'cleanup' in locals():
            cleanup.join(5)
        pipe.close()


def test_a_cancelled_reader_can_start_late_without_reopening_its_stream():
    import io
    import os
    import threading

    from dokimi.process_io import PipeReader

    read_fd, write_fd = os.pipe()
    pipe = io.BufferedReader(io.FileIO(read_fd, 'rb'))
    cap = process._Capture(16)
    owner = PipeReader(pipe, cap.add, 16)
    late_reader = threading.Thread(target=owner.pump, daemon=True)
    try:
        owner.close_unclaimed()
        late_reader.start()
        late_reader.join(2)
        assert pipe.closed and not late_reader.is_alive()
        assert cap.value() == (b'', False)
    finally:
        os.close(write_fd)
        late_reader.join(5)
        pipe.close()

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reader ownership includes closing handles before the process runner returns."""

import sys

from dokimi import process


def test_thread_readers_close_both_streams_before_caller_cleanup(monkeypatch):
    closed_before_cleanup = []
    original_cleanup = process._close_pipes

    def observe_cleanup(proc):
        streams = (proc.stdout, proc.stderr)
        closed_before_cleanup.extend(pipe.closed for pipe in streams if pipe is not None)
        # Clean up even on the unfixed implementation so the regression itself leaks nothing.
        for pipe in streams:
            if pipe is not None:
                pipe.close()
        original_cleanup(proc)

    monkeypatch.setattr(process, '_select_pipes', process._thread_pipes)
    monkeypatch.setattr(process, '_close_pipes', observe_cleanup)
    done = process.run(
        [sys.executable, '-c', "import sys; print('out'); print('err', file=sys.stderr)"],
        timeout=10, env=process.Env.inherit(),
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, 'out\n', 'err\n')
    assert closed_before_cleanup == [True, True]

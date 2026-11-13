# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit code 5 (infra_error) of issue #94 §2.6: an uncaught exception exits 5 and prints its traceback."""

from __future__ import annotations

import argparse

import pytest

from dokimi import cli


def test_an_uncaught_exception_exits_5_and_prints_its_traceback(monkeypatch, capsys):
    def crash(_args):
        raise RuntimeError("harness crashed")

    monkeypatch.setattr(cli, "parse_args", lambda: argparse.Namespace(cmd="run", func=crash))
    monkeypatch.setattr(cli.process, "install_signal_handlers", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 5  # not 1: a crash never reads as a failed verdict
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: harness crashed" in err

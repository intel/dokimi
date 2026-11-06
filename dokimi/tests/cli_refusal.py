# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run a command in-process and see what `cli.main` would print and exit with."""

from __future__ import annotations

import io
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Iterator

from dokimi import llm_cli
from dokimi.utils import InputError


class Outcome:
    """What the block printed and exited with: ``code``, ``out`` and ``err`` (set on exit)."""

    code: int = 0
    out: str = ""
    err: str = ""


@contextmanager
def refusal() -> Iterator[Outcome]:
    """Run the block as `cli.main` runs a command.

    `cli.main` prints an InputError (and a SamplingError) as ``Error: <message>`` and exits 2;
    any other SystemExit keeps its code, a string code printing it and exiting 1; a normal finish
    is 0. Only those exits are caught: any other exception propagates.
    """
    outcome = Outcome()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            yield outcome
        except (InputError, llm_cli.SamplingError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            outcome.code = 2
        except SystemExit as exc:
            if isinstance(exc.code, str):
                print(exc.code, file=sys.stderr)
                outcome.code = 1
            else:
                outcome.code = exc.code or 0
    outcome.out, outcome.err = out.getvalue(), err.getvalue()
    # Re-emit what the block printed, so a test's capsys reads still see it.
    sys.stdout.write(outcome.out)
    sys.stderr.write(outcome.err)

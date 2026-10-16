#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI for dokimi: run, compare, report."""

from __future__ import annotations

import argparse
import threading

from dokimi.commands import (
    answers,
    arms,
    baseline,
    benchmark,
    catalog,
    dashboard,
    dataset,
    evaluate,
    explore,
    ingest,
    library,
    novelty,
    orchestrate,
    personas,
    questions,
    repo,
    report,
    run,
    subjects,
    tasks,
    tools,
)
from dokimi import process
from dokimi.console import tolerate_console_encoding


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""

    def positive_int(value: str) -> int:
        """Argparse type that rejects non-positive integers."""
        try:
            ivalue = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got '{value}'") from None
        if ivalue < 1:
            raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
        return ivalue

    p = argparse.ArgumentParser(prog="dokimi")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Order preserved to keep --help output stable.
    run.register(sub, positive_int)             # run
    orchestrate.register(sub, positive_int)     # evaluate (one-command pipeline)
    run.register_compare(sub, positive_int)     # compare
    baseline.register(sub, positive_int)        # baseline
    report.register(sub, positive_int)          # report
    personas.register(sub, positive_int)        # personas
    questions.register(sub, positive_int)       # questions
    answers.register(sub, positive_int)         # answers
    evaluate.register(sub, positive_int)        # eval
    library.register(sub, positive_int)         # library
    benchmark.register(sub, positive_int)       # benchmark
    dashboard.register(sub, positive_int)       # dashboard
    arms.register(sub, positive_int)            # arms
    subjects.register(sub, positive_int)        # subjects (scorecards)
    tasks.register(sub, positive_int)           # tasks
    catalog.register(sub, positive_int)         # catalog (external skills/tasks)
    explore.register(sub, positive_int)         # explore (ExploreBench)
    dataset.register(sub, positive_int)         # dataset
    ingest.register(sub, positive_int)          # ingest
    novelty.register(sub, positive_int)         # novelty (content novelty)
    repo.register(sub, positive_int)            # repo (repositories as subjects)
    tools.register(sub, positive_int)           # tools (tool-choice probes)

    return p


def main() -> None:
    """Entry point."""
    if threading.current_thread() is threading.main_thread():  # signal.signal works only there
        process.install_signal_handlers()  # SIGTERM/SIGHUP: kill the process groups we started, then exit
    tolerate_console_encoding()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

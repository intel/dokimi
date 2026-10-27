#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI for dokimi: run, compare, report."""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Sequence

from dokimi.commands import (
    answers,
    arms,
    backends,
    baseline,
    benchmark,
    catalog,
    context,
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
from dokimi import __version__, process
from dokimi.console import tolerate_console_encoding
from dokimi.defaults import JUDGE_MODEL_FOR_PROVIDER, default_judge_model


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
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    context.register(sub, positive_int)         # context (context window of a run)
    backends.register(sub, positive_int)        # backends (CLI providers)

    return p


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``argv`` (default ``sys.argv[1:]``), then fill in defaults that hang on another flag.

    A left-out ``--judge-model`` becomes the default judge that runs on ``--judge-provider``,
    read once every flag is parsed, so the two flags can come in either order.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "judge_model", None) is JUDGE_MODEL_FOR_PROVIDER:
        model = default_judge_model(args.judge_provider)
        if model is None:
            parser.error(f"--judge-provider {args.judge_provider} has no default judge model; "
                         f"pass --judge-model")
        args.judge_model = model
    return args


def main() -> None:
    """Entry point."""
    if threading.current_thread() is threading.main_thread():  # signal.signal works only there
        process.install_signal_handlers()  # SIGTERM/SIGHUP: kill the process groups we started, then exit
    tolerate_console_encoding()
    args = parse_args()
    from dokimi.eval.question_text import EmptyQuestionError
    from dokimi.utils import InputError

    try:
        args.func(args)
    except (EmptyQuestionError, InputError) as exc:  # a bad input record, not a bug: no traceback
        print(f"Error: {exc}", file=sys.stderr)
        # 2, as the commands' other input errors: 1 is the verdict of `eval judge-validity`
        # and of the --strict gates, and a malformed file must not read as a failed gate.
        sys.exit(2)


if __name__ == "__main__":
    main()

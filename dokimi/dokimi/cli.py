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
    baseline,
    benchmark,
    catalog,
    context,
    dashboard,
    data,
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
    runs,
    subjects,
    tasks,
    tools,
)
from dokimi import __version__, cli_routes, process
from dokimi.console import tolerate_console_encoding
from dokimi.defaults import JUDGE_MODEL_FOR_PROVIDER, default_judge_model


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _route_help(text: str | None, path: tuple[str, ...]) -> str:
    """The one-line help of a route with its cost label: the parser's text, else the table's."""
    base = text or cli_routes.summary_of(path) or ""
    cost = cli_routes.cost_of(path)
    return " ".join(part for part in (base, f"[{cost}]" if cost else "") if part)


def _label_routes(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
    """Append the cost label to every subcommand's help line, at every depth."""
    sub = _subparsers(parser)
    if sub is None:
        return
    listed = {action.dest: action for action in sub._choices_actions}
    visited: set[int] = set()
    for name, child in sub.choices.items():
        if id(child) in visited:  # an alias: the same parser under a second name
            continue
        visited.add(id(child))
        path = (*prefix, name)
        if name in listed:
            listed[name].help = _route_help(listed[name].help, path)
        _label_routes(child, path)


class _RoutedParser(argparse.ArgumentParser):
    """The top-level parser. Its ``--help`` lists the roots by section (``dokimi.cli_routes``)."""

    def format_help(self) -> str:
        sub = _subparsers(self)
        listed = {action.dest: action for action in sub._choices_actions} if sub else {}
        fmt = self._get_formatter()
        fmt.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        # A parser root missing from cli_routes.ROOTS is left out of this list;
        # test_route_table_matches_the_parser_leaves_and_roots fails on it.
        for section in cli_routes.SECTIONS:
            roots = [root for root in cli_routes.ROOTS if root.section == section]
            if not roots:
                continue
            fmt.start_section(section)  # the formatter adds the colon
            for root in roots:
                action = listed.get(root.name)
                if action is None:  # a route with no help= in its parser: listed from the table
                    action = argparse.Action([], root.name, help=_route_help(None, (root.name,)))
                    if root.aliases:
                        action.metavar = f"{root.name} ({', '.join(root.aliases)})"
                fmt.add_argument(action)
            fmt.end_section()
        fmt.start_section("options")
        fmt.add_arguments([action for action in self._actions if action.option_strings])
        fmt.end_section()
        return fmt.format_help()


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

    p = _RoutedParser(prog="dokimi")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=argparse.ArgumentParser)

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
    runs.register(sub, positive_int)            # runs (the artifacts under the runs root)
    data.register(sub, positive_int)            # data (schema; M2 adds export, import and models)

    _label_routes(p)
    return p


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``argv`` (default ``sys.argv[1:]``), then fill in defaults that hang on another flag.

    A left-out ``--judge-model`` becomes the default judge that runs on ``--judge-provider``,
    read once every flag is parsed, so the two flags can come in either order.
    """
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    moved = cli_routes.moved_argv(raw)
    if moved is not None:  # a tombstone: say where the command went, do not run anything
        parser.exit(2, cli_routes.moved_text(moved) + "\n")
    args = parser.parse_args(raw)
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

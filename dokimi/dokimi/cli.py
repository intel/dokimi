#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI for dokimi: docs check, docs compare, report."""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from collections.abc import Sequence
from typing import NoReturn

from dokimi.commands import (
    answers,
    arms,
    backends,
    baseline,
    benchmark,
    catalog,
    context,
    dashboard,
    data,
    evaluate,
    explore,
    novelty,
    orchestrate,
    personas,
    questions,
    repo,
    report,
    run,
    runs,
    skill,
    subjects,
    tasks,
    tools,
)
from dokimi import __version__, cli_routes, llm_cli, process
from dokimi.console import tolerate_console_encoding
from dokimi.defaults import JUDGE_MODEL_FOR_PROVIDER
from dokimi.exit_codes import EXIT_INFRA
from dokimi.judge_rule import apply_default_judge, follow_answerer_cli, refuse_judge_model


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
    run.register(sub, positive_int)             # docs: check, compare (#94 M3)
    orchestrate.register(sub, positive_int)     # evaluate (one-command pipeline)
    baseline.register(sub, positive_int)        # baseline
    report.register(sub, positive_int)          # report
    answers.register(sub, positive_int)         # answers
    eval_sub = evaluate.register(sub, positive_int)  # eval: the groups below join it (#94 M1)
    personas.register(eval_sub, positive_int)   # eval personas
    questions.register(eval_sub, positive_int)  # eval questions, eval import
    benchmark.register(sub, positive_int)       # benchmark
    dashboard.register(sub, positive_int)       # dashboard
    arms.register(sub, positive_int)            # arms
    subjects.register(sub, positive_int)        # subjects (scorecards)
    tasks.register(sub, positive_int, eval_sub)  # tasks; eval tasks list, eval inspect
    catalog.register(sub, positive_int, eval_sub)  # catalog; eval triggers generate; catalog intents
    explore.register(sub, positive_int)         # explore (ExploreBench)
    skill.register(sub, positive_int)           # skill: novelty, shorten (#94 M2)
    novelty.register(sub, positive_int)         # novelty (routing, view: not yet moved)
    repo.register(sub, positive_int, eval_sub)  # repo; eval check
    tools.register(sub, positive_int)           # tools (tool-choice probes)
    context.register(sub, positive_int)         # trace: inspect, consistency, prefilter (#94 M2)
    runs.register(sub, positive_int)            # runs (the artifacts under the runs root)
    backends.register(sub, positive_int)        # backends (CLI providers)
    data.register(sub, positive_int)            # data: schema, export, import docling (#94 M2)

    _label_routes(p)
    return p


def _names_judge_provider(token: str) -> bool:
    """True for ``--judge-provider`` (``=value`` too) or an argparse abbreviation of it (``--judge-p``)."""
    flag = token.split("=", 1)[0]
    return flag.startswith("--judge-p") and "--judge-provider".startswith(flag)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``argv`` (default ``sys.argv[1:]``), then fill in defaults that hang on another flag.

    A left-out ``--judge-model`` becomes the default judge that runs on ``--judge-provider``,
    read once every flag is parsed, so the two flags can come in either order.
    """
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    moved = cli_routes.moved_argv(raw)
    if moved is not None:  # a tombstone: say where the command went, do not run anything
        moved = cli_routes.moved_flags_argv(moved) or moved  # the moved flag too, in one step
        parser.exit(2, cli_routes.moved_text(moved) + "\n")
    moved_flags = cli_routes.moved_flags_argv(raw)
    if moved_flags is not None:  # a moved flag spelling: print the argv with the new one
        parser.exit(2, cli_routes.moved_text(moved_flags) + "\n")
    args = parser.parse_args(raw)
    # argparse fills --judge-provider with its default (anthropic), so the rule needs to know whether one was named.
    args.judge_provider_named = any(_names_judge_provider(t) for t in raw)
    # A command that reads its answerer from a descriptor (subjects run) resolves the judge itself, once it is loaded.
    if getattr(args, "judge_model", None) is JUDGE_MODEL_FOR_PROVIDER and not getattr(args, "defer_judge_default", False):
        # With --matrix-cells / --matrix-cell every cell sets its own model, so the top-level one is no answerer.
        matrix = getattr(args, "matrix_cells", None) or getattr(args, "matrix_cell", None)
        try:
            apply_default_judge(args, matrix=bool(matrix), named=args.judge_provider_named)
        except ValueError as e:
            parser.error(str(e))
    # An explicit --judge-model on a CLI answerer judges on its CLI too, unless --judge-provider names one.
    # A command that defers its judge (subjects run) follows its answerer once the descriptor is loaded.
    if not getattr(args, "defer_judge_default", False):
        follow_answerer_cli(args, matrix=bool(getattr(args, "matrix_cells", None) or getattr(args, "matrix_cell", None)),
                            named=args.judge_provider_named)
    cur_judge_provider, cur_judge_model = getattr(args, "judge_provider", None), getattr(args, "judge_model", None)
    if cur_judge_provider in llm_cli.BACKENDS and cur_judge_model is not None \
            and not llm_cli.accepts_model(cur_judge_provider, cur_judge_model):
        # Checked here, before any persona or answer is generated for the run.
        parser.error(refuse_judge_model(cur_judge_provider, cur_judge_model, named=args.judge_provider_named,
                                        answerer_provider=getattr(args, "provider", None)))
    provider, model = getattr(args, "provider", None), getattr(args, "model", None)
    # With --matrix-cells every cell sets its own provider and model (checked when applied), so the
    # top-level model is not the one that runs.
    if provider in llm_cli.BACKENDS and model is not None and not llm_cli.accepts_model(provider, model) \
            and not getattr(args, "matrix_cells", None):
        # The default answer model is not a Claude model: left out, every call would fail.
        parser.error(f"--provider {provider} takes a Claude model (sonnet, haiku, opus or a claude-* id), "
                     f"not {model!r}: pass --model")
    return args


def _emits_cli_output(args: argparse.Namespace) -> bool:
    """True for a leaf that opted in (``emits_cli_output``) and was run with ``--json``."""
    return bool(getattr(args, "emits_cli_output", False) and getattr(args, "json", False))


def _exit_with_document(args: argparse.Namespace, code: str, message: str) -> NoReturn:
    """Print the cli_output.v1 error document on stdout and exit with the code §2.6 gives it."""
    from dokimi import cli_output

    kind = getattr(args, "cli_kind", args.cmd)  # a group leaf names itself, e.g. "runs list"
    cli_output.emit(cli_output.error_document(kind, code, message, run_id=cli_output.current_run_id()))
    sys.exit(cli_output.EXIT_OF_ERROR[code])


def main() -> None:
    """Entry point."""
    if threading.current_thread() is threading.main_thread():  # signal.signal works only there
        process.install_signal_handlers()  # SIGTERM/SIGHUP: kill the process groups we started, then exit
    tolerate_console_encoding()
    args = parse_args()
    from dokimi.artifacts import run_id
    from dokimi.utils import InputError

    try:
        run_id()  # a malformed DOKIMI_RUN_ID stops here, not at the first save after paid calls
        args.func(args)
    except (InputError, llm_cli.SamplingError) as exc:  # bad input, not a bug: no traceback
        if _emits_cli_output(args):
            _exit_with_document(args, "input", str(exc) or type(exc).__name__)
        print(f"Error: {exc}", file=sys.stderr)
        # 2 is the one code for bad input: 1 is the verdict of `eval judge validate` and of the
        # --strict gates, and a malformed file must not read as a failed gate.
        sys.exit(2)
    except Exception as exc:
        # A crash is an infra failure, exit 5 for every command (§2.6): it never reads as a failed verdict.
        traceback.print_exc()
        if _emits_cli_output(args):  # a --json leaf still prints its one document on a crash
            _exit_with_document(args, "infra", f"{type(exc).__name__}: {exc}")
        sys.exit(EXIT_INFRA)


if __name__ == "__main__":
    main()

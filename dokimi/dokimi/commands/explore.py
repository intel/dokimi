# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""explore subcommand group: run the ExploreBench localization track."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from dokimi.exit_codes import EXIT_USAGE, usage_exit

_TASKS_ROOT_HELP = (
    "Directory of exploration task YAML files (default: data/explore_tasks of a "
    "source checkout; an installed package has none and needs this flag)"
)
_REPO_ROOT_HELP = (
    "Tree a task is scored against when its YAML omits repo_root: "
    "(default: the dokimi source checkout, which is only right for the "
    "self-dogfooding tasks bundled there; an installed package needs this flag)"
)


def _no_tasks(root: Path | None) -> NoReturn:
    if root is not None and not root.is_dir():
        usage_exit(f"--tasks-root is not a directory: {root}")
    where = f"under {root}" if root is not None else "in the bundled data/explore_tasks"
    usage_exit(f"No explore tasks {where}. Pass --tasks-root <dir> of task YAML files.")


def cmd_explore_list(args: argparse.Namespace) -> None:
    from dokimi.explore import discover_explore_tasks

    root = Path(args.tasks_root) if args.tasks_root else None
    default_repo_root = Path(args.repo_root) if getattr(args, "repo_root", None) else None
    try:
        tasks = discover_explore_tasks(root, default_repo_root=default_repo_root)
    except (FileNotFoundError, ValueError) as exc:  # a task file that cannot be read or parsed
        usage_exit(str(exc))
    if not tasks:
        _no_tasks(root)
    for task in tasks:
        n_refs = len(task.references)
        print(f"{task.id}\t{task.product}\t{n_refs} ref(s)\t{task.query[:60]}")


def _build_arms(specs: list[str], output_root: Path):
    """Map arm specs to per-task explorer factories.

    Supported specs:
      ``oracle``            — cite exactly the task's references (a ceiling).
      ``empty``             — return an empty answer (a floor).
      ``command:<template>``— run a CLI explorer ({query}/{query_quoted}/
                              {repo_root}/{output_dir} placeholders).
    """
    from dokimi.explore import CommandExplorer, StaticExplorer

    arms: dict = {}
    for spec in specs:
        if spec == "oracle":
            arms[spec] = lambda task: StaticExplorer.from_references(task.references)
        elif spec == "empty":
            arms[spec] = lambda task: StaticExplorer(
                "<final_answer>\n</final_answer>", name="empty"
            )
        elif spec.startswith("command:"):
            template = spec[len("command:"):]
            if not template:
                usage_exit("'command:' arm requires a template")

            def _factory(task, _template=template, _spec=spec):
                return CommandExplorer(
                    _template,
                    name=_spec,
                    output_root=output_root / task.id / _safe(_spec),
                )

            arms[spec] = _factory
        else:
            usage_exit(f"Unknown explore arm: '{spec}'. Valid: oracle, empty, command:<template>")
    return arms


def cmd_explore_run(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.explore import (
        ExploreRunner,
        discover_explore_tasks,
        load_explore_task,
    )

    root = Path(args.tasks_root) if args.tasks_root else None
    default_repo_root = Path(args.repo_root) if getattr(args, "repo_root", None) else None
    try:
        if args.all:
            tasks = discover_explore_tasks(root, default_repo_root=default_repo_root)
        else:
            ids = [t.strip() for t in (args.tasks or "").split(",") if t.strip()]
            tasks = [
                load_explore_task(t, root=root, default_repo_root=default_repo_root) for t in ids
            ]
    except (FileNotFoundError, ValueError) as exc:  # a task file that cannot be read or parsed
        usage_exit(str(exc))
    if not tasks:
        if args.all:
            _no_tasks(root)
        usage_exit("No tasks selected. Pass --tasks <id[,id]> or --all.")

    specs = [s.strip() for s in args.arms.split(",") if s.strip()]
    if not specs:
        usage_exit("No arms selected. Pass --arms oracle,empty[,command:<tmpl>].")
    output_root = Path(args.output_dir)
    arms = _build_arms(specs, output_root)

    runner = ExploreRunner(
        tasks=tasks,
        arms=arms,
        model=args.model,
        baseline_arm=args.baseline_arm,
        output_root=output_root,
    )
    output = runner.run()

    out_json = Path(args.out_json) if args.out_json else output_root / "explore_runs.json"
    try:
        save_artifact("explore_runs", output, out_json)
    except OSError as exc:
        print(f"Error: cannot write {out_json}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from exc
    print(f"Saved explore run artifact: {out_json}")

    if args.out_md:
        from dokimi.report.exploration_report import render_explore_report
        from dokimi.utils import write_text_or_exit

        write_text_or_exit(Path(args.out_md), render_explore_report(output))
        print(f"Saved exploration report: {args.out_md}")

    for arm, stats in output["summary"]["per_arm"].items():
        print(
            f"{arm}: n={stats['n']} "
            f"file_f1={_fmt(stats['avg_file_f1'])} line_f1={_fmt(stats['avg_line_f1'])} "
            f"validity={_fmt(stats['avg_citation_validity_rate'])}"
        )
    for arm, cmp_row in output["summary"]["comparisons"].items():
        print(
            f"{arm} vs {cmp_row['baseline_arm']}: "
            f"file_f1 Δ {_fmt_delta(cmp_row['file_f1_delta'])}"
        )


def _fmt(value) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "n/a"


def _fmt_delta(value) -> str:
    return f"{value:+.3f}" if isinstance(value, (int, float)) else "n/a"


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text)


def register(sub, positive_int) -> None:
    """Add the `explore` subcommand group."""
    explore_p = sub.add_parser(
        "explore",
        help="Run exploration-only localization tasks (ExploreBench)",
    )
    explore_sub = explore_p.add_subparsers(dest="explore_cmd", required=True)

    list_p = explore_sub.add_parser("list", help="List exploration tasks")
    list_p.add_argument("--tasks-root", default=None, help=_TASKS_ROOT_HELP)
    list_p.add_argument("--repo-root", default=None, dest="repo_root", help=_REPO_ROOT_HELP)
    list_p.set_defaults(func=cmd_explore_list)

    run_p = explore_sub.add_parser("run", help="Run exploration tasks through arms")
    run_p.add_argument("--tasks", default=None, help="Comma-separated task ids")
    run_p.add_argument("--all", action="store_true", help="Run every discovered task")
    run_p.add_argument("--tasks-root", default=None, help=_TASKS_ROOT_HELP)
    run_p.add_argument("--repo-root", default=None, dest="repo_root", help=_REPO_ROOT_HELP)
    run_p.add_argument(
        "--arms",
        default="oracle,empty",
        help="Comma-separated arms: oracle, empty, command:<template>",
    )
    run_p.add_argument("--baseline-arm", default=None)
    run_p.add_argument("--model", default=None)
    run_p.add_argument("--output-dir", default="results/explore-runs")
    run_p.add_argument("--out-json", default=None)
    run_p.add_argument("--out-md", default=None, help="Also render a Markdown report")
    run_p.set_defaults(func=cmd_explore_run)

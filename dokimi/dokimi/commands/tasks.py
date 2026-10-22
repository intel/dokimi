# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""tasks subcommand group: run executable coding tasks through harnesses."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TASKS_ROOT_HELP = (
    "Directory holding one sub-directory per task (each with task.toml or task.md). "
    "Required for bare task names and for --all; the tasks live in a separate "
    "evaluation-content checkout, e.g. "
    "--tasks-root /path/to/content-repo/evaluation/harbor/tasks"
)


def _tasks_root(args: argparse.Namespace) -> Path | None:
    """Resolve --tasks-root, refusing a path that is not a directory.

    Task discovery itself tolerates an absent directory, because a content
    checkout may legitimately ship no executable tasks. A root the user named
    explicitly is different: a typo there would otherwise look like a suite
    with zero tasks.
    """

    if not args.tasks_root:
        return None
    root = Path(args.tasks_root)
    if not root.is_dir():
        raise SystemExit(f"--tasks-root is not a directory: {root}")
    return root


def cmd_tasks_list(args: argparse.Namespace) -> None:
    from dokimi.harnesses import discover_tasks

    try:
        tasks = discover_tasks(_tasks_root(args))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    for task in tasks:
        tags = task.metadata.get("tags") or []
        difficulty = task.metadata.get("difficulty", "?")
        print(f"{task.name}\t{difficulty}\t{','.join(tags)}")


def cmd_tasks_harnesses(args: argparse.Namespace) -> None:
    from dokimi.harnesses import known_harnesses

    for name, description in known_harnesses().items():
        print(f"{name}\t{description}")


def _launch_mismatches(harnesses: list[str], baseline: str | None) -> list[str]:
    """Claude arms whose agent runs somewhere else than the baseline's (host vs container).

    Their pass rates come from different sandboxes, so a delta between them is
    not the treatment's alone.
    """
    from dokimi.harnesses import create_harness
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    def location(name: str) -> str | None:
        try:
            h = create_harness(name)
        except ValueError:
            return None
        return h.agent_location if isinstance(h, DockerSolverHarness) and h.mode == "claude" else None

    base = location(baseline) if baseline else None
    if base is None:
        return []
    where = {"host": "on the host", "container": "in the task container"}
    return [f"{h} runs its agent {where[loc]}, the baseline {baseline} {where[base]}"
            for h in harnesses if h != baseline and (loc := location(h)) not in (None, base)]


def cmd_tasks_run(args: argparse.Namespace) -> None:
    from dokimi.harnesses import PreflightError, TaskSuiteRunner, discover_tasks, load_task

    root = _tasks_root(args)
    try:
        if args.all:
            tasks = discover_tasks(root)
        else:
            task_names = [t.strip() for t in args.tasks.split(",") if t.strip()]
            tasks = [load_task(t, root=root) for t in task_names]
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if not tasks:
        raise SystemExit("No tasks selected. Pass --tasks <name[,name]> or --all.")

    harnesses = [h.strip() for h in args.harnesses.split(",") if h.strip()]
    if not harnesses:
        raise SystemExit("No harnesses selected. Pass --harnesses codex,claude-code.")
    if args.baseline_harness and args.baseline_harness not in harnesses:
        print(f"Also running baseline harness: {args.baseline_harness}")
        harnesses.insert(0, args.baseline_harness)
    for mismatch in _launch_mismatches(harnesses, args.baseline_harness):
        print(f"WARNING: {mismatch}; their pass rates come from different sandboxes and "
              "should not be compared", file=sys.stderr)

    runner = TaskSuiteRunner(
        tasks=tasks,
        harnesses=harnesses,
        model=args.model,
        baseline_harness=args.baseline_harness,
        output_root=Path(args.output_dir),
        command_template=args.command_template,
        dry_run=args.dry_run,
        repeats=args.repeats,
    )
    try:
        runner.validate_harnesses()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    try:
        output = runner.run()
    except PreflightError as exc:
        # A missing tool (bench, Compose v2): one line, not a traceback.
        raise SystemExit(f"error: {exc}") from None

    out_json = Path(args.out_json) if args.out_json else Path(args.output_dir) / "task_runs.json"
    from dokimi.artifacts import save_artifact

    save_artifact("task_runs", output, out_json)
    print(f"Saved task run artifact: {out_json}")

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    from dokimi.report.task_runs_report import render_task_runs_report

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_task_runs_report(output), encoding="utf-8")
    print(f"Saved task run report:   {out_md}")

    for harness, stats in output["summary"]["per_harness"].items():
        rate = stats["pass_rate"]
        rate_s = "n/a" if rate is None else f"{rate:.2%}"
        cost = stats.get("cost", {}).get("total_cost_usd")
        cost_s = "" if cost is None else f", cost=${cost:.4f}"
        scored = stats.get("scored", stats["n"])
        errored_s = f", {stats['errored']} errored" if stats.get("errored") else ""
        print(
            f"{harness}: {stats['passed']}/{scored} pass ({rate_s}){errored_s}, "
            f"ops={stats['operation_count']}, elapsed={stats['elapsed_sec']:.2f}s{cost_s}"
        )
    for harness, cmp_row in output["summary"]["comparisons"].items():
        delta = cmp_row["pass_rate_delta"]
        delta_s = "n/a" if delta is None else f"{delta:+.2%}"
        cost_delta = cmp_row.get("cost_delta_usd")
        cost_s = "" if cost_delta is None else f", cost delta ${cost_delta:+.4f}"
        print(f"{harness} vs {cmp_row['baseline_harness']}: pass-rate delta {delta_s}{cost_s}")
    per_harness = output["summary"]["per_harness"].values()
    cells = sum(s["n"] for s in per_harness)
    errored = sum(s.get("errored", 0) for s in per_harness)
    if cells and errored == cells:  # no cell has a verdict: "0/0 pass (n/a)" is not a result
        print(f"✗ incomplete: every cell errored (0 scored, {errored} errored of {cells} cells)", file=sys.stderr)
        sys.exit(1)


def register(sub, positive_int) -> None:
    """Add the `tasks` subcommand group."""

    tasks_p = sub.add_parser(
        "tasks",
        help="Run executable coding tasks through Codex, Claude Code, or terminal-bench harnesses",
    )
    tasks_sub = tasks_p.add_subparsers(dest="tasks_cmd", required=True)

    list_p = tasks_sub.add_parser("list", help="List terminal-bench task metadata")
    list_p.add_argument("--tasks-root", default=None, help=_TASKS_ROOT_HELP)
    list_p.set_defaults(func=cmd_tasks_list)

    harnesses_p = tasks_sub.add_parser("harnesses", help="List known coding harness aliases")
    harnesses_p.set_defaults(func=cmd_tasks_harnesses)

    run_p = tasks_sub.add_parser("run", help="Run selected tasks through one or more harnesses")
    group = run_p.add_mutually_exclusive_group(required=True)
    group.add_argument("--tasks", default="", help="Comma-separated task names or paths")
    group.add_argument("--all", action="store_true", help="Run all discovered tasks")
    run_p.add_argument(
        "--harnesses",
        required=True,
        help="Comma-separated harness ids: codex, claude-code, terminal-bench:<agent>",
    )
    run_p.add_argument("--baseline-harness", default=None, dest="baseline_harness")
    run_p.add_argument("--model", default=None, help="Model ref passed to the harness")
    run_p.add_argument("--tasks-root", default=None, help=_TASKS_ROOT_HELP)
    run_p.add_argument("--output-dir", default="results/task-runs", dest="output_dir")
    run_p.add_argument("--out-json", default=None, dest="out_json")
    run_p.add_argument("--out-md", default=None, dest="out_md",
                       help="Markdown report path (default: alongside --out-json)")
    run_p.add_argument(
        "--repeats", type=positive_int, default=1,
        help="Run each (task, harness) cell N times for pass-rate/cost variance (default: 1)",
    )
    run_p.add_argument(
        "--command-template",
        default=None,
        help=(
            "Override command for every harness. Placeholders: {task_path}, {task}, "
            "{model}, {output_dir}, {prompt}, {prompt_quoted}."
        ),
    )
    run_p.add_argument(
        "--dry-run", action="store_true", help="Build artifact without invoking agents"
    )
    run_p.set_defaults(func=cmd_tasks_run)

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


def _inspect_line(report: dict) -> str:
    n = report["items"]
    items = "" if n is None else f"\t{n} item{'' if n == 1 else 's'}"
    return f"{report['path']}\t{report['format']}\t{report['kind']}{items}\t-> {', '.join(report['consumers'])}"


def _print_inspect(reports: list[dict]) -> None:
    for r in reports:
        print(_inspect_line(r))
        if r["present"] or r["missing"]:
            print(f"  parts: {', '.join(r['present']) or '-'}; missing: {', '.join(r['missing']) or '-'}")
        for alt in r["alternatives"]:
            print(f"  ambiguous: also {alt['format']} ({alt['confidence']:.2f}: {', '.join(alt['evidence'])})")
        for problem in r["problems"]:
            print(f"  problem: {problem}")
    by_format: dict[str, int] = {}
    for r in reports:
        by_format[r["format"]] = by_format.get(r["format"], 0) + 1
    print(f"{len(reports)} item{'' if len(reports) == 1 else 's'}: " + ", ".join(f"{f} {n}" for f, n in sorted(by_format.items())))


def cmd_tasks_inspect(args: argparse.Namespace) -> None:
    """Detect the eval-task format of a path (or of everything under it) and report its structure."""
    import json

    from dokimi.artifacts import save_artifact, stamp
    from dokimi.task_formats import inspect_path

    try:
        reports = [r.as_dict() for r in inspect_path(Path(args.path))]
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}") from None
    payload = {"root": str(args.path), "items": reports}
    if args.out:
        try:
            save_artifact("task_inspect", payload, Path(args.out))
        except OSError as exc:
            raise SystemExit(f"error: cannot write {args.out}: {exc}") from None
    if args.json:
        print(json.dumps(stamp("task_inspect", dict(payload)), indent=2, default=str))
    else:
        _print_inspect(reports)


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


def _select_swebench(args: argparse.Namespace) -> list:
    """Stage the selected rows of a SWE-bench dataset file under the output dir."""
    from dokimi.harnesses.swebench import KNOWN_BAD, load_swebench

    ids = None if args.all else [t.strip() for t in (args.tasks or "").split(",") if t.strip()]
    if ids == []:
        raise SystemExit("No tasks selected. Pass --tasks <instance_id[,...]> or --all.")
    if ids is None:
        print(f"--swe-bench --all skips {len(KNOWN_BAD)} known-bad rows (name them in --tasks to run them): "
              + ", ".join(sorted(KNOWN_BAD)), file=sys.stderr)
    try:
        return load_swebench(Path(args.swe_bench), Path(args.output_dir) / "swe-bench-tasks", ids)
    except (OSError, ValueError) as exc:  # SweBenchError and bad JSON are ValueErrors
        raise SystemExit(f"--swe-bench: {exc}") from None


def _select_tasks(args: argparse.Namespace) -> list:
    from dokimi.harnesses import discover_tasks, load_task

    if getattr(args, "swe_bench", None):
        return _select_swebench(args)
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
    return tasks


def _print_summary(output: dict) -> None:
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


def cmd_tasks_run(args: argparse.Namespace) -> None:
    from dokimi.harnesses import PreflightError, TaskSuiteRunner

    tasks = _select_tasks(args)

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
        network=tuple(args.network),
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

    _print_summary(output)


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

    inspect_p = tasks_sub.add_parser(
        "inspect",
        help="Detect the eval-task format of a file or directory tree and report its structure (no run)",
    )
    inspect_p.add_argument("path", help="A task directory, a dataset file, or a tree holding either")
    inspect_p.add_argument("--json", action="store_true", help="Print the task_inspect.v1 report as JSON")
    inspect_p.add_argument("--out", default=None, help="Write the task_inspect.v1 report to this file")
    inspect_p.set_defaults(func=cmd_tasks_inspect)

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
    run_p.add_argument(
        "--swe-bench", default=None, dest="swe_bench", metavar="FILE",
        help=("A SWE-bench dataset (.jsonl or .json rows); --tasks names instance ids. "
              "Each row runs in its prebuilt swebench/sweb.eval.* image (docker-* harnesses)."),
    )
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
        "--network", action="append", default=[], metavar="PHASE=MODE",
        help=(
            "Network for a docker-* phase (build, solve, verify, or all): none, public, or "
            "allowlist:host[:port],... Repeatable; overrides the task's own network_mode. "
            "Default build=public solve=none verify=none"
        ),
    )
    run_p.add_argument(
        "--dry-run", action="store_true", help="Build artifact without invoking agents"
    )
    run_p.set_defaults(func=cmd_tasks_run)

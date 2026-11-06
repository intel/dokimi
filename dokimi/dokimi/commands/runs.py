# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""runs subcommand group: the artifacts under the runs root (#278). Reads only; never spends."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone

from dokimi.artifacts import INPUT_KINDS, SCHEMA_VERSIONS
from dokimi.runs.scan import (
    SKIP_REASONS,
    Artifact,
    ScanResult,
    by_run,
    plain,
    runs_root,
    scan,
    select,
)
from dokimi.runs.show import Shown, ShownRun, show_target
from dokimi.utils import InputError


def _untimed(artifact: Artifact) -> bool:
    return artifact.recorded_at is None and not artifact.generated_at


def _when(artifact: Artifact) -> str:
    """The recorded time in UTC to the minute; the file's mtime, marked ``*``, when it has none."""
    stamp = artifact.recorded_at
    if stamp:
        return stamp.strftime("%Y-%m-%d %H:%M")
    if artifact.generated_at:  # not ISO 8601, or out of range: shown as written
        return artifact.generated_at
    return _mtime(artifact) + "*"


def _mtime(artifact: Artifact) -> str:
    """The file's mtime in UTC to the minute; ``@<seconds>`` when it is past what a date holds."""
    try:
        return datetime.fromtimestamp(artifact.mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):  # tmpfs keeps mtimes past the year 9999
        return f"@{artifact.mtime:.0f}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _table(artifacts: list[Artifact], with_run: bool = True) -> list[str]:
    """One row per artifact; the path is last, so a long one does not widen the others."""
    head = ("SCHEMA", "SUBJECT", "MODEL", "TIME (UTC)", "PATH")
    rows: list[tuple[str, ...]] = [("RUN", *head) if with_run else head]
    for a in artifacts:
        cells = (a.schema_version, a.subject or "-", a.model or "-", _when(a), a.path.as_posix())
        rows.append(tuple(plain(c) for c in ((a.run_id or "-", *cells) if with_run else cells)))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return ["  ".join(row[i].ljust(w) for i, w in enumerate(widths)) + "  " + row[-1]
            for row in rows]


def _runs_line(groups: list[list[Artifact]]) -> str:
    runs = [g for g in groups if g[0].run_id is not None]
    line = f"runs: {len(runs)}"
    if runs:
        line += f" ({_count(sum(len(g) for g in runs), 'artifact')})"
    loose = len(groups) - len(runs)
    if loose:
        line += f"; no run_id: {loose} (runs show PATH says why)"
    return line


def _summary(result: ScanResult, groups: list[list[Artifact]], root: str) -> list[str]:
    shown = [a for g in groups for a in g]
    total = _count(len(result.artifacts), "artifact")
    lines = [f"{len(shown)} of {total} under {root} match" if len(shown) != len(result.artifacts)
             else f"{total} under {root}"]
    if any(_untimed(a) for a in shown):
        lines[0] += " (* the file's mtime: the artifact records no time)"
    if shown:
        lines.append(_runs_line(groups))
    labels = {**SKIP_REASONS, "unknown_schema_version": SKIP_REASONS["unknown_schema_version"]
              + " (" + ", ".join(f"{plain(v)}: {n}" for v, n in sorted(result.unknown_versions.items()))
              + ")"}
    skipped = [f"{result.skipped[reason]} {label}" for reason, label in labels.items()
               if result.skipped[reason]]
    if skipped:
        lines.append("skipped: " + ", ".join(skipped))
    if result.unreadable:
        first = result.unreadable[0]
        lines.append(f"unreadable: {len(result.unreadable)} "
                     f"(first: {plain(first.path.as_posix())}: {plain(first.reason)})")
    return lines


def _runs_json(groups: list[list[Artifact]]) -> list[dict]:
    return [{"run_id": g[0].run_id, "kinds": sorted({a.kind for a in g}),
             "paths": [a.path.as_posix() for a in g]}
            for g in groups if g[0].run_id is not None]


def cmd_runs_list(args: argparse.Namespace) -> None:
    """One row per artifact under the runs root, the artifacts of one run together and the runs
    newest first, and the count of what was skipped."""
    root = runs_root()
    exists = root.exists()
    result = scan(root.path) if exists else ScanResult(root=root.path)
    groups = by_run(select(result.artifacts, args.kind, args.subject))
    shown = [a for g in groups for a in g]
    if args.json:
        print(json.dumps({
            "root": root.path.as_posix(), "artifacts": [a.to_json() for a in shown],
            "runs": _runs_json(groups),
            "total": len(result.artifacts), "skipped": dict(sorted(result.skipped.items())),
            "unknown_versions": dict(sorted(result.unknown_versions.items())),
            "unreadable": [u.to_json() for u in result.unreadable],
        }, indent=2))
        return
    shown_root = plain(root.path.as_posix().rstrip("/") + "/")
    if not exists:
        print(f"no runs under {shown_root} (it does not exist; DOKIMI_RUNS_DIR names another root)")
        return
    if shown:
        print("\n".join(_table(shown)))
        print()
    print("\n".join(_summary(result, groups, shown_root)))


def _show_json(shown: Shown, root: str) -> dict:
    return {**shown.artifact.to_json(), "root": root, "schema": shown.schema,
            "schema_valid": not shown.errors, "schema_errors": shown.errors,
            "contents": shown.contents,
            "siblings": [{"name": s.name, "schema_version": s.schema_version}
                         for s in shown.siblings],
            "siblings_total": shown.siblings_total, "siblings_error": shown.siblings_error}


def _no_run_id(shown: Shown) -> str:
    if shown.artifact.kind in INPUT_KINDS:
        return "- (an input kind: no envelope)"
    if any(e.startswith("run_id:") for e in shown.errors):
        return "- (not a run id: see the schema errors)"
    return "- (saved before the envelope)"


def _show_text(shown: Shown) -> list[str]:
    a = shown.artifact
    errors = _count(len(shown.errors), "error")
    when = a.generated_at or f"- ({_mtime(a)} UTC, the file's mtime)"
    beside = [s.name + (f" ({s.schema_version})" if s.schema_version else "")
              for s in shown.siblings]
    if shown.siblings_total > len(shown.siblings):
        beside.append(f"and {shown.siblings_total - len(shown.siblings)} more")
    rows = [
        ("path", a.path.as_posix()),
        ("kind", f"{a.kind} ({a.schema_version})"),
        ("schema", f"invalid, {errors}" if shown.errors else "valid"),
        ("subject", a.subject or "-"),
        ("model", a.model or "-"),
        ("time", when),
        ("run_id", a.run_id or _no_run_id(shown)),
        ("size", f"{a.size_bytes:,} bytes"),
        ("contents", ", ".join(f"{key}: {n}" for key, n in shown.contents.items()) or "-"),
        ("next to it", shown.siblings_error or ", ".join(beside) or "-"),
    ]
    lines = [f"{label + ':':<12}{plain(value)}" for label, value in rows]
    lines[3:3] = [f"  - {plain(e)}" for e in shown.errors]
    return lines


def _run_text(run: ShownRun) -> list[str]:
    kinds = Counter(a.kind for a in run.artifacts)
    lines = [f"{'run_id:':<12}{plain(run.run_id)}",
             f"{'artifacts:':<12}{len(run.artifacts)} ("
             + ", ".join(f"{kind}: {n}" for kind, n in sorted(kinds.items())) + ")"]
    if run.unreadable:
        first = run.unreadable[0]
        lines.append(f"{'unreadable:':<12}{_count(len(run.unreadable), 'file')} under the root, "
                     f"which may belong to it (first: {plain(first.path.as_posix())}: "
                     f"{plain(first.reason)})")
    table = _table(run.artifacts, with_run=False)
    if any(_untimed(a) for a in run.artifacts):
        table.append("* the file's mtime: the artifact records no time")
    return [*lines, "", *table]


def _show_run(root: str, run: ShownRun, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"root": root, "run_id": run.run_id,
                          "artifacts": [a.to_json() for a in run.artifacts],
                          "unreadable": [u.to_json() for u in run.unreadable]}, indent=2))
    else:
        print("\n".join(_run_text(run)))


def cmd_runs_show(args: argparse.Namespace) -> None:
    """One artifact: its kind, schema check, fields, contents and neighbours; or, for a run id,
    the artifacts of that run. A failed schema check is printed, and the exit code stays 0."""
    root = runs_root()
    if not root.exists():
        raise InputError(f"no runs root: {plain(root.path.as_posix())}/ does not exist "
                         f"(DOKIMI_RUNS_DIR names another root)")
    shown = show_target(root.path, args.path)
    if isinstance(shown, ShownRun):
        _show_run(root.path.as_posix(), shown, args.json)
        return
    if args.json:
        print(json.dumps(_show_json(shown, root.path.as_posix()), indent=2))
    else:
        print("\n".join(_show_text(shown)))


def register(sub: argparse._SubParsersAction, positive_int) -> None:
    runs_p = sub.add_parser(
        "runs", help="The artifacts under the runs root: results/, or DOKIMI_RUNS_DIR")
    runs_sub = runs_p.add_subparsers(dest="runs_cmd", required=True)

    list_p = runs_sub.add_parser(
        "list",
        help="One row per artifact under the runs root (run, kind, subject, model, time, path), "
             "the artifacts of one run together and the runs newest first, and the count of "
             "every file skipped. Reads only.",
    )
    list_p.add_argument("--kind", action="append", choices=sorted(SCHEMA_VERSIONS), metavar="KIND",
                        help="Only artifacts of this kind (eval, arms, novelty, ...); repeatable")
    list_p.add_argument("--subject",
                        help="Only artifacts whose subject is exactly this (a product, a repo id, "
                             "a skill ref)")
    list_p.add_argument("--json", action="store_true",
                        help="Print the artifacts, the runs and the skip counts as JSON")
    list_p.set_defaults(func=cmd_runs_list)

    show_p = runs_sub.add_parser(
        "show",
        help="One artifact: its kind, whether it meets its schema (a failed check is printed and "
             "exits 0), subject, model, time, what it counts and the files next to it; or one "
             "run: the artifacts that carry its run_id. Reads only.",
    )
    show_p.add_argument("path", metavar="PATH|RUN_ID",
                        help="The artifact: its path as runs list prints it (relative to the "
                             "root), or an absolute path inside the root. Or a run_id from runs "
                             "list, when no artifact file has that name")
    show_p.add_argument("--json", action="store_true",
                        help="Print the artifact's fields, schema errors and neighbours (for a "
                             "run_id: the run's artifacts) as JSON")
    show_p.set_defaults(func=cmd_runs_show)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""runs subcommand group: the artifacts under the runs root (#278). Reads only; never spends."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from dokimi.artifacts import SCHEMA_VERSIONS
from dokimi.runs.scan import (
    SKIP_REASONS,
    Artifact,
    ScanResult,
    plain,
    runs_root,
    scan,
    select,
)
from dokimi.runs.show import Shown, show
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


def _table(artifacts: list[Artifact]) -> list[str]:
    rows: list[tuple[str, ...]] = [("SCHEMA", "SUBJECT", "MODEL", "TIME (UTC)", "PATH")]
    rows += [tuple(plain(cell) for cell in (a.schema_version, a.subject or "-", a.model or "-",
                                             _when(a), a.path.as_posix()))
             for a in artifacts]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return ["  ".join(row[i].ljust(w) for i, w in enumerate(widths)) + "  " + row[4]
            for row in rows]


def _summary(result: ScanResult, shown: list[Artifact], root: str) -> list[str]:
    total = _count(len(result.artifacts), "artifact")
    lines = [f"{len(shown)} of {total} under {root} match" if len(shown) != len(result.artifacts)
             else f"{total} under {root}"]
    if any(_untimed(a) for a in shown):
        lines[0] += " (* the file's mtime: the artifact records no time)"
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


def cmd_runs_list(args: argparse.Namespace) -> None:
    """One row per artifact under the runs root, newest first, and the count of what was skipped."""
    root = runs_root()
    exists = root.exists()
    result = scan(root.path) if exists else ScanResult(root=root.path)
    shown = select(result.artifacts, args.kind, args.subject)
    if args.json:
        print(json.dumps({
            "root": root.path.as_posix(), "artifacts": [a.to_json() for a in shown],
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
    print("\n".join(_summary(result, shown, shown_root)))


def _show_json(shown: Shown, root: str) -> dict:
    return {**shown.artifact.to_json(), "root": root, "schema": shown.schema,
            "schema_valid": not shown.errors, "schema_errors": shown.errors,
            "contents": shown.contents,
            "siblings": [{"name": s.name, "schema_version": s.schema_version}
                         for s in shown.siblings],
            "siblings_total": shown.siblings_total, "siblings_error": shown.siblings_error}


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
        ("run_id", "- (no artifact records one yet)"),
        ("size", f"{a.size_bytes:,} bytes"),
        ("contents", ", ".join(f"{key}: {n}" for key, n in shown.contents.items()) or "-"),
        ("next to it", shown.siblings_error or ", ".join(beside) or "-"),
    ]
    lines = [f"{label + ':':<12}{plain(value)}" for label, value in rows]
    lines[3:3] = [f"  - {plain(e)}" for e in shown.errors]
    return lines


def cmd_runs_show(args: argparse.Namespace) -> None:
    """One artifact: its kind, schema check, fields, contents and neighbours. A failed schema
    check is printed, and the exit code stays 0."""
    root = runs_root()
    if not root.exists():
        raise InputError(f"no runs root: {root.path.as_posix()}/ does not exist "
                         f"(DOKIMI_RUNS_DIR names another root)")
    shown = show(root.path, args.path)
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
        help="One row per artifact under the runs root (kind, subject, model, time, path), "
             "newest first, and the count of every file skipped. Reads only.",
    )
    list_p.add_argument("--kind", action="append", choices=sorted(SCHEMA_VERSIONS), metavar="KIND",
                        help="Only artifacts of this kind (eval, arms, novelty, ...); repeatable")
    list_p.add_argument("--subject",
                        help="Only artifacts whose subject is exactly this (a product, a repo id, "
                             "a skill ref)")
    list_p.add_argument("--json", action="store_true",
                        help="Print the artifacts and the skip counts as JSON")
    list_p.set_defaults(func=cmd_runs_list)

    show_p = runs_sub.add_parser(
        "show",
        help="One artifact: its kind, whether it meets its schema (a failed check is printed and "
             "exits 0), subject, model, time, what it counts and the files next to it. Reads only.",
    )
    show_p.add_argument("path", help="The artifact: its path as runs list prints it (relative to "
                                     "the root), or an absolute path inside the root")
    show_p.add_argument("--json", action="store_true",
                        help="Print the artifact's fields, schema errors and neighbours as JSON")
    show_p.set_defaults(func=cmd_runs_show)

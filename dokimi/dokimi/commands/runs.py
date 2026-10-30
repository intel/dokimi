# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""runs subcommand group: the artifacts under the runs root (#278). Reads only; never spends."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from dokimi.artifacts import SCHEMA_VERSIONS
from dokimi.runs.scan import SKIP_REASONS, Artifact, ScanResult, runs_root, scan, select


def _plain(text: str) -> str:
    """``text`` with control characters escaped: a file name or field cannot split a row or
    reach the terminal as an escape sequence."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
                   for c in text)


def _untimed(artifact: Artifact) -> bool:
    return artifact.recorded_at is None and not artifact.generated_at


def _when(artifact: Artifact) -> str:
    """The recorded time in UTC to the minute; the file's mtime, marked ``*``, when it has none."""
    stamp = artifact.recorded_at
    if stamp:
        return stamp.strftime("%Y-%m-%d %H:%M")
    if artifact.generated_at:  # not ISO 8601, or out of range: shown as written
        return artifact.generated_at
    return datetime.fromtimestamp(artifact.mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") + "*"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _table(artifacts: list[Artifact]) -> list[str]:
    rows: list[tuple[str, ...]] = [("SCHEMA", "SUBJECT", "MODEL", "TIME (UTC)", "PATH")]
    rows += [tuple(_plain(cell) for cell in (a.schema_version, a.subject or "-", a.model or "-",
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
              + " (" + ", ".join(f"{v}: {n}" for v, n in sorted(result.unknown_versions.items()))
              + ")"}
    skipped = [f"{result.skipped[reason]} {label}" for reason, label in labels.items()
               if result.skipped[reason]]
    if skipped:
        lines.append("skipped: " + ", ".join(skipped))
    if result.unreadable:
        first = result.unreadable[0]
        lines.append(f"unreadable: {len(result.unreadable)} "
                     f"(first: {_plain(first.path.as_posix())}: {_plain(first.reason)})")
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
    shown_root = _plain(root.path.as_posix().rstrip("/") + "/")
    if not exists:
        print(f"no runs under {shown_root} (it does not exist; DOKIMI_RUNS_DIR names another root)")
        return
    if shown:
        print("\n".join(_table(shown)))
        print()
    print("\n".join(_summary(result, shown, shown_root)))


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

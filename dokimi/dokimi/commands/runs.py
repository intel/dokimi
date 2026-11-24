# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""runs subcommand group: the artifacts under the runs root (#278), and removing runs from it
(#94 R3). Never spends."""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dokimi import cli_flags
from dokimi.commands import runs_baseline
from dokimi.artifacts import INPUT_KINDS, SCHEMA_VERSIONS
from dokimi.exit_codes import EXIT_INFRA
from dokimi.runs import baseline
from dokimi.runs.rm import Removal, apply, baseline_runs, parse_age, plan_gc, plan_rm
from dokimi.runs.scan import (
    SKIP_REASONS,
    Artifact,
    ScanResult,
    Unreadable,
    by_run,
    existing_root,
    lookup,
    plain,
    read_error_reason,
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


def _count(n: int, noun: str, plural: str = "") -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {plural or noun + 's'}"


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


def _unreadable_status(unreadable: list) -> dict:
    """``partial`` and its reason when entries under the root could not be read; the exit code stays 0."""
    if not unreadable:
        return {}
    return {"status": "partial", "reason": f"{_count(len(unreadable), 'entry', 'entries')} under the root could not be read"}


def cmd_runs_list(args: argparse.Namespace) -> None:
    """One row per artifact under the runs root, the artifacts of one run together and the runs
    newest first, and the count of what was skipped."""
    from dokimi import cli_output
    root = runs_root()
    exists = root.exists()
    result = scan(root.path) if exists else ScanResult(root=root.path)
    groups = by_run(select(result.artifacts, args.kind, args.subject))
    shown = [a for g in groups for a in g]
    if args.json:
        cli_output.emit_leaf("runs list", {
            "root": root.path.as_posix(), "artifacts": [a.to_json() for a in shown],
            "runs": _runs_json(groups),
            "total": len(result.artifacts), "skipped": dict(sorted(result.skipped.items())),
            "unknown_versions": dict(sorted(result.unknown_versions.items())),
            "unreadable": [u.to_json() for u in result.unreadable],
        }, summary={"shown": len(shown), "total": len(result.artifacts)}, **_unreadable_status(result.unreadable))
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
        lines.append(f"{'unreadable:':<12}{_count(len(run.unreadable), 'entry', 'entries')} under the root, "
                     f"which may belong to it (first: {plain(first.path.as_posix())}: "
                     f"{plain(first.reason)})")
    table = _table(run.artifacts, with_run=False)
    if any(_untimed(a) for a in run.artifacts):
        table.append("* the file's mtime: the artifact records no time")
    return [*lines, "", *table]


def _show_run(root: str, run: ShownRun, as_json: bool) -> None:
    if as_json:
        from dokimi import cli_output
        cli_output.emit_leaf("runs show", {
            "root": root, "run_id": run.run_id,
            "artifacts": [a.to_json() for a in run.artifacts],
            "unreadable": [u.to_json() for u in run.unreadable]}, **_unreadable_status(run.unreadable))
    else:
        print("\n".join(_run_text(run)))


def cmd_runs_show(args: argparse.Namespace) -> None:
    """One artifact: its kind, schema check, fields, contents and neighbours; or, for a run id,
    the artifacts of that run. A failed schema check is printed, and the exit code stays 0."""
    from dokimi import cli_output
    root = existing_root()
    shown = show_target(root.path, args.path)
    if isinstance(shown, ShownRun):
        _show_run(root.path.as_posix(), shown, args.json)
        return
    if args.json:
        cli_output.emit_leaf("runs show", _show_json(shown, root.path.as_posix()),
                             summary={"schema_valid": not shown.errors})
    else:
        print("\n".join(_show_text(shown)))


def _first(entries: list[Unreadable]) -> str:
    return f"first: {plain(entries[0].path.as_posix())}: {plain(entries[0].reason)}"


def _left_lines(removal: Removal, of_run: bool) -> list[str]:
    """What a removal left behind: the entries it could not read, and the files it could not remove."""
    lines = []
    if removal.unreadable:
        why = ", which may belong to it" if of_run else ""
        lines.append(f"unreadable: {_count(len(removal.unreadable), 'entry', 'entries')} under the "
                     f"root left in place{why} ({_first(removal.unreadable)})")
    if removal.changed:
        lines.append(f"left in place: {_count(len(removal.changed), 'file')} replaced since selected "
                     f"(first: {plain(removal.changed[0].as_posix())})")
    if removal.not_removed:
        lines.append(f"not removed: {_count(len(removal.not_removed), 'file')} "
                     f"({_first(removal.not_removed)})")
    return lines


def _rows(artifacts: list[Artifact], with_run: bool) -> list[str]:
    table = _table(artifacts, with_run)
    if any(_untimed(a) for a in artifacts):
        table.append("* the file's mtime: the artifact records no time")
    return table


def _artifacts(removed: int, removal: Removal) -> str:
    """``2 artifacts``; ``1 of 2 artifacts`` when some could not be removed or were replaced."""
    left = len(removal.not_removed) + len(removal.changed)
    if not left:
        return _count(removed, "artifact")
    return f"{removed} of {_count(removed + left, 'artifact')}"


def _rm_text(removal: Removal, verb: str, run_id: str | None) -> list[str]:
    size = f"{removal.size_bytes:,} bytes"
    if run_id is None:
        head = f"{verb} {plain(removal.file.as_posix())} ({size})" if removal.file else f"{verb} nothing"
        return [head, *_left_lines(removal, False)]
    run = removal.runs[0] if removal.runs else []
    lines = [f"{verb} run {plain(run_id)}: {_artifacts(len(run), removal)}, {size}",
             *_left_lines(removal, True)]
    return lines + (["", *_rows(run, with_run=False)] if run else [])


def _gc_text(removal: Removal, verb: str, age: str, root: str, selected: int) -> list[str]:
    """``selected`` is the count of runs selected before any was removed; ``removal.runs`` holds
    those that lost an artifact."""
    artifacts = [a for run in removal.runs for a in run]
    lines = [*_rows(artifacts, with_run=True), ""] if artifacts else []
    if selected:
        runs = _count(selected, "run")
        if len(removal.runs) != selected:
            runs = f"{len(removal.runs)} of {runs}"
        lines.append(f"{verb} {runs} older than {age} under {root}: "
                     f"{_artifacts(len(artifacts), removal)}, {removal.size_bytes:,} bytes")
    else:
        lines.append(f"no run under {root} to remove: none is older than {age}"
                     + (" but those a baseline holds" if removal.kept else ""))
    if removal.kept:
        lines.append(f"kept {_count(len(removal.kept), 'run')} older than {age} that a baseline holds: "
                     + ", ".join(f"{plain(r)} ({plain(b)})" for r, b in removal.kept.items()))
    return lines + _left_lines(removal, False)


def _emit_removal(kind: str, removal: Removal, args: argparse.Namespace, extra: dict) -> None:
    """The ``--json`` document: ``failed`` (exit 5) when a file could not be removed."""
    from dokimi import cli_output
    data = {"root": removal.root.as_posix(), "dry_run": args.dry_run, **extra,
            "runs": _runs_json(removal.runs), "paths": [p.as_posix() for p in removal.paths],
            "size_bytes": removal.size_bytes,
            "not_removed": [{"path": u.path.as_posix(), "reason": u.reason} for u in removal.not_removed],
            "changed": [p.as_posix() for p in removal.changed],
            "unreadable": [u.to_json() for u in removal.unreadable]}
    status = _unreadable_status(removal.unreadable)
    if removal.not_removed:
        message = f"{_count(len(removal.not_removed), 'file')} could not be removed"
        status = {"status": "failed", "reason": message,
                  "error": cli_output.error_object("infra", message)}
    cli_output.emit_leaf(kind, data, summary={"paths": len(removal.paths), "dry_run": args.dry_run},
                         **status)


def _finish(kind: str, removal: Removal, args: argparse.Namespace, extra: dict, text) -> None:
    """Remove (unless ``--dry-run``), print, and exit 5 when a file could not be removed (#94 §2.6)."""
    if not args.dry_run:
        apply(removal)
    if args.json:
        _emit_removal(kind, removal, args, extra)
    else:
        print("\n".join(text("would remove" if args.dry_run else "removed")))
    if removal.not_removed:
        sys.exit(EXIT_INFRA)


def cmd_runs_rm(args: argparse.Namespace) -> None:
    """The artifacts of one run, or one file under the runs root; never a path outside it."""
    root = existing_root()
    removal = plan_rm(root.path, args.path)
    run_id = removal.runs[0][0].run_id if removal.runs else None
    _finish("runs rm", removal, args, {"run_id": run_id},
            lambda verb: _rm_text(removal, verb, run_id))


def _baselines_held(root: Path) -> dict[str, str]:
    """The runs the pointers under ``root`` hold and, after them, those of the copies ``baseline
    save`` listed in its manifest."""
    held = baseline.held_runs(root)
    for run_id, name in _manifest_held().items():
        held.setdefault(run_id, name)
    return held


def _manifest_held() -> dict[str, str]:
    from dokimi.commands.baseline import BASELINES_DIR, _baseline_manifest
    manifest = BASELINES_DIR / "manifest.json"
    try:  # _baseline_manifest's Path.exists() reads "no permission" as absent on Python 3.14
        unreachable = lookup(manifest) is None and lookup(manifest, follow_symlinks=False)
    except OSError as exc:
        raise InputError(f"{manifest.as_posix()}: {read_error_reason(exc)}, so the runs the "
                         "baselines hold are not known") from exc
    if unreachable:  # a dangling link or a link loop: there, but not readable
        raise InputError(f"{manifest.as_posix()}: a link to nothing, so the runs the baselines "
                         "hold are not known")
    return baseline_runs(_baseline_manifest()["baselines"])


def _age(text: str) -> timedelta:
    try:
        return parse_age(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def cmd_runs_gc(args: argparse.Namespace) -> None:
    """The runs whose newest artifact is older than ``--older-than``, bar (with ``--keep-baselines``)
    the runs a saved baseline holds."""
    root = runs_root()
    exists = root.exists()
    # a save waits for the lock, so it cannot point at a run gc is removing (docs/runs.md)
    hold = exists and args.keep_baselines and not args.dry_run
    with baseline.locked(root.path) if hold else contextlib.nullcontext():
        _gc(args, root.path, exists)


def _gc(args: argparse.Namespace, root: Path, exists: bool) -> None:
    held = _baselines_held(root) if args.keep_baselines else {}
    cutoff = time.time() - args.older_than.total_seconds()
    removal = plan_gc(root, cutoff, held) if exists else Removal(root=root)
    age = f"{args.older_than.days}d"
    shown_root = plain(root.as_posix().rstrip("/") + "/")
    selected = len(removal.runs)  # apply drops a run none of whose files could be removed
    _finish("runs gc", removal, args, {"older_than": age, "keep_baselines": args.keep_baselines,
                                      "kept": [{"run_id": r, "baseline": b} for r, b in removal.kept.items()]},
            lambda verb: _gc_text(removal, verb, age, shown_root, selected))


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
                        help="Print cli_output.v1 on stdout (the artifacts, the runs and the skip counts under data)")
    list_p.set_defaults(func=cmd_runs_list, emits_cli_output=True, cli_kind="runs list")

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
                        help="Print cli_output.v1 on stdout (the artifact's fields, schema errors and "
                             "neighbours under data; for a run_id, the run's artifacts)")
    show_p.set_defaults(func=cmd_runs_show, emits_cli_output=True, cli_kind="runs show")

    rm_p = runs_sub.add_parser(
        "rm",
        help="Remove one run, the artifacts that carry its run_id, or one file under the runs root; "
             "never anything outside it. A run removes only its artifacts: other files and "
             "directories stay; a PATH removes that one file, artifact or not.",
    )
    rm_p.add_argument("path", metavar="RUN_ID|PATH",
                      help="A run_id from runs list, or a file: its path as runs list prints it "
                           "(relative to the root), or an absolute path inside the root. A symlink "
                           "is removed itself, never its target")
    cli_flags.add(rm_p, "dry_run", "--dry-run", action="store_true",
                  help="List what would be removed; remove nothing")
    cli_flags.add(rm_p, "output", "--json", action="store_true",
                  help="Print cli_output.v1 on stdout (the run and the paths removed under data)")
    rm_p.set_defaults(func=cmd_runs_rm, emits_cli_output=True, cli_kind="runs rm")

    gc_p = runs_sub.add_parser(
        "gc",
        help="Remove every run under the runs root whose newest artifact is older than --older-than; "
             "artifacts with no run_id stay.",
    )
    cli_flags.add(gc_p, "retention", "--older-than", metavar="AGE", type=_age, required=True,
                  help="An age in days, such as 30d: a run whose newest artifact records a time "
                       "(or, recording none, has an mtime) before then is removed")
    cli_flags.add(gc_p, "retention", "--keep-baselines", action="store_true",
                  help="Keep the runs a baseline points to (runs baseline save), and those "
                       "whose run_id a copy saved by baseline save carries "
                       "(baselines/eval/manifest.json)")
    cli_flags.add(gc_p, "dry_run", "--dry-run", action="store_true",
                  help="List what would be removed; remove nothing")
    cli_flags.add(gc_p, "output", "--json", action="store_true",
                  help="Print cli_output.v1 on stdout (the runs removed and kept under data)")
    gc_p.set_defaults(func=cmd_runs_gc, emits_cli_output=True, cli_kind="runs gc")

    runs_baseline.register(runs_sub)

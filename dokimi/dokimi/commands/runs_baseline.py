# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""runs baseline: save, list and remove the named pointers to a run under the runs root (#94
§2.3, R3). Never spends."""

from __future__ import annotations

import argparse
import sys

from dokimi import cli_flags
from dokimi.exit_codes import EXIT_INFRA, EXIT_INVALID
from dokimi.runs import baseline
from dokimi.runs.scan import existing_root, plain, runs_root


def _infra(kind: str, args: argparse.Namespace, message: str) -> None:
    """A pointer that could not be written or removed: exit 5, as ``runs rm`` exits (#94 §2.6)."""
    from dokimi import cli_output
    if args.json:
        cli_output.emit(cli_output.error_document(kind, "infra", message,
                                                  run_id=cli_output.current_run_id()))
    else:
        print(f"Error: {message}", file=sys.stderr)
    sys.exit(EXIT_INFRA)


def _conflict(args: argparse.Namespace, root: str, conflict: baseline.Conflict) -> None:
    """The pointer moved since the caller read it: exit 4, ``invalid`` (a stale baseline, #94 §2.6)."""
    from dokimi import cli_output
    if args.json:
        cli_output.emit_leaf("runs baseline save", {
            "root": root, "name": conflict.name, "run_id": args.run,
            "expected": conflict.expected, "found": conflict.found},
            status="failed", verdict="invalid", reason="stale_baseline")
    else:
        print(f"Error: {conflict}", file=sys.stderr)
    sys.exit(EXIT_INVALID)


def cmd_runs_baseline_save(args: argparse.Namespace) -> None:
    """Point NAME at a run under the runs root; with ``--expect-previous``, only while it holds
    that run."""
    from dokimi import cli_output
    root = existing_root().path
    try:
        saved = baseline.save(root, args.name, args.run, args.expect_previous)
    except baseline.Conflict as conflict:
        _conflict(args, root.as_posix(), conflict)
        return
    except OSError as exc:
        _infra("runs baseline save", args, f"baseline {args.name}: cannot write: {exc.strerror or exc}")
        return
    if args.json:
        cli_output.emit_leaf("runs baseline save", {
            "root": root.as_posix(), "name": saved.name, "run_id": saved.run_id,
            "previous": saved.previous, "artifacts": saved.artifacts, "path": saved.path.as_posix()})
        return
    noun = "artifact" if saved.artifacts == 1 else "artifacts"
    before = "none" if saved.previous is None else plain(saved.previous) or "(empty)"
    print(f"saved baseline {saved.name} -> {saved.run_id} ({saved.artifacts} {noun}); before: {before}")


def cmd_runs_baseline_list(args: argparse.Namespace) -> None:
    """Every baseline under the runs root, by name, and the entries of the store that are not one."""
    from dokimi import cli_output
    root = runs_root()
    listing = baseline.read_all(root.path) if root.exists() else baseline.Listing()
    if args.json:
        status = ({"status": "partial", "reason": f"{len(listing.unreadable)} baseline file(s) "
                   "could not be read"} if listing.unreadable else {})
        cli_output.emit_leaf("runs baseline list", {
            "root": root.path.as_posix(),
            "baselines": [{"name": p.name, "run_id": p.run_id, "saved_at": p.saved_at_iso}
                          for p in listing.pointers],
            "unreadable": [u.to_json() for u in listing.unreadable]},
            summary={"baselines": len(listing.pointers)}, **status)
        return
    shown_root = plain(root.path.as_posix().rstrip("/") + "/")
    if listing.pointers:
        rows = [("NAME", "RUN", "SAVED (UTC)")] + [
            (p.name, p.run_id, p.saved_at_iso.replace("T", " ")[:16]) for p in listing.pointers]
        widths = [max(len(row[i]) for row in rows) for i in range(2)]
        print("\n".join(f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]}" for r in rows))
        print()
    n = len(listing.pointers)
    print(f"{n} baseline{'' if n == 1 else 's'} under {shown_root}")
    for entry in listing.unreadable:
        print(f"unreadable: {plain(entry.path.as_posix())}: {plain(entry.reason)}")


def cmd_runs_baseline_rm(args: argparse.Namespace) -> None:
    """Remove one baseline; the run it points to stays."""
    from dokimi import cli_output
    root = existing_root().path
    try:
        held = baseline.remove(root, args.name)
    except OSError as exc:
        _infra("runs baseline rm", args, f"baseline {args.name}: cannot remove: {exc.strerror or exc}")
        return
    if args.json:
        cli_output.emit_leaf("runs baseline rm", {"root": root.as_posix(), "name": args.name,
                                                  "run_id": held})
        return
    print(f"removed baseline {args.name} (it held {held or 'no run id'}); the run stays")


def register(runs_sub: argparse._SubParsersAction) -> None:
    baseline_p = runs_sub.add_parser(
        "baseline", help="Named pointers to a run under the runs root (.baselines/), like git tags")
    sub = baseline_p.add_subparsers(dest="baseline_cmd", required=True)

    save_p = sub.add_parser(
        "save",
        help="Point NAME at RUN, replacing what it pointed to; with --expect-previous, only while "
             "it points to that run (exit 4 otherwise, the pointer left as it was)")
    save_p.add_argument("name", metavar="NAME",
                        help="The baseline: segments of letters, digits, '.', '_', '@' or '-' "
                             "joined by '/', such as main or skill/a@main")
    save_p.add_argument("run", metavar="RUN",
                        help="A run_id from runs list: some artifact under the runs root carries it")
    cli_flags.add(save_p, "baseline", "--expect-previous", metavar="RUN_ID",
                  help="Save only while NAME points to RUN_ID: a compare-and-swap, so of two "
                       "saves that read the same pointer one wins and the other exits 4")
    cli_flags.add(save_p, "output", "--json", action="store_true",
                  help="Print cli_output.v1 on stdout (the baseline, its run and what it held under data)")
    save_p.set_defaults(func=cmd_runs_baseline_save, emits_cli_output=True,
                        cli_kind="runs baseline save")

    list_p = sub.add_parser("list", help="Every baseline under the runs root: its name, its run "
                                         "and when it was saved. Reads only.")
    cli_flags.add(list_p, "output", "--json", action="store_true",
                  help="Print cli_output.v1 on stdout (the baselines under data)")
    list_p.set_defaults(func=cmd_runs_baseline_list, emits_cli_output=True,
                        cli_kind="runs baseline list")

    rm_p = sub.add_parser("rm", help="Remove one baseline; the run it points to stays")
    rm_p.add_argument("name", metavar="NAME", help="The baseline, as runs baseline list prints it")
    cli_flags.add(rm_p, "output", "--json", action="store_true",
                  help="Print cli_output.v1 on stdout (the baseline and the run it held under data)")
    rm_p.set_defaults(func=cmd_runs_baseline_rm, emits_cli_output=True, cli_kind="runs baseline rm")

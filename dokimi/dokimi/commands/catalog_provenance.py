# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog provenance``: which upstream version each skill copy under ``--root`` came from, and how far behind it is.

Read-only and offline: the upstreams are the caller's (``--upstream`` checkouts, ``--upstream-manifest`` files). The
matching is in :mod:`dokimi.external.upstream`; this module reads the catalog and prints the rows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dokimi import cli_flags
from dokimi.utils import InputError


def _rel(skill_dir: Path, root: Path) -> str:
    try:
        return skill_dir.relative_to(root).as_posix()
    except ValueError:
        return skill_dir.as_posix()


def _behind_text(row: dict) -> str:
    if row["match"] is None:
        return "-"
    if row["at_head"]:
        return "at head"
    commits = "" if row["behind_commits"] is None else f"{row['behind_commits']} commits"
    months = "" if row["behind_months"] is None else f"{row['behind_months']} mo"
    removed = "path removed at head" if row.get("removed_at_head") else ""
    return ", ".join(part for part in (commits, months, removed) if part) or "unknown"


def _text(report: dict) -> str:
    summary = report["summary"]
    lines = []
    for up in report["upstreams"]:
        head = f"head {up['head_commit']} {up['head_date'] or ''}".strip() if up["head_commit"] else "no head"
        note = {
            "shallow": " (shallow clone: older versions are not seen)",
            "partial": f" ({up['missing_blobs']} blobs the clone does not hold: their versions are not seen)",
        }.get(up["history"], "")
        lines.append(f"# upstream {up['name']}: {head}, history {up['history']}{note}")
    median = summary["median_months_behind"]
    lines.append(
        f"# skills ({summary['skills']} under {report['root']}: {summary['exact']} exact, "
        f"{summary['similar']} similar, {summary['unmatched']} unmatched; {summary['edited']} edited; "
        f"{summary['at_head']} at head; median {'-' if median is None else median} months behind)"
    )
    lines.append("skill\tmatch\tupstream\tversion\tbehind\tedited")
    for row in report["skills"]:
        upstream_text = "-" if row["upstream"] is None else f"{row['upstream']}:{row['path']}"
        version = "-" if row["commit"] is None else f"{row['commit']}{' ' + row['date'] if row['date'] else ''}"
        if row["version"]:
            version = f"v{row['version']} {version}"
        edited = "-" if row["edited"] is None else "yes" if row["edited"] else "no"
        lines.append(
            f"{row['skill']}\t{row['match'] or 'unmatched'}\t{upstream_text}\t{version}\t"
            f"{_behind_text(row)}\t{edited}"
        )
    return "\n".join(lines)


def cmd_catalog_provenance(args: argparse.Namespace) -> None:
    from dokimi.external import read_skill_manifest
    from dokimi.external import upstream

    root = Path(args.root)
    if not root.is_dir():
        raise InputError(f"--root is not a directory: {root}")
    if not args.upstream and not args.upstream_manifest:
        raise InputError("catalog provenance needs --upstream CHECKOUT (a git checkout) or "
                         "--upstream-manifest FILE: dokimi ships no upstream list")
    index = upstream.build(args.upstream or (), args.upstream_manifest or ())
    try:
        entries = read_skill_manifest(root)
    except ValueError as exc:
        raise InputError(f"skills.yaml: {exc}") from exc
    rows = []
    for entry in entries:
        # A skill that does not load (no description, broken YAML) still has bytes: it is matched like the others.
        path = entry.skill.path if entry.skill is not None else entry.path
        if path is not None and path.is_dir():
            path = path / "SKILL.md"
        if path is None or not path.is_file():
            continue  # no file to read: `catalog check` reports it
        raw = path.read_bytes()
        match = index.match(raw, upstream.frontmatter_name(raw))
        rows.append({"skill": _rel(path.parent, root), "name": entry.name, **upstream.to_row(match)})
    report = {
        "root": str(root),
        "upstreams": upstream.upstream_rows(index),
        "skills": rows,
        "summary": upstream.summary(rows),
    }
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(_text(report))


def register(catalog_sub) -> None:
    """Add ``catalog provenance`` to the ``catalog`` group."""
    prov_p = catalog_sub.add_parser(
        "provenance",
        help="For each skill under --root: the upstream version it copies, how far behind head, and whether it was edited",
    )
    prov_p.add_argument("--root", required=True, help="Local checkout of the skills to match")
    cli_flags.add(
        prov_p, "upstream", "--upstream", action="append", default=None, metavar="CHECKOUT",
        help="A local git checkout of an upstream (its history gives the versions). Repeatable; the first one "
             "wins a tie. Nothing is fetched: clone it yourself, full history for an exact count",
    )
    cli_flags.add(
        prov_p, "upstream", "--upstream-manifest", action="append", default=None, metavar="FILE",
        help="A JSON manifest of one upstream: {\"schema\": \"dokimi.upstream.v1\", \"name\", \"head\": {commit, date}, "
             "\"skills\": [{path, content, head?, commit?, date?}]}. Repeatable",
    )
    prov_p.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="text: one line per skill (default); json: the upstreams, one row per skill and the summary",
    )
    prov_p.set_defaults(func=cmd_catalog_provenance)

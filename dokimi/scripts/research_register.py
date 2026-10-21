#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Check, query and export the research register in ``research/records/``.

Each record is one Markdown file, ``research/records/<type>/<id>.md``: YAML
front matter with the typed fields of ``research/record.v1.json`` and a free
Markdown body (setup, result, rationale). Ideas, experiments, findings,
decisions and implementation references point at each other by id, so a
refuted experiment stays findable from the idea it tested.

    python scripts/research_register.py check
    python scripts/research_register.py query --type experiment --where outcome=refuted,inconclusive
    python scripts/research_register.py query --type experiment --ref I001
    python scripts/research_register.py next experiment
    python scripts/research_register.py export > register.json
    python scripts/research_register.py render > map.md

``check`` exits 1 when a record breaks its schema, sits under the wrong path,
points at a missing record or one of the wrong type, or cites a retracted
experiment. ``export`` writes every record plus the reference edges and an idea
tree grouped by map theme. ``render`` turns that tree into the plans-and-research
map: a Mermaid mindmap and a table, as Markdown.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

PROJECT = Path(__file__).resolve().parents[1]
RESEARCH = PROJECT / "research"
SCHEMA = RESEARCH / "record.v1.json"
# The license header every file carries, as an HTML comment after the front matter.
_HEADER = re.compile(r"\A\s*<!--.*?SPDX-License-Identifier.*?-->\s*", re.DOTALL)

PREFIX = {"idea": "I", "experiment": "X", "finding": "F", "decision": "D", "implref": "R"}
# field -> record type it must point at, per record type.
REFS: dict[str, dict[str, str]] = {
    "experiment": {"idea": "idea"},
    "finding": {"experiments": "experiment"},
    "decision": {"findings": "finding", "ideas": "idea", "superseded_by": "decision"},
    "implref": {"decision": "decision"},
}
THEMES = {
    "P": "User workflows and integrations",
    "E": "Engineering foundations",
    "V": "Eval design, quality and infrastructure",
    "K": "Skill scope, knowledge and utility",
    "G": "Graphs, context and MCP",
    "O": "Traces, optimization and visualization",
    "C": "Judging and empirical validation",
    "D": "Documentation and adoption",
    "W": "Research workspace and records",
}


@dataclass
class Record:
    fields: dict[str, Any]
    body: str
    path: Path

    @property
    def id(self) -> str:
        return str(self.fields.get("id", self.path.stem))

    @property
    def type(self) -> str:
        return str(self.fields.get("type", ""))


def parse(path: Path) -> Record:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError(f"{path}: no YAML front matter")
    front, body = text[4:].split("\n---\n", 1)
    fields = yaml.safe_load(front)
    if not isinstance(fields, dict):
        raise ValueError(f"{path}: front matter is not a mapping")
    # YAML reads an unquoted 2026-10-14 as a date; the schema wants the string.
    fields = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fields.items()}
    return Record(fields, _HEADER.sub("", body).strip(), path)


def load(root: Path = RESEARCH) -> tuple[dict[str, Record], list[str]]:
    records: dict[str, Record] = {}
    errors: list[str] = []
    base = root / "records"
    for path in sorted(base.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.suffix != ".md" or len(path.relative_to(base).parts) != 2:
            errors.append(f"{path}: not a record, records live at records/<type>/<id>.md")
            continue
        try:
            record = parse(path)
        except (ValueError, yaml.YAMLError) as exc:
            errors.append(str(exc))
            continue
        if record.id in records:
            errors.append(f"{record.id}: duplicate id in {path} and {records[record.id].path}")
            continue
        records[record.id] = record
    return records, errors


def ref_values(record: Record, field: str) -> list[str]:
    value = record.fields.get(field)
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def check(records: dict[str, Record], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for rid, record in sorted(records.items()):
        kind = record.type
        if kind not in PREFIX:
            errors.append(f"{rid}: unknown type {kind!r}")
            continue
        sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{kind}"}
        for err in jsonschema.Draft202012Validator(sub).iter_errors(record.fields):
            errors.append(f"{rid}: {err.json_path}: {err.message}")
        if record.path.parent.name != kind or record.path.stem != rid:
            errors.append(f"{rid}: expected at records/{kind}/{rid}.md, found {record.path.name}")
        if not rid.startswith(PREFIX[kind]):
            errors.append(f"{rid}: a {kind} id starts with {PREFIX[kind]}")
        for field, want in REFS.get(kind, {}).items():
            for target in ref_values(record, field):
                if not isinstance(target, str):
                    continue  # the schema already reports a non-id value
                if target not in records:
                    errors.append(f"{rid}.{field}: no record {target}")
                elif records[target].type != want:
                    errors.append(f"{rid}.{field}: {target} is {records[target].type}, "
                                  f"want {want}")
        state = record.fields.get("state")
        if state == "superseded" and "superseded_by" not in record.fields:
            errors.append(f"{rid}: a superseded decision names its superseded_by")
        if "superseded_by" in record.fields and state != "superseded":
            errors.append(f"{rid}: superseded_by is set but state is {state!r}, not 'superseded'")
        if record.fields.get("superseded_by") == rid:
            errors.append(f"{rid}: superseded_by names the record itself")
        else:
            seen = {rid}
            step = record.fields.get("superseded_by")
            while isinstance(step, str) and step in records and step not in seen:
                seen.add(step)
                step = records[step].fields.get("superseded_by")
            if step == rid:
                errors.append(f"{rid}: superseded_by chain loops back to {rid}")
        for target in ref_values(record, "experiments"):
            if target in records and records[target].fields.get("retracted"):
                errors.append(f"{rid}.experiments: {target} is retracted")
    return errors


def _text(value: object) -> str:
    # Spell booleans as the records do (`retracted: true`), not as Python's str() does.
    return str(value).lower() if isinstance(value, bool) else str(value)


def matches(record: Record, where: list[str], ref: str | None, kind: str | None) -> bool:
    if kind and record.type != kind:
        return False
    for clause in where:
        field, _, wanted = clause.partition("=")
        value = record.fields.get(field)
        if value is None:
            return False  # a missing field matches no value, not the string "None"
        values = [_text(v) for v in value] if isinstance(value, list) else [_text(value)]
        if not set(values) & set(wanted.split(",")):
            return False
    if ref:
        return any(ref in ref_values(record, f) for f in REFS.get(record.type, {}))
    return True


def next_id(records: dict[str, Record], kind: str) -> str:
    prefix = PREFIX[kind]
    used = [int(r[len(prefix):]) for r in records if r.startswith(prefix) and r[len(prefix):].isdigit()]
    return f"{prefix}{max(used, default=0) + 1:03d}"


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""  # git is not installed


def export(records: dict[str, Record], root: Path = RESEARCH) -> dict[str, Any]:
    edges = [{"from": rid, "to": target, "rel": field}
             for rid, record in sorted(records.items())
             for field in REFS.get(record.type, {})
             for target in ref_values(record, field)]
    pointing: dict[str, list[str]] = {}
    for edge in edges:
        pointing.setdefault(edge["to"], []).append(edge["from"])
    themes = []
    for key, title in THEMES.items():
        ideas = [r for r in records.values() if r.type == "idea" and r.fields["theme"] == key]
        themes.append({"id": key, "title": title, "ideas": [
            {"id": r.id, "title": r.fields["title"], "status": r.fields["status"],
             "experiments": [{"id": x, "outcome": records[x].fields["outcome"],
                              "retracted": bool(records[x].fields.get("retracted"))}
                             for x in sorted(pointing.get(r.id, []))
                             if records[x].type == "experiment"]}
            for r in sorted(ideas, key=lambda r: r.id)]})
    commit = _git(root, "rev-parse", "HEAD")
    # Records edited after that commit make the stamp a lie unless it says so.
    dirty = bool(commit and _git(root, "status", "--porcelain", "--", "."))
    return {"commit": commit or None, "dirty": dirty,
            "records": [{**r.fields, "body": r.body} for _, r in sorted(records.items())],
            "edges": edges, "themes": themes}


OUTCOME_MARK = {"supported": "+", "refuted": "-", "inconclusive": "?", "aborted": "x",
                "planned": "...", "running": "..."}


def _label(text: str) -> str:
    # Mermaid mindmap labels break on brackets and parentheses.
    return re.sub(r"[()\[\]{}]", "", text)


def render(data: dict[str, Any]) -> str:
    titles = {r["id"]: r["title"] for r in data["records"]}
    stamp = f"`{data['commit'] or 'unknown'}`"
    if data.get("dirty"):
        stamp += " (with uncommitted changes)"
    lines = ["# Plans and research map", "",
             f"Generated by `scripts/research_register.py render` from commit "
             f"{stamp}. Outcome marks: + supported, - refuted, "
             "? inconclusive, x aborted, ... planned or running.", "",
             "```mermaid", "mindmap", "  root((Dokimi research))"]
    rows = []
    for theme in data["themes"]:
        if not theme["ideas"]:
            continue
        lines.append(f"    {theme['id']}[{_label(theme['title'])}]")
        for idea in theme["ideas"]:
            lines.append(f"      {idea['id']}[{idea['id']} {_label(idea['title'])} - {idea['status']}]")
            for exp in idea["experiments"]:
                mark = "retracted" if exp["retracted"] else OUTCOME_MARK[exp["outcome"]]
                lines.append(f"        {exp['id']}[{exp['id']} {mark} {_label(titles[exp['id']])}]")
                outcome = exp["outcome"] + (", retracted" if exp["retracted"] else "")
                rows.append(f"| {theme['id']} | {idea['id']} | {exp['id']} | {outcome} | "
                            f"{titles[exp['id']].replace('|', '/')} |")
            if not idea["experiments"]:
                rows.append(f"| {theme['id']} | {idea['id']} | | | "
                            f"{idea['title'].replace('|', '/')} |")
    lines += ["```", "", "| Theme | Idea | Experiment | Outcome | Title |",
              "|---|---|---|---|---|", *rows]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=RESEARCH, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate every record and its references")
    query = sub.add_parser("query", help="list records matching every filter")
    query.add_argument("--type", choices=PREFIX)
    query.add_argument("--where", action="append", default=[], metavar="FIELD=V1,V2")
    query.add_argument("--ref", metavar="ID", help="records that point at ID")
    nxt = sub.add_parser("next", help="print the next free id for a record type")
    nxt.add_argument("type", choices=PREFIX)
    sub.add_parser("export", help="write records, edges and the theme tree as JSON")
    sub.add_parser("render", help="write the theme tree as a Mermaid mindmap and table")
    args = parser.parse_args(argv)

    records, errors = load(args.root)
    schema = json.loads((args.root / SCHEMA.name).read_text(encoding="utf-8"))
    errors += check(records, schema)
    if args.command == "check":
        print("\n".join(errors) if errors else f"OK: {len(records)} records")
        return 1 if errors else 0
    # Every other command reads the records as valid, so refuse a broken register up front.
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.command == "query":
        fields = {p for d in schema["$defs"].values() for p in d.get("properties", {})}
        bad = [c for c in args.where if "=" not in c or c.partition("=")[0] not in fields]
        if bad:
            print(f"--where needs FIELD=VALUE with a schema field: {', '.join(bad)}",
                  file=sys.stderr)
            return 2
        if args.ref and args.ref not in records:
            print(f"--ref {args.ref}: no record {args.ref}", file=sys.stderr)
            return 2
        for record in sorted(records.values(), key=lambda r: r.id):
            if matches(record, args.where, args.ref, args.type):
                extra = record.fields.get("outcome") or record.fields.get("state") or \
                    record.fields.get("status") or record.fields.get("confidence") or ""
                if record.fields.get("retracted"):
                    extra += ", retracted"
                print(f"{record.id}\t{record.type}\t{extra}\t{record.fields['title']}")
    elif args.command == "next":
        print(next_id(records, args.type))
    elif args.command == "render":
        sys.stdout.write(render(export(records, args.root)))
    else:
        json.dump(export(records, args.root), sys.stdout, indent=1, ensure_ascii=False)
        print()
    return 0


if __name__ == "__main__":
    # Redirected output defaults to the locale code page (cp1252 on Windows), which
    # writes an export that is not UTF-8 and fails on any title outside it.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())

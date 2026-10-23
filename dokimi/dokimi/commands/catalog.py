# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""catalog subcommand group: scan external skill/task catalogs before use.

Points at a local checkout the caller already produced (e.g.
``git clone https://github.com/uxlfoundation/skills`` or
``https://github.com/intel/skills``) -- nothing here
fetches anything over the network. See ``dokimi.external.catalog``
for the manifest formats understood.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_catalog_scan(args: argparse.Namespace) -> None:
    from dokimi.external import read_skill_manifest, read_task_manifest

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"--root is not a directory: {root}")

    any_failure = False

    if args.kind in ("skills", "all"):
        try:
            skills = read_skill_manifest(root)
        except ValueError as exc:
            raise SystemExit(f"skills.yaml: {exc}") from exc
        print(f"# skills ({len(skills)} declared under {root})")
        for skill in skills:
            state = "ok" if skill.loaded else f"ERROR: {skill.error}"
            status = skill.status or "-"
            print(f"{skill.name}\t{status}\t{skill.path}\t{state}")
            if not skill.loaded:
                any_failure = True
        print()

    if args.kind in ("tasks", "all"):
        try:
            tasks = read_task_manifest(root, tasks_subdir=args.tasks_subdir)
        except ValueError as exc:
            raise SystemExit(f"suites.json: {exc}") from exc
        print(f"# tasks ({len(tasks)} declared under {root})")
        for task in tasks:
            if task.loaded:
                state = "ok"
            elif task.skipped:
                state = f"skipped: {task.reason}"
            else:
                state = f"ERROR: {task.reason}"
                any_failure = True
            track = task.track or "-"
            status = task.status or "-"
            print(f"{task.name}\t{status}\t{track}\t{state}")

    if any_failure:
        raise SystemExit(1)


def _read_questions_file(path: str):
    """The parsed ``--questions`` file, or exit with a message naming it."""
    import json

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # ValueError covers JSONDecodeError and bad UTF-8
        raise SystemExit(f"--questions {path}: {exc}") from exc


def cmd_catalog_trigger_set(args: argparse.Namespace) -> None:
    import json

    from dokimi.external import trigger_set as ts
    from dokimi.skills import discover_skills

    catalog_root = Path(args.catalog)
    if not catalog_root.is_dir():
        raise SystemExit(f"--catalog is not a directory: {catalog_root}")
    catalog = discover_skills(catalog_root)
    # Checked before any --rewrite call, so a bad invocation costs no LLM calls.
    if args.format == "skill-creator":
        if not args.target:
            raise SystemExit("--format skill-creator needs --target <skill>")
        if args.target not in {s.name for s in catalog}:
            raise SystemExit(f"--target {args.target!r} is not a skill in --catalog "
                             f"(have: {', '.join(sorted(s.name for s in catalog))})")
    sources = []
    for spec in args.questions:
        path, _, rule = spec.rpartition("=")  # labels never contain '=', paths may
        if not path or not rule:
            raise SystemExit(f"--questions needs PATH=LABEL (a skill name, 'none' or '@field'): {spec}")
        data = _read_questions_file(path)
        questions = data.get("questions", data) if isinstance(data, dict) else data
        if not isinstance(questions, list) or not all(isinstance(q, dict) for q in questions):
            raise SystemExit(f"{path}: expected a list of question objects")
        sources.append((questions, rule))

    rewrite = None
    if args.rewrite:
        if not args.model:
            raise SystemExit("--rewrite needs --model")
        from dokimi.llm import llm_call

        def rewrite(text: str) -> str:
            return llm_call(ts.REWRITE_PROMPT + text, model=args.model, provider=args.provider)

    try:
        queries = ts.build_trigger_set(sources, catalog, rewrite=rewrite)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    # An @field rule that resolves for no question in its source is a typo in the
    # path, not a set of negatives; refuse it rather than write "nothing loads".
    start = 0
    for (qs, rule), spec in zip(sources, args.questions, strict=True):
        chunk = queries[start:start + len(qs)]
        start += len(qs)
        if rule.startswith("@") and chunk and not any(q.labelled for q in chunk):
            raise SystemExit(f"{spec}: {rule} resolves for none of {len(chunk)} questions")
    summary = ts.lint_summary(queries)
    if summary["label_unresolved"]:
        print(f"warning: {summary['label_unresolved']} questions have an unresolved @field label; "
              "they are left unlabelled and dropped from --format reach/skill-creator", file=sys.stderr)
    if summary["unknown_expected"] or (
        args.strict and (summary["names_skill"] or summary["exam_framing"] or summary["label_unresolved"])
    ):
        # Refuse before writing, so a pipeline that only checks for the file
        # cannot pick up a set with a mistyped label.
        print(json.dumps(summary, indent=2))
        raise SystemExit(1)
    if args.format == "questions":
        from dokimi.questions.artifact import save_questions_artifact

        save_questions_artifact(ts.to_questions(queries), args.out)
    else:
        # External tools' formats: no schema of ours to hold them to.
        payload = (ts.to_reach(queries, catalog_id=str(catalog_root)) if args.format == "reach"
                   else ts.to_skill_creator(queries, args.target))
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    multi = sum(len(q.acceptable) > 1 for q in ts.labelled(queries))
    if args.format == "reach" and multi:
        print(f"note: {multi} queries list several acceptable skills; skill-reach credits only "
              "the first (the rest become neutral acceptable_skills), while the skill-catalog: "
              "arm credits any of them")
    n_out = len(queries) if args.format == "questions" else len(ts.labelled(queries))
    print(f"wrote {args.out} ({args.format}, {n_out} queries, catalog of {len(catalog)})")


def register(sub, positive_int) -> None:
    """Add the `catalog` subcommand group."""

    catalog_p = sub.add_parser(
        "catalog",
        help="Scan an external skill/task catalog (e.g. a local uxlfoundation/skills or intel/skills checkout)",
    )
    catalog_sub = catalog_p.add_subparsers(dest="catalog_cmd", required=True)

    scan_p = catalog_sub.add_parser(
        "scan", help="Report which skills/tasks under --root load cleanly"
    )
    scan_p.add_argument("--root", required=True, help="Local checkout to scan")
    scan_p.add_argument(
        "--kind", choices=["skills", "tasks", "all"], default="all",
        help="Restrict the scan to skills, tasks, or both (default: all)",
    )
    scan_p.add_argument(
        "--tasks-subdir", default="evaluation/harbor/tasks",
        help="Task directory relative to --root (default: evaluation/harbor/tasks, "
             "the uxlfoundation/skills convention)",
    )
    scan_p.set_defaults(func=cmd_catalog_scan)

    ts_p = catalog_sub.add_parser(
        "trigger-set",
        help="Build a skill-selection query set (labels + leakage/framing lint) from question sets",
    )
    ts_p.add_argument("--catalog", required=True, help="Directory of the skills the agent will see")
    ts_p.add_argument(
        "--questions", action="append", required=True, metavar="PATH=LABEL",
        help="Question file and its label: a skill name, 'none' (no skill should load), or "
             "'@field.path' read per question (e.g. @metadata.skill_under_test). Repeatable.",
    )
    ts_p.add_argument("--format", choices=["reach", "skill-creator", "questions"], default="reach",
                      help="reach: google/skill-reach QuerySet; skill-creator: one target's "
                           "eval set; questions: a questions artifact for the skill-catalog: arm")
    ts_p.add_argument("--target", help="Target skill for --format skill-creator")
    ts_p.add_argument("--rewrite", action="store_true",
                      help="Rewrite each question into first-person user voice (model sees only the question)")
    ts_p.add_argument("--model", help="Model for --rewrite")
    ts_p.add_argument("--provider", default="openai", help="Provider for --rewrite (default: openai)")
    ts_p.add_argument("--strict", action="store_true",
                      help="Exit 1 if any query names a skill, keeps exam framing, or has an unresolved @field label")
    ts_p.add_argument("--out", required=True, help="Output JSON path")
    ts_p.set_defaults(func=cmd_catalog_trigger_set)

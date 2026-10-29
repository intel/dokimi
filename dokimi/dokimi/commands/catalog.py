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
import json
import sys
from pathlib import Path
from dokimi.exit_codes import usage_exit


def _skill_row(skill, review) -> dict:
    """One skill of the scan report. ``state`` is ``error`` for a load error or an error finding,
    ``warn`` for a warning finding only, else ``ok``. A skill that did not load has a review only
    when its frontmatter is broken YAML, and that review's finding is its only finding."""
    if not skill.loaded:
        state, findings = "error", (review.findings if review else [])
    else:
        state, findings = review.state, review.findings
    return {
        "name": skill.name,
        "manifest_status": None if skill.status is None else str(skill.status),
        "path": str(skill.path) if skill.path else None,
        "state": state,
        "error": skill.error,
        "disable_model_invocation": bool(review and review.disable_model_invocation),
        "content_sha256": review.content_sha256 if review else None,
        "duplicate_of": review.duplicate_of if review else None,
        "findings": [{"level": f.level, "code": f.code, "message": f.message} for f in findings],
    }


def _print_skill_row(row: dict) -> None:
    if row["error"] is not None:
        state = f"ERROR: {row['error']}"
    elif row["state"] == "ok":
        state = "ok"
    else:
        worst = next(f for f in row["findings"] if f["level"] == row["state"])
        state = f"{'ERROR' if row['state'] == 'error' else 'warn'}: {worst['message']}"
    print(f"{row['name']}\t{row['manifest_status'] or '-'}\t{row['path'] or '-'}\t{state}")
    for f in row["findings"]:
        print(f"  {f['level']} {f['code']}: {f['message']}")
    if row["disable_model_invocation"]:
        print("  note disable-model-invocation: true (the model is never offered this skill)")


def _scan_skills(args: argparse.Namespace, root: Path, report: dict, as_json: bool) -> bool:
    """Review the skills under *root* into ``report``; True when the scan must exit 1."""
    from dokimi.external import read_skill_manifest
    from dokimi.external.validate import review_skills

    try:
        skills = read_skill_manifest(root)
    except ValueError as exc:
        usage_exit(f"skills.yaml: {exc}")
    reviews = review_skills(skills)
    counts = {"ok": 0, "warn": 0, "error": 0}
    failed = False
    for skill, review in zip(skills, reviews, strict=True):
        row = _skill_row(skill, review)
        counts[row["state"]] += 1
        report["skills"].append(row)
        # A load error always fails; a spec error fails only with --strict.
        if not skill.loaded or (args.strict and row["state"] == "error"):
            failed = True
    if not as_json:
        print(
            f"# skills ({len(skills)} declared under {root}; "
            f"{counts['ok']} ok, {counts['warn']} warn, {counts['error']} error)"
        )
        for row in report["skills"]:
            _print_skill_row(row)
        print()
    return failed


def _task_row(task) -> dict:
    return {
        "name": task.name,
        "manifest_status": None if task.status is None else str(task.status),
        "track": task.track,
        "path": str(task.path) if task.path else None,
        "state": "ok" if task.loaded else "skipped" if task.skipped else "error",
        "reason": task.reason,
    }


def _scan_tasks(args: argparse.Namespace, root: Path, report: dict, as_json: bool) -> bool:
    """List the tasks under *root* into ``report``; True when the scan must exit 1."""
    from dokimi.external import read_task_manifest

    try:
        tasks = read_task_manifest(root, tasks_subdir=args.tasks_subdir)
    except ValueError as exc:
        usage_exit(str(exc))
    if not as_json:
        print(f"# tasks ({len(tasks)} declared under {root})")
    failed = False
    for task in tasks:
        row = _task_row(task)
        report["tasks"].append(row)
        failed = failed or row["state"] == "error"
        if not as_json:
            state = {"ok": "ok", "skipped": f"skipped: {task.reason}"}.get(
                row["state"], f"ERROR: {task.reason}"
            )
            print(f"{task.name}\t{task.status or '-'}\t{task.track or '-'}\t{state}")
    return failed


def cmd_catalog_scan(args: argparse.Namespace) -> None:
    root = Path(args.root)
    if not root.is_dir():
        usage_exit(f"--root is not a directory: {root}")

    as_json = args.format == "json"
    report: dict = {"root": str(root), "skills": [], "tasks": []}
    any_failure = False
    if args.kind in ("skills", "all"):
        any_failure = _scan_skills(args, root, report, as_json)
    if args.kind in ("tasks", "all"):
        any_failure = _scan_tasks(args, root, report, as_json) or any_failure
    if as_json:
        print(json.dumps(report, indent=2))
    if any_failure:
        raise SystemExit(1)


def _write_trigger_set(args: argparse.Namespace, queries, catalog_root: Path) -> None:
    """Write the query set to ``--out`` in ``--format``, or exit 1 naming ``--out`` when it cannot be written."""
    import json

    from dokimi.artifacts import ArtifactValidationError
    from dokimi.external import trigger_set as ts

    try:
        if args.format == "questions":
            from dokimi.questions.artifact import save_questions_artifact

            save_questions_artifact(ts.to_questions(queries), args.out)
        else:
            # External tools' formats: no schema of ours to hold them to.
            payload = (ts.to_reach(queries, catalog_id=str(catalog_root)) if args.format == "reach"
                       else ts.to_skill_creator(queries, args.target))
            Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except ArtifactValidationError as exc:  # the path was writable; the payload failed the schema
        usage_exit(f"Error: questions set rejected: {exc}")
    except (OSError, ValueError) as exc:  # unwritable --out: a directory, a file as parent, no permission, empty path (pathlib ValueError)
        usage_exit(f"Error: cannot write --out {args.out}: {exc}")


def _discover_catalog_skills(catalog_root: Path) -> list:
    from dokimi.skills import discover_skills

    try:
        return discover_skills(catalog_root)
    except (OSError, ValueError, RecursionError) as exc:  # unreadable, non-UTF-8, bad frontmatter or nesting too deep
        usage_exit(f"--catalog {catalog_root}: {exc}")


def cmd_catalog_trigger_set(args: argparse.Namespace) -> None:
    import json

    from dokimi.external import trigger_set as ts

    catalog_root = Path(args.catalog)
    if not catalog_root.is_dir():
        usage_exit(f"--catalog is not a directory: {catalog_root}")
    catalog = _discover_catalog_skills(catalog_root)
    # Checked before any --rewrite call, so a bad invocation costs no LLM calls.
    if args.format == "skill-creator":
        if not args.target:
            usage_exit("--format skill-creator needs --target <skill>")
        if args.target not in {s.name for s in catalog}:
            usage_exit(f"--target {args.target!r} is not a skill in --catalog "
                       f"(have: {', '.join(sorted(s.name for s in catalog))})")
    sources = _question_sources(args.questions or [])
    sources += _trigger_set_sources(args.trigger_set or [], catalog)
    if not sources:
        usage_exit("give --questions PATH=LABEL or --trigger-set PATH[=TARGET]")
    specs = list(args.questions or []) + list(args.trigger_set or [])

    rewrite = None
    if args.rewrite:
        if not args.model:
            usage_exit("--rewrite needs --model")
        from dokimi.llm import llm_call

        def rewrite(text: str) -> str:
            return llm_call(ts.REWRITE_PROMPT + text, model=args.model, provider=args.provider)

    try:
        queries = ts.build_trigger_set(sources, catalog, rewrite=rewrite)
    except ValueError as exc:
        usage_exit(str(exc))
    # An @field rule that resolves for no question in its source is a typo in the
    # path, not a set of negatives; refuse it rather than write "nothing loads".
    start = 0
    for (qs, rule), spec in zip(sources, specs, strict=True):
        chunk = queries[start:start + len(qs)]
        start += len(qs)
        if rule.startswith("@") and chunk and not any(q.labelled for q in chunk):
            usage_exit(f"{spec}: {rule} resolves for none of {len(chunk)} questions")
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
    _write_trigger_set(args, queries, catalog_root)

    print(json.dumps(summary, indent=2))
    multi = sum(len(q.acceptable) > 1 for q in ts.labelled(queries))
    if args.format == "reach" and multi:
        print(f"note: {multi} queries list several acceptable skills; skill-reach credits only "
              "the first (the rest become neutral acceptable_skills), while the skill-catalog: "
              "arm credits any of them")
    n_out = len(queries) if args.format == "questions" else len(ts.labelled(queries))
    print(f"wrote {args.out} ({args.format}, {n_out} queries, catalog of {len(catalog)})")


def cmd_catalog_formats(args: argparse.Namespace) -> None:
    import json

    from dokimi.skills import formats

    root = Path(args.root)
    if not root.is_dir():
        usage_exit(f"--root is not a directory: {root}")
    packages = formats.detect_skill_packages(root)
    summary = formats.summarize(packages)
    if args.json:
        print(json.dumps({"root": str(root), "summary": summary,
                          "packages": [p.to_dict(root) for p in packages]}, indent=2))
        return
    for p in packages:
        problems = "; ".join(p.problems) or "ok"
        print(f"{p.format}\t{p.name or '-'}\t{p.selected_by}\t{p.host or '-'}\t"
              f"{p.path.relative_to(root).as_posix()}\t{problems}")
    print()
    for fmt, row in summary.items():
        print(f"# {fmt}: {row['n']} ({row['loadable']} loadable as skills, "
              f"{row['with_problems']} with problems); selected by {row['selected_by']}")


def _question_sources(specs: list) -> list:
    """Each ``--questions PATH=LABEL`` as a (questions, rule) source."""
    from dokimi.utils import read_json_input

    sources = []
    for spec in specs:
        path, _, rule = spec.rpartition("=")  # labels never contain '=', paths may
        if not path or not rule:
            usage_exit(f"--questions needs PATH=LABEL (a skill name, 'none' or '@field'): {spec}")
        data = read_json_input(path, flag="--questions")
        questions = data.get("questions", data) if isinstance(data, dict) else data
        if not isinstance(questions, list) or not all(isinstance(q, dict) for q in questions):
            usage_exit(f"{path}: expected a list of question objects")
        sources.append((questions, rule))
    return sources


def _trigger_set_sources(specs: list, catalog) -> list:
    """Each ``--trigger-set PATH[=TARGET]`` read back as a (questions, rule) source.

    The set is read into the canonical trigger model and handed on as questions
    labelled by their own ``expected_skill``, so it is linted (and rewritten) like
    any other source. TARGET names the skill a skill-creator set was written for.
    """
    from dokimi.external import trigger_set as ts
    from dokimi.utils import read_json_input

    names = {s.name for s in catalog}
    out = []
    for spec in specs:
        path, target = spec, None
        if not Path(spec).exists() and "=" in spec:
            path, _, target = spec.rpartition("=")
        data = read_json_input(path, flag="--trigger-set")
        try:
            fmt, queries = ts.read_trigger_set(data, target=target)
        except ValueError as exc:
            usage_exit(f"{path}: {exc}")
        if fmt == ts.FORMAT_SKILL_CREATOR:
            if target not in names:
                usage_exit(f"{spec}: skill-creator target {target!r} is not a skill in "
                           f"--catalog (have: {', '.join(sorted(names))})")
            if len(names) > 1:
                print(f"note: {path}: skill-creator negatives are read as 'no skill loads'; in a "
                      f"catalog of {len(names)} a rival load counts as a false trigger", file=sys.stderr)
        elif target:
            usage_exit(f"{spec}: =TARGET applies to skill-creator sets only, this is {fmt}")
        print(f"read {path} as {fmt} ({len(queries)} queries)", file=sys.stderr)
        rows = ts.to_questions(queries)["questions"]
        for row, q in zip(rows, queries, strict=True):
            if q.reach:
                row[ts.REACH_FIELDS] = q.reach
        out.append((rows, "@expected_skill"))
    return out


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
        "--strict",
        action="store_true",
        help="Exit 1 on a skill spec error (name not the directory name or not kebab-case, "
        "description over 1024 characters) as well as on a skill that does not load",
    )
    scan_p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="text: one line per entry; json: one object with a row per skill and task (default: text)",
    )
    scan_p.add_argument(
        "--tasks-subdir", default="evaluation/harbor/tasks",
        help="Task directory relative to --root (default: evaluation/harbor/tasks, "
             "the uxlfoundation/skills convention)",
    )
    scan_p.set_defaults(func=cmd_catalog_scan)

    fm_p = catalog_sub.add_parser(
        "formats",
        help="List the skill/rule packages under --root by ecosystem (Agent Skills, Cursor "
             "rules, Copilot instructions, Gemini extensions, Claude plugins)",
    )
    fm_p.add_argument("--root", required=True, help="Local checkout to scan")
    fm_p.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    fm_p.set_defaults(func=cmd_catalog_formats)

    ts_p = catalog_sub.add_parser(
        "trigger-set",
        help="Build a skill-selection query set (labels + leakage/framing lint) from question sets",
    )
    ts_p.add_argument("--catalog", required=True, help="Directory of the skills the agent will see")
    ts_p.add_argument(
        "--questions", action="append", metavar="PATH=LABEL",
        help="Question file and its label: a skill name, 'none' (no skill should load), or "
             "'@field.path' read per question (e.g. @metadata.skill_under_test). Repeatable.",
    )
    ts_p.add_argument(
        "--trigger-set", action="append", dest="trigger_set", metavar="PATH[=TARGET]",
        help="An existing trigger set to read back: a skill-reach QuerySet, a skill-creator / "
             "agentskills.io [{query, should_trigger}] set (=TARGET names its skill), or questions "
             "with expected_skill. Repeatable; combines with --questions.",
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

    from dokimi.commands import catalog_probe

    catalog_probe.register(catalog_sub, positive_int)

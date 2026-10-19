# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo subcommand group: repositories as evaluation subjects (repo_manifest.v1)."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


def _normalize_whitespace(text: str, max_len: int = 100) -> str:
    """Replace newlines and carriage returns with spaces, then truncate."""
    normalized = re.sub(r'[\r\n]+', ' ', text).strip()
    return normalized[:max_len]


def cmd_repo_validate(args: argparse.Namespace) -> None:
    """Validate a repository's repo-eval.yaml and its tasks against one snapshot."""
    from dokimi.repos import ManifestError, RepoSource, SourceError, check_repo, open_source
    from dokimi.repos.source import base_opener

    try:
        source = RepoSource.parse(args.source)
        tree = open_source(source, _cache_dir(args))
        manifest = _load_manifest(tree, args)
    except (SourceError, ManifestError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    open_base = base_opener(source, tree)
    report = check_repo(tree, manifest, open_base)
    if open_base is None:
        report.notes.append("task bases were not checked: the source has no git history")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        mark = "✓" if report.ok else "✗"
        families = ", ".join(f"{k} {v}" for k, v in report.to_dict()["tasks"]["by_family"].items())
        print(f"{mark} {manifest.id}: {manifest.path} in {report.tree}")
        if manifest.runnable:
            print(f"  tasks: {len(report.tasks)} ({families or 'none valid'})")
        else:
            print("  tasks: none (guidance-only manifest)")
        line = f"  guidance files: {len(report.guidance_files)} at the eval tree"
        if report.guidance_by_base:
            counts = report.guidance_by_base.values()
            line += f"; {min(counts)}–{max(counts)} across {len(counts)} task bases"
        print(line)
        for warning in report.warnings:
            print(f"  warning: {warning}")
        for note in report.notes:
            print(f"  note: {note}")
        for error in report.errors:
            print(f"  error: {error}", file=sys.stderr)
    if not report.ok:
        sys.exit(1)


def cmd_repo_scan(args: argparse.Namespace) -> None:
    """L0: check every mechanically checkable claim in the guidance against the same tree."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos import ManifestError, RepoSource, SourceError, open_source
    from dokimi.repos.l0 import scan_repo

    try:
        tree = open_source(RepoSource.parse(args.source), _cache_dir(args))
        manifest = _load_manifest(tree, args, guidance_default=True)
        static = json.loads(Path(args.static).read_text(encoding="utf-8")) if args.static else None
    except (SourceError, ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    payload = scan_repo(tree, manifest, static)
    if args.out:
        try:
            save_artifact("repo_readiness", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(1)
    static_info = payload["static"]
    static_errors = static_info["errors"] if static_info is not None else []
    for problem in static_errors:  # on stderr in --json mode too
        print(f"✗ --static output not merged: {problem}", file=sys.stderr)
    if args.json:
        print(json.dumps(stamp("repo_readiness", dict(payload)), indent=2))
    else:
        c = payload["counts"]
        detected = " (detected)" if payload["adapters_detected"] else ""
        print(f"{manifest.id}: {len(payload['files'])} guidance files in {payload['repo']['tree']}; "
              f"adapters: {', '.join(payload['adapters']) or 'none'}{detected}")
        print(f"  claims: {c['total']} (ok {c['ok']}, unresolved {c['unresolved']}, fail {c['fail']})")
        for warning in payload["warnings"]:
            print(f"  warning: {warning}")
        if static_info is not None and not static_errors:
            r = payload["by_source"]["repository"]
            print(f"  repository static: {r['total']} claims, {r['fail']} fail; "
                  f"{static_info['superseded']} framework claims replaced")
        for claim in payload["claims"]:
            if claim["status"].split("_", 1)[0] == "fail":
                print(f"  FAIL {claim['file']}:{claim['line']} [{claim.get('kind')}] "
                      f"{str(claim.get('claim'))[:90]} -- {claim.get('why')}")
        if args.out:
            print(f"wrote {args.out}")
    if static_errors:
        sys.exit(1)
    if args.fail_on_fail and (payload["counts"]["fail"] or payload["incomplete"]):
        sys.exit(1)


def cmd_repo_inspect(args: argparse.Namespace) -> None:
    """Static inspection: instruction files, check surfaces, launch risks, file inventory."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos import RepoSource, SourceError, open_source
    from dokimi.repos.environment import inspect_repo

    try:
        tree = open_source(RepoSource.parse(args.source), _cache_dir(args))
    except SourceError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    payload = inspect_repo(tree, large=args.large_bytes)
    if args.out:
        try:
            save_artifact("repo_environment", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(1)
    if args.json:
        print(json.dumps(stamp("repo_environment", dict(payload)), indent=2))
    else:
        c = payload["counts"]
        roots = sum(i["scope"] == "root" for i in payload["instructions"])
        print(f"{payload['repo']['tree']}: {len(payload['instructions'])} instruction files ({roots} root), "
              f"{len(payload['checks'])} check surfaces, risk {payload['risk']}")
        for kind in ("build", "test", "lint", "typecheck"):
            found = [x for x in payload["checks"] if x["kind"] == kind]
            how = ", ".join(sorted({f"{x['ecosystem']} ({x['basis']})" for x in found})) or "none found"
            print(f"  {kind}: {how}")
        print(f"  findings: {c['error']} error, {c['warning']} warning, {c['info']} info")
        for f in payload["findings"]:
            if f["severity"] != "info":
                where = f" {f['path']}" if f["path"] else ""
                print(f"  {f['severity'].upper()} {f['rule']}{where} -- {_normalize_whitespace(f['why'])}")
        print("  stages: " + ", ".join(f"{s['stage']} {s['status']}" for s in payload["stages"]))
        if args.out:
            print(f"wrote {args.out}")
    if args.fail_on_error and payload["counts"]["error"]:
        sys.exit(1)


def cmd_repo_fetch(args: argparse.Namespace) -> None:
    """Clone (once) and fetch a pinned remote source into the cache; print the SHA."""
    from dokimi.repos import RepoSource, SourceError, open_source
    from dokimi.repos.source import GitTree

    try:
        source = RepoSource.parse(args.source)
        if source.rev is None:
            raise SourceError("fetch needs a pinned source: <url-or-path>@<rev>")
        tree = open_source(source, _cache_dir(args))
    except SourceError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    assert isinstance(tree, GitTree)
    print(f"{tree.sha}\t{tree.git_dir}")


def cmd_repo_reach(args: argparse.Namespace) -> None:
    """L1 reach probe: which guidance files one harness/version/model loads."""
    from dokimi.repos import ManifestError, RepoSource, SourceError, open_source
    from dokimi.repos.harness_profile import ProfileError, load_profile
    from dokimi.repos.reach import (
        ReachError,
        format_matrix,
        planned,
        probe_files,
        run_reach,
        save_reach,
        scenarios,
    )

    kinds = tuple(k.strip() for k in args.scenarios.split(",") if k.strip())
    only = tuple(args.only or ())
    try:
        # A typo would otherwise drop a scenario kind silently and still spend the budget.
        unknown_kinds = sorted(set(kinds) - {"root", "cwd", "read"})
        if unknown_kinds:
            raise ReachError(f"unknown --scenarios kind(s) {', '.join(unknown_kinds)} (known: root, cwd, read)")
        if not math.isfinite(args.budget) or args.budget <= 0:
            raise ReachError(f"--budget must be > 0, got {args.budget:g}")
        profile = load_profile(args.harness)
        tree = open_source(RepoSource.parse(args.source), _cache_dir(args))
        manifest = _load_manifest(tree, args, guidance_default=True)
        files = probe_files(tree, manifest, profile)
        available = [s.id for s in scenarios(files, kinds)]
        missing = [o for o in only if o not in available]
        if missing:
            raise ReachError(f"--only {', '.join(missing)} matches no scenario (have: {', '.join(available)})")
        if args.dry_run:
            runs = planned(files, profile, args.model, kinds, only, args.bin, args.max_turns, args.budget)
            print(json.dumps({"files": files, "runs": runs}, indent=2))
            return
        planned_count = len(scenarios(files, kinds, only))
        if not planned_count:
            raise ReachError("no scenario selected (check --scenarios and --only)")
        print(f"{len(files)} guidance files, {planned_count} scenarios, "
              f"at most ${args.budget:g} each", file=sys.stderr)
        out_dir = Path(args.out)
        payload = run_reach(
            tree=tree, manifest=manifest, profile=profile, model=args.model, out_dir=out_dir,
            bin_path=args.bin, work_dir=Path(args.work_dir) if args.work_dir else None, kinds=kinds,
            only=only, salt=args.salt, max_turns=args.max_turns, budget_usd=args.budget,
            timeout_s=args.timeout, keep=args.keep,
        )
    except (SourceError, ManifestError, ProfileError, ReachError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    path = save_reach(payload, out_dir)
    print(format_matrix(payload))
    print(f"wrote {path}")
    if any(s["error"] for s in payload["scenarios"]):
        sys.exit(1)


# The guidance `scan` and `reach` check in a repository that has no manifest yet.
DEFAULT_GUIDANCE_GLOBS = (
    "**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md", ".github/instructions/*.md",
)


def _load_manifest(tree, args: argparse.Namespace, *, guidance_default: bool = False):
    """The manifest from the tree, or from --manifest-file for a repository not aligned yet.

    With ``guidance_default`` (the commands that only read guidance), a tree with neither
    falls back to a guidance-only manifest over :data:`DEFAULT_GUIDANCE_GLOBS`, said on
    stderr, so a first scan needs no manifest written by hand.
    """
    import yaml

    from dokimi.repos import ManifestError, load_manifest, parse_manifest

    from dokimi.repos import DEFAULT_MANIFEST_PATH

    # Only a path the caller did not name falls back: a mistyped --manifest stays an error.
    path = args.manifest or DEFAULT_MANIFEST_PATH
    if not args.manifest_file:
        if guidance_default and args.manifest is None and not tree.is_file(path):
            print(f"note: no {path} in {tree.label}; checking the default guidance "
                  f"globs {', '.join(DEFAULT_GUIDANCE_GLOBS)} (pass --manifest-file to choose)",
                  file=sys.stderr)
            return parse_manifest({
                "schema": "repo_manifest.v1", "id": _manifest_id(tree.label),
                "guidance": {"globs": list(DEFAULT_GUIDANCE_GLOBS)},
            }, "<default>")
        return load_manifest(tree, path)
    try:
        raw = yaml.safe_load(Path(args.manifest_file).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(args.manifest_file, [str(exc)]) from exc
    return parse_manifest(raw, path)


def _manifest_id(label: str) -> str:
    """A manifest id (``^[a-z0-9][a-z0-9._-]*$``) from a tree label such as a path or url@rev."""
    import re

    name = re.split(r"[/:]", label.rstrip("/"))[-1].split("@")[0].removesuffix(".git").lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-._")
    return name or "repo"


def _cache_dir(args: argparse.Namespace) -> Path | None:
    return Path(args.cache_dir) if args.cache_dir else None


def register(sub, positive_int) -> None:
    """Add the `repo` subcommand group."""
    repo_p = sub.add_parser(
        "repo",
        help="Repositories as evaluation subjects: validate repo-eval.yaml, L0 scan, fetch sources, reach probe, "
        "task self-check, agent runs and their report",
    )
    repo_sub = repo_p.add_subparsers(dest="repo_cmd", required=True)
    source_help = (
        "Repository source: a local directory (read as it is; in a git working tree, files git ignores are "
        "left out), <dir>@<rev>, or "
        "<https-or-ssh-url>@<rev> (cloned bare into the cache)"
    )
    cache_help = "Clone cache directory (default: $DOKIMI_REPO_CACHE or ~/.cache/dokimi/repos)"

    validate_p = repo_sub.add_parser(
        "validate", help="Validate the manifest and every task.json against one snapshot"
    )
    validate_p.add_argument("source", help=source_help)
    validate_p.add_argument(
        "--manifest",
        default=".agent-eval/repo-eval.yaml",
        help="Manifest path relative to the repository root (default: .agent-eval/repo-eval.yaml)",
    )
    validate_p.add_argument("--manifest-file", default=None, dest="manifest_file",
                            help="Read the manifest from this local file instead of from the tree, as if it were "
                            "at --manifest (for a repository that has not added it yet)")
    validate_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    validate_p.add_argument("--json", action="store_true", help="Print the full report as JSON")
    validate_p.set_defaults(func=cmd_repo_validate)

    scan_p = repo_sub.add_parser(
        "scan",
        help="L0: check the links, paths, identifiers, commands and build labels the guidance names "
        "against the same tree (repo_readiness.v1). No LLM call.",
    )
    scan_p.add_argument("source", help=source_help)
    scan_p.add_argument("--manifest", default=None,
                        help="Manifest path relative to the repository root (default: "
                        ".agent-eval/repo-eval.yaml). With this flag, --manifest-file and the default path "
                        "all absent, the default guidance globs (AGENTS.md, CLAUDE.md, Copilot instructions) "
                        "are checked")
    scan_p.add_argument("--manifest-file", default=None, dest="manifest_file",
                        help="Read the manifest from this local file instead of from the tree, as if it were "
                        "at --manifest (for a repository that has not added it yet)")
    scan_p.add_argument("--static", default=None,
                        help="repo_static.v1 document the repository's `static` entry point wrote; merged into "
                        "the report, its claims replacing the framework's on the same file, line and text. "
                        "An invalid document is reported and makes the command exit 1")
    scan_p.add_argument("--out", default=None, help="Write the repo_readiness.v1 report to this file")
    scan_p.add_argument("--json", action="store_true", help="Print the full report as JSON")
    scan_p.add_argument("--fail-on-fail", action="store_true", dest="fail_on_fail",
                        help="Exit 1 when any claim fails, an also_scan file is missing, a guidance file "
                        "cannot be read, or no guidance file matched (for a CI gate)")
    scan_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    scan_p.set_defaults(func=cmd_repo_scan)

    inspect_p = repo_sub.add_parser(
        "inspect",
        help="Static inspection of what the tree offers an agent: instruction files and their scope, ways to "
        "build and check a change, launch risks (hooks, MCP, risky scripts), file inventory "
        "(repo_environment.v1). No manifest, no LLM call, no repository build/test/script commands run.",
    )
    inspect_p.add_argument("source", help=source_help)
    inspect_p.add_argument("--large-bytes", type=positive_int, default=100_000, dest="large_bytes",
                           help="A file above this many bytes is reported as large (default: 100000, ~30k tokens)")
    inspect_p.add_argument("--out", default=None, help="Write the repo_environment.v1 report to this file")
    inspect_p.add_argument("--json", action="store_true", help="Print the full report as JSON")
    inspect_p.add_argument("--fail-on-error", action="store_true", dest="fail_on_error",
                           help="Exit 1 when any finding has severity error (for a CI gate)")
    inspect_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    inspect_p.set_defaults(func=cmd_repo_inspect)

    fetch_p = repo_sub.add_parser("fetch", help="Clone/fetch a pinned source and print its commit SHA")
    fetch_p.add_argument("source", help=source_help)
    fetch_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    fetch_p.set_defaults(func=cmd_repo_fetch)

    reach_p = repo_sub.add_parser(
        "reach",
        help="L1 reach probe: run a harness on a token-seeded workspace and report which "
        "guidance files it loads (guidance_reach.v1). Costs one cheap-model call per scenario.",
    )
    reach_p.add_argument("source", help=source_help)
    reach_p.add_argument("--manifest", default=None,
                         help="Manifest path relative to the repository root (default: "
                         ".agent-eval/repo-eval.yaml). With this flag, --manifest-file and the default path "
                         "all absent, the default guidance globs (AGENTS.md, CLAUDE.md, Copilot instructions) "
                         "are checked")
    reach_p.add_argument("--manifest-file", default=None, dest="manifest_file",
                         help="Read the manifest from this local file instead of from the tree, as if it were "
                         "at --manifest (for a repository that has not added it yet)")
    reach_p.add_argument("--harness", default="claude-code",
                         help="Built-in profile name or a harness_profile.v1 YAML path (default: claude-code)")
    reach_p.add_argument("--model", required=True, help="Model string passed to the harness as it is")
    reach_p.add_argument("--bin", default=None, help="Harness executable (default: the profile's `bin`)")
    reach_p.add_argument("--out", default="reach-out", help="Output directory: reach.json and traces/")
    reach_p.add_argument("--scenarios", default="root,cwd,read",
                         help="Comma list of scenario kinds: root, cwd, read (default: all)")
    reach_p.add_argument("--only", action="append", default=None, metavar="SCENARIO",
                         help="Run only this scenario id (for example read=cpp); repeatable")
    reach_p.add_argument("--work-dir", default=None, dest="work_dir",
                         help="Parent of the temporary workspace (default: the system temp dir). "
                         "No ancestor may hold guidance files.")
    reach_p.add_argument("--salt", default=None, help="Token salt (default: random per run)")
    reach_p.add_argument("--max-turns", type=positive_int, default=4, dest="max_turns")
    reach_p.add_argument("--budget", type=float, default=0.10, help="Per-scenario USD cap (default: 0.10)")
    reach_p.add_argument("--timeout", type=positive_int, default=300, help="Per-scenario seconds")
    reach_p.add_argument("--keep", action="store_true", help="Keep the temporary workspace")
    reach_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Print the files and the exact argv of every scenario; run nothing")
    reach_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    reach_p.set_defaults(func=cmd_repo_reach)

    from . import repo_run

    repo_run.register(repo_sub, positive_int, source_help, cache_help)

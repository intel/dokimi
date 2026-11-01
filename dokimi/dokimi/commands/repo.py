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
from typing import Any

from dokimi.exit_codes import EXIT_USAGE


def _normalize_whitespace(text: str, max_len: int | None = 100) -> str:
    """One line: whitespace runs become one space, control characters are escaped, then truncated."""
    flat = re.sub(r"\s+", " ", text).strip()
    printable = "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in flat)
    return printable[:max_len]


def _write_json(path: str, payload: dict) -> None:
    """Write a --out report as JSON, or exit 2 with `cannot write`."""
    try:
        Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"✗ cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _source_tree(args: argparse.Namespace) -> tuple:
    """The source (source, tree): a malformed string, unpinned remote or pinned local path that is not the repository root is a usage error (2); a clone, fetch or pin resolution failure is 1."""
    from dokimi.repos import RepoSource, SourceError, open_source
    from dokimi.repos.source import SourceArgumentError

    try:
        source = RepoSource.parse(args.source)
    except SourceError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    # 2 = malformed source string, unpinned remote, pinned local path inside a repository, manifest/profile errors; 1 = clone/fetch/resolve of a pin
    # (incl. pinned local non-git source, pin absent locally, remote pin missing from origin).
    try:
        return source, open_source(source, _cache_dir(args))
    except SourceArgumentError as exc:  # a pinned local path that is not the repository root
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except SourceError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as exc:
        # A cache dir that cannot be made, or git output that is not UTF-8: a failed read, not a usage error.
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


def _manifest_or_exit(tree, args: argparse.Namespace, *, guidance_default: bool = False,
                      also_usage: tuple = ()) -> Any:
    """The manifest of the tree. A bad manifest or a manifest path that leaves the tree is a usage error
    (2); a failed `git show` (a blob fetched lazily from origin) is a fetch failure (1)."""
    from dokimi.repos import ManifestError, SourceError
    from dokimi.repos.source import SourceArgumentError

    try:
        return _load_manifest(tree, args, guidance_default=guidance_default)
    except (SourceArgumentError, ManifestError, *also_usage) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except SourceError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_repo_validate(args: argparse.Namespace) -> None:
    """Validate a repository's repo-eval.yaml and its tasks against one snapshot."""
    from dokimi.repos import check_repo
    from dokimi.repos.source import base_opener

    source, tree = _source_tree(args)
    manifest = _manifest_or_exit(tree, args)
    open_base = base_opener(source, tree)
    report = check_repo(tree, manifest, open_base)
    if open_base is None:
        report.notes.append("task bases were not checked: the source has no git history")

    if args.out:
        _write_json(args.out, report.to_dict())
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
    if not report.ok or (args.fail_on_warning and report.warnings):
        sys.exit(1)


def _read_static(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # ValueError: bad JSON or UTF-8
        print(f"✗ --static {path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def cmd_repo_scan(args: argparse.Namespace) -> None:
    """L0: check every mechanically checkable claim in the guidance against the same tree."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos.l0 import scan_repo

    _, tree = _source_tree(args)
    manifest = _manifest_or_exit(tree, args, guidance_default=True, also_usage=(OSError,))
    static = _read_static(args.static) if args.static else None
    payload = scan_repo(tree, manifest, static)
    if args.out:
        try:
            save_artifact("repo_readiness", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
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
        if payload["incomplete"]:
            print(f"  incomplete: {'; '.join(payload['incomplete_reasons'])} (repo scan --fail-on-fail exits 1)")
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
    _scan_gate(payload, args.fail_on_fail)  # runs first: an invalid --static still gets the incomplete reason
    if static_errors:
        sys.exit(1)


def _scan_gate(payload: dict, fail_on_fail: bool) -> None:
    """--fail-on-fail: exit 1 on a failed claim or an incomplete scan, saying on stderr what the scan missed."""
    if not fail_on_fail or not (payload["counts"]["fail"] or payload["incomplete"]):
        return
    if payload["incomplete"]:
        print(f"✗ scan incomplete: {'; '.join(payload['incomplete_reasons'])}", file=sys.stderr)
    sys.exit(1)


def _code_index(path: str | None) -> Any:
    """The SCIP index at ``path`` (``None`` without one); exit 2 when it cannot be read."""
    if path is None:
        return None
    from dokimi.repos.code_index import load
    from dokimi.repos.errors import CodeIndexError

    try:
        return load(path)
    except (OSError, CodeIndexError) as exc:
        print(f"✗ cannot read the code index {path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _print_structure(section: dict[str, Any] | None) -> None:
    if section is None:
        return
    kept = [m for m in section["modules"] if m["kept"]]
    print(f"  structure: {section['index']['documents_in_tree']}/{section['index']['documents']} index documents "
          f"in the tree, {len(section['modules'])} modules, {len(kept)} kept" + ("; top reference fan-in:" if kept else ""))
    for m in kept[:5]:
        print(f"    {_normalize_whitespace(m['module'], max_len=None)}: fan-in {m['reference_fanin']}, "
              f"other-tree modules {m['other_tree_modules']}, ambiguity {m['ambiguity']:.2f}"
              + ("" if m["in_guidance"] else ", not in guidance"))


def cmd_repo_inspect(args: argparse.Namespace) -> None:
    """Static inspection: instruction files, check surfaces, launch risks, file inventory."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos.environment import inspect_repo

    _, tree = _source_tree(args)
    payload = inspect_repo(tree, large=args.large_bytes, index=_code_index(args.index))
    if "structure" in payload and not payload["structure"]["index"]["documents_in_tree"]:
        print(f"✗ no document of the code index {args.index} is a file of this tree", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if args.out:
        try:
            save_artifact("repo_environment", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
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
                where = f" {_normalize_whitespace(f['path'], max_len=None)}" if f["path"] else ""
                print(f"  {f['severity'].upper()} {f['rule']}{where} -- {_normalize_whitespace(f['why'])}")
        print("  stages: " + ", ".join(f"{s['stage']} {s['status']}" for s in payload["stages"]))
        _print_structure(payload.get("structure"))
        if args.out:
            print(f"wrote {args.out}")
    if args.fail_on_error and payload["counts"]["error"]:
        sys.exit(1)


def cmd_repo_fetch(args: argparse.Namespace) -> None:
    """Clone (once) and fetch a pinned remote source into the cache; print the SHA."""
    from dokimi.repos import RepoSource, SourceError, open_source
    from dokimi.repos.source import GitTree, SourceArgumentError

    try:  # the source as typed: a usage error
        source = RepoSource.parse(args.source)
        if source.rev is None:
            raise SourceError("fetch needs a pinned source: <url-or-path>@<rev>")
    except SourceError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:  # the clone and fetch: a runtime failure (network, git, unknown revision)
        tree = open_source(source, _cache_dir(args))
    except SourceArgumentError as exc:  # a pinned local path that is not the repository root
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except (SourceError, OSError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    assert isinstance(tree, GitTree)
    if args.json:
        print(json.dumps({"sha": tree.sha, "git_dir": str(tree.git_dir)}, indent=2))
    else:
        print(f"{tree.sha}\t{tree.git_dir}")


def _check_reach_flags(args: argparse.Namespace, kinds: tuple[str, ...]) -> None:
    """Refuse a flag that would drop a scenario kind silently, or spend a budget that is not one."""
    from dokimi.repos.reach import ReachError

    # A typo would otherwise drop a scenario kind silently and still spend the budget.
    unknown_kinds = sorted(set(kinds) - {"root", "cwd", "read"})
    if unknown_kinds:
        raise ReachError(f"unknown --scenarios kind(s) {', '.join(unknown_kinds)} (known: root, cwd, read)")
    if not math.isfinite(args.budget) or args.budget <= 0:
        raise ReachError(f"--budget must be > 0, got {args.budget:g}")


def _tree_read_exit_code(exc: Exception) -> int:
    """Reading the manifest or the probed files: a path that leaves the tree, a bad manifest or a bad
    scenario selection is a usage error (2); a failed `git show` or ls-tree is a fetch failure (1)."""
    from dokimi.repos import SourceError
    from dokimi.repos.source import SourceArgumentError

    git_failure = isinstance(exc, SourceError) and not isinstance(exc, SourceArgumentError)
    return 1 if git_failure else EXIT_USAGE


def cmd_repo_reach(args: argparse.Namespace) -> None:
    """L1 reach probe: which guidance files one harness/version/model loads."""
    from dokimi.repos import ManifestError, RepoSource, SourceError, open_source
    from dokimi.repos.harness_profile import ProfileError, load_profile
    from dokimi.repos.source import SourceArgumentError
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
    try:  # the arguments as typed: a usage error
        _check_reach_flags(args, kinds)
        profile = load_profile(args.harness)
        source = RepoSource.parse(args.source)
    except (SourceError, ProfileError, ReachError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:  # the clone and fetch: a runtime failure
        tree = open_source(source, _cache_dir(args))
    except SourceArgumentError as exc:  # a pinned local path that is not the repository root
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except (SourceError, OSError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    try:  # the manifest and the scenarios it selects: a usage error; a failed git read is a fetch failure (1)
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
    except (SourceError, ManifestError, ReachError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(_tree_read_exit_code(exc))
    print(f"{len(files)} guidance files, {planned_count} scenarios, "
          f"at most ${args.budget:g} each", file=sys.stderr)
    out_dir = Path(args.out)
    try:  # the harness runs: a runtime failure
        payload = run_reach(
            tree=tree, manifest=manifest, profile=profile, model=args.model, out_dir=out_dir,
            bin_path=args.bin, work_dir=Path(args.work_dir) if args.work_dir else None, kinds=kinds,
            only=only, salt=args.salt, max_turns=args.max_turns, budget_usd=args.budget,
            timeout_s=args.timeout, keep=args.keep,
        )
    except (SourceError, ProfileError, ReachError) as exc:  # ProfileError: --bin cannot run its version command
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    path = save_reach(payload, out_dir)
    print(format_matrix(payload))
    print(f"wrote {path}")
    if any(s["error"] for s in payload["scenarios"]):
        sys.exit(1)


def cmd_repo_budget(args: argparse.Namespace) -> None:
    """What the repository puts into an agent's context at start-up, lazily and on demand."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos.budget import budget
    from dokimi.repos.harness_profile import ProfileError, load_profile

    try:  # the harness profile: a usage error
        profile = load_profile(args.harness)
    except ProfileError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    _, tree = _source_tree(args)
    payload = budget(tree, profile.guidance_names, profile.import_max_depth)
    if args.out:
        try:
            save_artifact("repo_budget", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
    if args.json:
        print(json.dumps(stamp("repo_budget", dict(payload)), indent=2))
    else:
        t = payload["totals"]
        print(f"{payload['tree']}: start {t['start']:,} chars, lazy {t['lazy']:,}, "
              f"on demand {t['on demand']:,}, off {t['off']:,}")
        print("| when | kind | path | chars |")
        print("|---|---|---|---|")
        order = {"start": 0, "lazy": 1, "on demand": 2, "off": 3}
        for e in sorted(payload["entries"], key=lambda e: (order[e["when"]], -e["chars"], e["path"])):
            print(f"| {e['when']} | {e['kind']} | {e['path']} | {e['chars']:,} |")
        if payload["mcp_servers"]:
            print(f"MCP servers in .mcp.json: {', '.join(payload['mcp_servers'])} "
                  "(their tools are listed by the server at start-up, not counted here)")
        for warning in payload["warnings"]:
            print(f"warning: {warning}")
    if args.fail_on_warning and payload["warnings"]:
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
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as exc:  # ValueError: not UTF-8
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
        help="Repositories as evaluation subjects: validate repo-eval.yaml, L0 scan, fetch sources, reach probe, budget, "
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
    validate_p.add_argument("--out", default=None, help="Write the full report as JSON to this file")
    validate_p.add_argument("--json", action="store_true", help="Print the full report as JSON")
    validate_p.add_argument("--fail-on-warning", action="store_true", dest="fail_on_warning",
                            help="Exit 1 when the report has any warning (for a CI gate)")
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
                        "A file that cannot be read or is not JSON exits 2; a schema-invalid document is "
                        "reported and makes the command exit 1")
    scan_p.add_argument("--out", default=None, help="Write the repo_readiness.v1 report to this file")
    scan_p.add_argument("--json", action="store_true", help="Print the full report as JSON")
    scan_p.add_argument("--fail-on-fail", action="store_true", dest="fail_on_fail",
                        help="Exit 1 when any claim fails, an also_scan file is missing, a guidance file "
                        "or Bazel build input cannot be read, or no guidance file matched (for a CI gate)")
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
    inspect_p.add_argument("--index", default=None, metavar="FILE",
                           help="A SCIP code index of the tree (built by scip-python, scip-clang, ...): adds the "
                           "structure section (modules, reference fan-in, cross-tree bridges, name ambiguity). Read besides "
                           "the tree; exit 2 when it cannot be read or none of its documents is a file of the tree")
    inspect_p.add_argument("--out", default=None, help="Write the repo_environment.v1 report to this file")
    inspect_p.add_argument("--json", action="store_true", help="Print the full report as JSON")
    inspect_p.add_argument("--fail-on-error", action="store_true", dest="fail_on_error",
                           help="Exit 1 when any finding has severity error (for a CI gate)")
    inspect_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    inspect_p.set_defaults(func=cmd_repo_inspect)

    fetch_p = repo_sub.add_parser("fetch", help="Clone/fetch a pinned source and print its commit SHA")
    fetch_p.add_argument("source", help=source_help)
    fetch_p.add_argument("--json", action="store_true", help="Print {sha, git_dir} as JSON instead of tab-separated")
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

    budget_p = repo_sub.add_parser(
        "budget",
        help="What the repository puts into an agent's context when it starts at the root: guidance "
        "files and their imports, the skill listing, lazily loaded and on-demand files, in characters; "
        "warns on skills with no description and no body text, skills with near-duplicate descriptions (identical, or Jaccard "
        "index >= 0.5 after removing stop words when both have 3+ words), AGENTS.md files a root CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md turns off "
        "(Claude Code rules), unreadable files (non-UTF-8, dangling link), and imports past the harness's hop "
        "limit. No LLM call.",
    )
    budget_p.add_argument("source", help=source_help)
    budget_p.add_argument("--harness", default="claude-code",
                          help="Built-in profile name or a harness_profile.v1 YAML path; its guidance names "
                          "supply the file names, but the classification rules (root vs nested, off rule) are "
                          "hardcoded to Claude Code until other harnesses are measured (default: claude-code)")
    budget_p.add_argument("--out", default=None, help="Write the repo_budget.v1 inventory to this file")
    budget_p.add_argument("--json", action="store_true", help="Print the repo_budget.v1 inventory as JSON")
    budget_p.add_argument("--fail-on-warning", action="store_true", dest="fail_on_warning",
                          help="Exit 1 when any warning is printed (for a CI gate)")
    budget_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    budget_p.set_defaults(func=cmd_repo_budget)

    from . import repo_run

    repo_run.register(repo_sub, positive_int, source_help, cache_help)

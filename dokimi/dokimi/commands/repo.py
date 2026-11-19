# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo subcommand group: repositories as evaluation subjects (repo_manifest.v1)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dokimi.repos import RepoSource

from dokimi import cli_output
from dokimi.commands.repo_reach import add_reach_parser
from dokimi.commands.repo_skill_reach import add_skill_reach_parser
from dokimi.exit_codes import EXIT_USAGE
from dokimi.commands.repo_scan import (_loaded_with_default, _print_scan_text, _scan_gate, _scan_reason,
                                         _typecheck_or_exit)
from dokimi.utils import InputError


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


def _parse_source(args: argparse.Namespace) -> RepoSource:
    """The source as typed: a malformed string, an unpinned remote, a local path that is not a directory or a ``--cache-dir`` that is not a usable directory is a usage error (2); a local path the filesystem refuses to stat (no permission to traverse it) is 1."""
    from dokimi.repos import RepoSource, SourceError

    try:
        source = RepoSource.parse(args.source)
    except SourceError as exc:
        raise InputError(str(exc)) from exc
    except OSError as exc:  # Path.is_dir raises PermissionError on Python 3.11-3.13
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    _cache_dir(args)  # a bad --cache-dir is a usage error too, and must not reach a handler of the clone's failures
    return source


def _open_tree(source: RepoSource, args: argparse.Namespace):
    """The source's tree, cloned into ``--cache-dir`` when remote: a bad ``--cache-dir`` or a pinned local path that is not the repository root is a usage error (2); a clone, fetch or pin resolution failure is 1."""
    from dokimi.repos import SourceError, open_source
    from dokimi.repos.source import SourceArgumentError

    cache_dir = _cache_dir(args)  # before the handlers below: InputError is a ValueError
    try:
        return open_source(source, cache_dir)
    except SourceArgumentError as exc:
        raise InputError(str(exc)) from exc
    except (SourceError, OSError, ValueError) as exc:  # a cache dir that cannot be made, or git output that is not UTF-8
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


def _source_tree(args: argparse.Namespace) -> tuple:
    """The source (source, tree): a malformed string, unpinned remote or pinned local path that is not the repository root is a usage error (2); a clone, fetch or pin resolution failure is 1."""
    source = _parse_source(args)
    return source, _open_tree(source, args)


def _manifest_or_exit(tree, args: argparse.Namespace, *, guidance_default: bool = False,
                      also_usage: tuple = ()) -> Any:
    """The manifest of the tree. A bad manifest or a manifest path that leaves the tree is a usage error
    (2); a failed `git show` (a blob fetched lazily from origin) is a fetch failure (1)."""
    from dokimi.repos import ManifestError, SourceError
    from dokimi.repos.source import SourceArgumentError

    try:
        return _load_manifest(tree, args, guidance_default=guidance_default)
    except (SourceArgumentError, ManifestError, *also_usage) as exc:
        raise InputError(str(exc)) from exc
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
    failed = not report.ok or bool(args.fail_on_warning and report.warnings)
    if args.json:
        reason = None if not failed else (f"{len(report.errors)} error(s)" if not report.ok
                                          else f"{len(report.warnings)} warning(s) under --fail-on-warning")
        findings = [cli_output.finding("error", "validate-error", str(e), manifest.path) for e in report.errors]
        findings += [cli_output.finding("warn", "validate-warning", str(w), manifest.path) for w in report.warnings]
        cli_output.emit_leaf("repo validate", report.to_dict(), status="failed" if failed else "complete",
                             reason=reason, subjects=[{"subject": manifest.id, "verdict": None, "findings": findings}])
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
    if failed:
        sys.exit(1)


def _read_static(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # ValueError: bad JSON or UTF-8
        raise InputError(f"--static {path}: {exc}") from exc


def cmd_repo_scan(args: argparse.Namespace) -> None:
    """L0: check every mechanically checkable claim in the guidance against the same tree."""
    from dokimi.commands.repo_scan import scan_subjects
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos.l0 import scan_repo
    from dokimi.repos.l0.typecheck import TypecheckError

    _, tree = _source_tree(args)
    manifest = _manifest_or_exit(tree, args, guidance_default=True, also_usage=(OSError,))
    static = _read_static(args.static) if args.static else None
    check = _typecheck_or_exit(args)
    try:
        loaded, not_counted = _loaded_with_default(tree, manifest)
        payload = scan_repo(tree, manifest, static, typecheck=check, also=loaded, not_counted=not_counted)
    except TypecheckError as exc:
        print(f"✗ repo scan --typecheck: {exc}", file=sys.stderr)  # the error says whether the tool or the interpreter
        sys.exit(EXIT_USAGE)
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
        failed = bool(args.fail_on_fail and (payload["counts"]["fail"] or payload["incomplete"]) or static_errors)
        cli_output.emit_leaf("repo scan", stamp("repo_readiness", dict(payload)),
                             status="failed" if failed else "complete",
                             reason=_scan_reason(payload, static_errors, args.fail_on_fail) if failed else None,
                             subjects=scan_subjects(payload),
                             summary={"claims": dict(payload["counts"]), "files": len(payload["files"]),
                                      "adapters": ", ".join(payload.get("adapters") or []) or "none"})
    else:
        _print_scan_text(manifest, payload, args.out)
    _scan_gate(payload, args.fail_on_fail)  # runs first: an invalid --static still gets the incomplete reason
    if static_errors:
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
        raise InputError(f"cannot read the code index {path}: {exc}") from exc


def _print_structure(section: dict[str, Any] | None) -> None:
    if section is None:
        return
    kept = [m for m in section["modules"] if m["kept"]]
    missing = section["index"].get("tree_files_not_in_index", 0)
    print(f"  structure: {section['index']['documents_in_tree']}/{section['index']['documents']} index documents "
          f"in the tree" + (f", {missing} tree files of the indexed kinds not in it" if missing else "")
          + f", {len(section['modules'])} modules, {len(kept)} kept" + ("; top reference fan-in:" if kept else ""))
    for m in kept[:5]:
        share = "n/a" if m["ambiguity"] is None else f"{m['ambiguity']:.2f}"
        print(f"    {_normalize_whitespace(m['module'], max_len=None)}: fan-in {m['reference_fanin']}, "
              f"other-tree modules {m['other_tree_modules']}, ambiguity {share}"
              + ("" if m["in_guidance"] else ", not in guidance"))


def cmd_repo_inspect(args: argparse.Namespace) -> None:
    """Static inspection: instruction files, check surfaces, launch risks, file inventory."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos.environment import inspect_repo

    _, tree = _source_tree(args)
    payload = inspect_repo(tree, large=args.large_bytes, index=_code_index(args.index))
    if "structure" in payload and not payload["structure"]["index"]["documents_in_tree"]:
        raise InputError(f"no document of the code index {args.index} is a file of this tree")
    if args.out:
        try:
            save_artifact("repo_environment", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
    failed = bool(args.fail_on_error and payload["counts"]["error"])
    if args.json:
        cli_output.emit_leaf("repo inspect", stamp("repo_environment", dict(payload)),
                             status="failed" if failed else "complete",
                             reason=f"{payload['counts']['error']} error(s) under --fail-on-error" if failed else None)
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
    if failed:
        sys.exit(1)


def cmd_repo_fetch(args: argparse.Namespace) -> None:
    """Clone (once) and fetch a pinned remote source into the cache; print the SHA."""
    from dokimi.repos import RepoSource, SourceError
    from dokimi.repos.source import GitTree

    try:  # the source as typed: a usage error
        source = RepoSource.parse(args.source)
        if source.rev is None:
            raise SourceError("fetch needs a pinned source: <url-or-path>@<rev>")
    except SourceError as exc:
        raise InputError(str(exc)) from exc
    tree = _open_tree(source, args)
    assert isinstance(tree, GitTree)
    if args.json:
        cli_output.emit_leaf("repo fetch", {"sha": tree.sha, "git_dir": str(tree.git_dir)})
    else:
        print(f"{tree.sha}\t{tree.git_dir}")


def cmd_repo_budget(args: argparse.Namespace) -> None:
    """What the repository puts into an agent's context at start-up, lazily and on demand."""
    from dokimi.artifacts import save_artifact, stamp
    from dokimi.repos.budget import budget
    from dokimi.repos.harness_profile import ProfileError, load_profile

    try:  # the harness profile: a usage error
        profile = load_profile(args.harness)
    except ProfileError as exc:
        raise InputError(str(exc)) from exc
    _, tree = _source_tree(args)
    payload = budget(tree, profile.guidance_names, profile.import_max_depth, profile.reads_claude_rules,
                     claude_displaces_agents=profile.claude_displaces_agents,
                     follow_imports=profile.follows_imports)
    if args.out:
        try:
            save_artifact("repo_budget", payload, Path(args.out))
        except OSError as exc:
            print(f"✗ cannot write {args.out}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
    failed = bool(args.fail_on_warning and payload["warnings"])
    if args.json:
        cli_output.emit_leaf("repo budget", stamp("repo_budget", dict(payload)),
                             status="failed" if failed else "complete",
                             reason=f"{len(payload['warnings'])} warning(s) under --fail-on-warning" if failed else None)
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
    if failed:
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
        if not Path(args.manifest_file).is_file():  # a FIFO would block the read until a writer opens it (#975)
            raise OSError("not a regular file")
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
    """``--cache-dir`` as given, or ``None``; a value no clone can be cached in is a usage error (2) (#765).

    Without the flag, a non-empty ``$DOKIMI_REPO_CACHE`` is checked and returned the same way (#827); the flag wins.
    A read-only directory passes: a populated one replays without writing; an empty one fails later with exit 1.
    """
    import os

    from dokimi.commands.cache_dir import CLONE_USE, resolved_cache_dir

    env = os.environ.get("DOKIMI_REPO_CACHE")
    if not args.cache_dir and env:
        return resolved_cache_dir(env, CLONE_USE, label="$DOKIMI_REPO_CACHE")
    return resolved_cache_dir(args.cache_dir, CLONE_USE, label="--cache-dir")


def register(sub, positive_int, eval_sub) -> None:
    """Add the `repo` subcommand group. Its check moved to `eval check` (#94 M1)."""
    repo_p = sub.add_parser(
        "repo",
        help="Repositories as evaluation subjects: validate repo-eval.yaml, L0 scan, fetch sources, reach probe, budget, "
        "agent runs and their report",
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
    validate_p.add_argument("--json", action="store_true", help="Print cli_output.v1 on stdout (the full report under data)")
    validate_p.add_argument("--fail-on-warning", action="store_true", dest="fail_on_warning",
                            help="Exit 1 when the report has any warning (for a CI gate)")
    validate_p.set_defaults(func=cmd_repo_validate, emits_cli_output=True, cli_kind="repo validate")

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
    scan_p.add_argument("--typecheck", action="store_true", default=False,
                        help="Type-check each Python fence of the guidance (§8.2): one claim per fence, fail when the "
                        "tool reports an error. The tool is mypy unless --typecheck-tool names basedpyright (on PATH). "
                        "mypy is not installed by pip install dokimi: the tested pin is the one in "
                        "requirements-test.txt; a missing mypy exits 2. Without --python-env the fences are unknown (unresolved), "
                        "not checked")
    scan_p.add_argument("--typecheck-tool", default=None, dest="typecheck_tool", choices=("mypy", "basedpyright"),
                        help="The tool --typecheck runs (default mypy). Needs --typecheck")
    scan_p.add_argument("--python-env", default=None, dest="python_env", metavar="PYTHON",
                        help="The repository's interpreter (a venv with its pinned dependencies installed); the fences "
                        "are checked against its site-packages. Needs --typecheck")
    scan_p.add_argument("--out", default=None, help="Write the repo_readiness.v1 report to this file")
    scan_p.add_argument("--json", action="store_true", help="Print cli_output.v1 on stdout (the full report under data)")
    scan_p.add_argument("--fail-on-fail", action="store_true", dest="fail_on_fail",
                        help="Exit 1 when any claim fails, an also_scan file is missing, a guidance file "
                        "or Bazel build input cannot be read, or no guidance file matched (for a CI gate)")
    scan_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    scan_p.set_defaults(func=cmd_repo_scan, emits_cli_output=True, cli_kind="repo scan")

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
    inspect_p.add_argument("--json", action="store_true", help="Print cli_output.v1 on stdout (the full report under data)")
    inspect_p.add_argument("--fail-on-error", action="store_true", dest="fail_on_error",
                           help="Exit 1 when any finding has severity error (for a CI gate)")
    inspect_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    inspect_p.set_defaults(func=cmd_repo_inspect, emits_cli_output=True, cli_kind="repo inspect")

    fetch_p = repo_sub.add_parser("fetch", help="Clone/fetch a pinned source and print its commit SHA")
    fetch_p.add_argument("source", help=source_help)
    fetch_p.add_argument("--json", action="store_true", help="Print cli_output.v1 on stdout ({sha, git_dir} under data)")
    fetch_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    fetch_p.set_defaults(func=cmd_repo_fetch, emits_cli_output=True, cli_kind="repo fetch")

    add_reach_parser(repo_sub, positive_int, source_help, cache_help, _open_tree, _load_manifest)

    add_skill_reach_parser(repo_sub, positive_int)

    budget_p = repo_sub.add_parser(
        "budget",
        help="What the repository puts into an agent's context when it starts at the root: guidance "
        "files and their imports, the skill listing, lazily loaded and on-demand files, in characters; "
        "warns on skills with no description and no body text, skills with near-duplicate descriptions (identical, or Jaccard "
        "index >= 0.5 after removing stop words when both have 3+ words), AGENTS.md files a root CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md turns off, "
        "or one in the AGENTS.md file's own directory (Claude Code rules), unreadable files (non-UTF-8, dangling link), and imports past the harness's hop "
        "limit. No LLM call.",
    )
    budget_p.add_argument("source", help=source_help)
    budget_p.add_argument("--harness", default="claude-code",
                          help="Built-in profile name or a harness_profile.v1 YAML path; its guidance names "
                          "supply the file names, guidance.imports whether @ imports are followed (not when null) "
                          "and guidance.claude_displaces_agents the off rules; root vs nested is Claude Code's "
                          "(default: claude-code)")
    budget_p.add_argument("--out", default=None, help="Write the repo_budget.v1 inventory to this file")
    budget_p.add_argument("--json", action="store_true", help="Print cli_output.v1 on stdout (the repo_budget.v1 inventory under data)")
    budget_p.add_argument("--fail-on-warning", action="store_true", dest="fail_on_warning",
                          help="Exit 1 when any warning is printed (for a CI gate)")
    budget_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    budget_p.set_defaults(func=cmd_repo_budget, emits_cli_output=True, cli_kind="repo budget")

    from . import repo_run, repo_security

    repo_run.register(repo_sub, eval_sub, positive_int, source_help, cache_help)
    repo_security.register(repo_sub)

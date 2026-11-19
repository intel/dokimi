# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo reach` kept out of commands/repo.py, which is at its size cap: the probe, `--from` and the receipt.

The tree and manifest helpers stay in repo.py, which hands them in, so this module imports nothing from it.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from dokimi import cli_flags
from dokimi.exit_codes import EXIT_USAGE
from dokimi.utils import InputError

# The probe's flags, unset by default: --from refuses any of them given, even at its default value.
_PROBE_ONLY = {"only": None, "salt": None, "work_dir": None, "keep": False, "bin": None, "dry_run": False,
               "model": None, "scenarios": None, "max_turns": None, "budget": None, "timeout": None, "out": None}
_PROBE_DEFAULTS = {"scenarios": "root,cwd,read", "max_turns": 4, "budget": 0.10, "timeout": 300}


def _reach_entry(args: argparse.Namespace, *, open_tree: Callable, load_manifest: Callable) -> None:
    """`repo reach`: probe the repository, or with --from read a saved reach.json."""
    if args.from_file is not None:  # an empty name is still a name: a usage error, never a probe
        _reach_from_saved(args, open_tree, load_manifest)
    else:
        for key, value in _PROBE_DEFAULTS.items():
            if getattr(args, key) is None:
                setattr(args, key, value)
        cmd_repo_reach(args, open_tree, load_manifest)


def _probe_flags_given(args: argparse.Namespace) -> list[str]:
    spelled = {"budget": "--max-usd-per-cell", "out": "--out-dir"}  # the probe flags whose dest is not their spelling
    return [spelled.get(k, f"--{k.replace('_', '-')}") for k, default in _PROBE_ONLY.items()
            if getattr(args, k) != default]


def _saved_reach_inputs(args: argparse.Namespace):
    """The harness profile, the source and the saved reach.json of ``--from``, as typed; raises on a usage error."""
    from dokimi.repos import RepoSource
    from dokimi.repos.harness_profile import load_profile
    from dokimi.repos.reach import ReachError, load_reach, saved_gaps, saved_matrix_problem

    if not args.from_file:
        raise ReachError("--from needs a file name")
    given = _probe_flags_given(args)
    if given:
        raise ReachError(f"--from reads a saved probe, so {', '.join(given)} cannot be used with it")
    profile = load_profile(args.harness)
    source = RepoSource.parse(args.source)
    reach = load_reach(Path(args.from_file))
    gaps = saved_gaps(reach, args.format)
    if gaps:
        raise ReachError(f"{args.from_file} has no {', '.join(gaps)}, which --format {args.format} needs")
    if args.format == "matrix" and (problem := saved_matrix_problem(reach)):
        raise ReachError(f"{args.from_file} cannot be shown as a matrix: {problem}")
    if args.format == "receipt" and _receipt_would_overwrite(Path(args.from_file), _receipt_path(args.from_file)):
        raise ReachError(f"the receipt would replace {args.from_file}, the probe it is read from: "
                         "rename the saved file")
    return profile, source, reach


def _reach_from_saved(args: argparse.Namespace, open_tree: Callable, load_manifest: Callable) -> None:
    """The matrix or the receipt of a saved reach.json, checked against the source; no harness runs."""
    from dokimi.repos import ManifestError, SourceError
    from dokimi.repos.harness_profile import ProfileError
    from dokimi.repos.receipt import check_receipt_provenance, check_saved_guidance, check_saved_reach
    from dokimi.repos.reach import ReachError, format_matrix

    try:  # the arguments and the saved file as typed: a usage error
        profile, source, reach = _saved_reach_inputs(args)
    except (SourceError, ProfileError, ReachError, OSError, ValueError) as exc:
        raise InputError(str(exc)) from exc
    tree = open_tree(source, args)
    try:  # the manifest and the file's provenance: a usage error; a failed git read is a fetch failure (1)
        manifest = load_manifest(tree, args, guidance_default=True)
        check_saved_reach(reach, manifest.id, profile.name, getattr(tree, "sha", None))
        if args.format == "receipt":
            check_receipt_provenance(reach, getattr(tree, "sha", None))
            check_saved_guidance(reach, profile.guidance)
    except (SourceError, ManifestError, ReachError) as exc:
        _refuse_tree_read(exc)
    if args.format == "receipt":
        _print_receipt(tree, profile, reach, Path(args.from_file).parent)
        print(f"wrote {_receipt_path(args.from_file)}", file=sys.stderr)
    else:
        print(format_matrix(reach))
    if any(s["error"] for s in reach["scenarios"]):
        sys.exit(1)


def _tree_read_exit_code(exc: Exception) -> int:
    """Reading the manifest or the probed files: a path that leaves the tree, a bad manifest or a bad
    scenario selection is a usage error (2); a failed `git show` or ls-tree is a fetch failure (1)."""
    from dokimi.repos import SourceError
    from dokimi.repos.source import SourceArgumentError

    git_failure = isinstance(exc, SourceError) and not isinstance(exc, SourceArgumentError)
    return 1 if git_failure else EXIT_USAGE


def _refuse_tree_read(exc: Exception) -> NoReturn:
    """Report a failed read of the tree: a usage error (2) raised as InputError; a failed git read (1) printed as ``✗``."""
    if _tree_read_exit_code(exc) == EXIT_USAGE:
        raise InputError(str(exc)) from exc
    print(f"✗ {exc}", file=sys.stderr)
    sys.exit(1)


def _refuse_receipt_over_probe(args: argparse.Namespace, out_dir: Path) -> None:
    """Exit as a usage error when ``--format receipt`` would write the receipt over the probe it is built from, or
    when an output path is a link that cannot be resolved (a loop)."""
    from dokimi.repos.reach import ReachError

    if args.format != "receipt":
        return
    try:
        overwrite = _receipt_would_overwrite(out_dir / "reach.json", out_dir / RECEIPT_NAME)
    except ReachError as exc:
        raise InputError(str(exc)) from exc
    if overwrite:
        raise InputError(f"{out_dir / RECEIPT_NAME} is the probe itself (a link to {out_dir / 'reach.json'}): "
                         "remove the link")


def _reach_plan(args: argparse.Namespace, tree, profile, kinds: tuple[str, ...],
                only: tuple[str, ...], load_manifest: Callable) -> tuple[Any, list[str], int]:
    """The manifest, the probed files and the count of scenarios a run executes, for a run and --dry-run alike.

    Exits as the run would: a bad manifest or scenario selection is a usage error and a failed git read a fetch
    failure (1); a tree with no guidance file, or whose every guidance file is a link that cannot be followed, is
    run_reach's own refusal (1), before the scenarios are selected.
    """
    from dokimi.repos import ManifestError, SourceError
    from dokimi.repos.reach import ReachError, check_files, followable, probe_files, scenarios, shadow_for

    try:  # the manifest and the files it selects
        manifest = load_manifest(tree, args, guidance_default=True)
        files = probe_files(tree, manifest, profile)
    except (SourceError, ManifestError, ReachError) as exc:
        _refuse_tree_read(exc)
    try:  # what run_reach plans from, with its guards: a file to probe, no scenario for a link it drops
        check_files(files, profile)
        probed = followable(tree, files)[0]
    except ReachError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    try:  # the scenarios they select: a usage error; a failed git read is a fetch failure (1)
        shadow = shadow_for(profile, tree, kinds)
        available = [s.id for s in scenarios(probed, kinds, shadow=shadow)]
        missing = [o for o in only if o not in available]
        if missing:
            raise ReachError(f"--only {', '.join(missing)} matches no scenario (have: {', '.join(available)})")
        planned_count = len(scenarios(probed, kinds, only, shadow))
        if not planned_count:
            raise ReachError("no scenario selected (check --scenarios and --only)")
    except (SourceError, ManifestError, ReachError) as exc:
        _refuse_tree_read(exc)
    return manifest, files, planned_count


def cmd_repo_reach(args: argparse.Namespace, open_tree: Callable, load_manifest: Callable) -> None:
    """L1 reach probe: which guidance files one harness/version/model loads."""
    from dokimi.repos import RepoSource, SourceError
    from dokimi.repos.harness_profile import ProfileError, load_profile
    from dokimi.repos.reach import ReachError, check_flags, check_kinds, check_shadow, run_reach, save_reach

    kinds = tuple(k.strip() for k in args.scenarios.split(",") if k.strip())
    only = tuple(args.only or ())
    try:  # the arguments as typed: a usage error
        check_flags(kinds, args.budget, args.model)
        profile = load_profile(args.harness)
        check_kinds(profile, kinds)
        check_shadow(profile, kinds)
        source = RepoSource.parse(args.source)
    except (SourceError, ProfileError, ReachError) as exc:
        raise InputError(str(exc)) from exc
    tree = open_tree(source, args)
    manifest, files, planned_count = _reach_plan(args, tree, profile, kinds, only, load_manifest)
    if not args.dry_run:
        print(f"{len(files)} guidance files, {planned_count} scenarios, "
              f"at most ${args.budget:g} each", file=sys.stderr)
    out_dir = Path(args.out or "reach-out")
    _refuse_receipt_over_probe(args, out_dir)
    try:  # the harness runs, or with --dry-run every check before the first: a runtime failure
        payload = run_reach(
            tree=tree, manifest=manifest, profile=profile, model=args.model, out_dir=out_dir,
            bin_path=args.bin, work_dir=Path(args.work_dir) if args.work_dir else None, kinds=kinds,
            only=only, salt=args.salt, max_turns=args.max_turns, budget_usd=args.budget,
            timeout_s=args.timeout, keep=args.keep, dry_run=args.dry_run,
        )
    except (SourceError, ProfileError, ReachError) as exc:  # ProfileError: --bin cannot run its version command
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return
    path = save_reach(payload, out_dir)
    _print_reach_output(tree, profile, payload, out_dir, path, receipt=args.format == "receipt")
    if any(s["error"] for s in payload["scenarios"]):
        sys.exit(1)


def _print_reach_output(tree, profile, payload: dict, out_dir: Path, path: Path, receipt: bool) -> None:
    """The matrix on stdout; with ``receipt``, the effective-instructions receipt on stdout and the note on stderr."""
    from dokimi.repos.reach import format_matrix

    if receipt:
        _print_receipt(tree, profile, payload, out_dir)
        print(f"wrote {path}", file=sys.stderr)
    else:
        print(format_matrix(payload))
        print(f"wrote {path}")


RECEIPT_NAME = "effective_instructions.json"


def _receipt_path(from_file: str) -> Path:
    return Path(from_file).parent / RECEIPT_NAME


def _strict_resolve(path: Path) -> Path:
    """``path`` with every link resolved, a missing or dangling last component included. Non-strict resolve() of a link
    loop raises RuntimeError before Python 3.13 and returns the looping path from 3.13 on; strict resolve raises
    OSError on a loop on every version."""
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        return path.resolve()


def _receipt_would_overwrite(probe: Path, receipt: Path) -> bool:
    """A receipt written at the probe's path, directly or through a link, would replace the probe it is built from.
    A link loop cannot be resolved (OSError, or RuntimeError before Python 3.13): a usage error, not a traceback."""
    from dokimi.repos.reach import ReachError

    try:
        return _strict_resolve(probe) == _strict_resolve(receipt)
    except (OSError, RuntimeError) as exc:
        raise ReachError(f"cannot resolve {receipt}: {exc}") from exc


def _print_receipt(tree, profile, payload: dict, out_dir: Path) -> None:
    """The effective-instructions receipt of a probe: budget's start-up chain, reach's observed states."""
    from dokimi.artifacts import save_artifact
    from dokimi.repos.budget import budget
    from dokimi.repos.receipt import build_receipt

    receipt = build_receipt(payload, budget(tree, profile.guidance_names, profile.import_max_depth, profile.reads_claude_rules,
                                            claude_displaces_agents=profile.claude_displaces_agents,
                                            follow_imports=profile.follows_imports), tree)
    try:  # a directory that is not writable, or one already named effective_instructions.json: a runtime failure
        written = save_artifact("effective_instructions", receipt, out_dir / RECEIPT_NAME)
    except OSError as exc:
        print(f"✗ cannot write {out_dir / RECEIPT_NAME}: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(written, indent=2))


def add_reach_parser(repo_sub, positive_int, source_help: str, cache_help: str, open_tree: Callable,
                     load_manifest: Callable) -> None:
    """Add `repo reach`; ``open_tree`` and ``load_manifest`` are repo.py's helpers of those names."""
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
    reach_p.add_argument("--model", default=None, help="Model string passed to the harness as it is (required "
                         "unless --from)")
    reach_p.add_argument("--from", default=None, dest="from_file", metavar="REACH_JSON",
                         help="Read a saved reach.json instead of probing: no harness runs, no cost. The source is "
                         "still read, to check the file's repository, harness and sha; the receipt goes beside the file")
    reach_p.add_argument("--bin", default=None, help="Harness executable (default: the profile's `bin`)")
    cli_flags.add(reach_p, "output", "--out-dir", dest="out", default=None, help="Output directory: reach.json and traces/ (default: reach-out)")
    reach_p.add_argument("--scenarios", default=None,
                         help="Comma list of scenario kinds: root, cwd, read, bash, shadow (default: root,cwd,read). "
                         "bash cats each probe file through the Bash tool, for a profile with a bash tool mode; "
                         "shadow writes the profile's guidance.shadow_file (CLAUDE.local.md for claude-code) "
                         "for one scenario at a time; a profile without one refuses it")
    reach_p.add_argument("--only", action="append", default=None, metavar="SCENARIO",
                         help="Run only this scenario id (for example read=cpp); repeatable")
    reach_p.add_argument("--work-dir", default=None, dest="work_dir",
                         help="Parent of the temporary workspace (default: the system temp dir). "
                         "No ancestor may hold guidance files.")
    reach_p.add_argument("--salt", default=None, help="Token salt (default: random per run)")
    reach_p.add_argument("--max-turns", type=positive_int, default=None, dest="max_turns",
                         help="Turn cap per scenario (default: 4)")
    cli_flags.add(reach_p, "spend", "--max-usd-per-cell", type=float, default=None, dest="budget", metavar="X",
                  help="Per-scenario USD cap (default: 0.10)")
    reach_p.add_argument("--timeout", type=positive_int, default=None, help="Per-scenario seconds (default: 300)")
    reach_p.add_argument("--keep", action="store_true", help="Keep the temporary workspace")
    reach_p.add_argument("--format", choices=["matrix", "receipt"], default="matrix",
                         help="matrix: the table (default). receipt: print the effective-instructions/v0 receipt as JSON and "
                         "write it to effective_instructions.json in --out-dir (docs/repo-evaluation-contract.md §7.1)")
    reach_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Print the files and the exact argv of every scenario; run no harness. It makes the "
                         "checks a run makes before its first harness call, so it builds the workspace and removes it")
    reach_p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)
    reach_p.set_defaults(func=functools.partial(_reach_entry, open_tree=open_tree, load_manifest=load_manifest))

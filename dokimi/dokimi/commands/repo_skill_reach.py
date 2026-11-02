# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo skill-reach: which Agent Skills a harness lists, from a fixture catalogue (skill_discovery.v1)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_USAGE


def cmd_repo_skill_reach(args: argparse.Namespace) -> None:
    """Skill-discovery probe: which skills one harness/version/model lists, from a fixture catalogue."""
    from dokimi.repos.harness_profile import ProfileError, load_profile
    from dokimi.repos.reach import ReachError
    from dokimi.repos.skill_reach import (
        SkillReachError,
        format_table,
        load_catalogue,
        run_skill_reach,
        save_skill_reach,
        scenario_ids,
    )

    try:  # the arguments as typed: a usage error
        if not math.isfinite(args.budget) or args.budget <= 0:
            raise SkillReachError(f"--budget must be > 0, got {args.budget:g}")
        profile = load_profile(args.harness)
        cases = load_catalogue(Path(args.catalogue) if args.catalogue else None)
        only = tuple(args.only or ())
        missing = sorted(set(only) - {c.id for c in cases})
        if missing:
            raise SkillReachError(f"--only {', '.join(missing)} matches no case")
        selected = [c for c in cases if not only or c.id in only]
    except (ProfileError, SkillReachError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if args.dry_run:
        argv = profile.argv(prompt="<ask>", model=args.model, tools="skills", max_turns=args.max_turns,
                            budget_usd=args.budget, bin_path=args.bin) if "skills" in profile.tools else None
        print(json.dumps({"cases": [c.id for c in selected], "scenarios": [s for s, _ in scenario_ids(selected)],
                          "argv": argv, "declared": profile.skills}, indent=2))
        return
    print(f"{len(selected)} cases, {len(scenario_ids(selected))} scenarios, at most ${args.budget:g} each",
          file=sys.stderr)
    out_dir = Path(args.out)
    try:
        payload = run_skill_reach(
            profile=profile, model=args.model, out_dir=out_dir, cases=selected,
            catalogue=Path(args.catalogue) if args.catalogue else None, bin_path=args.bin,
            work_dir=Path(args.work_dir) if args.work_dir else None, salt=args.salt,
            max_turns=args.max_turns, budget_usd=args.budget, timeout_s=args.timeout, keep=args.keep,
        )
    except (ProfileError, SkillReachError, ReachError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    path = save_skill_reach(payload, out_dir)
    print(format_table(payload))
    print(f"wrote {path}")
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

    receipt = build_receipt(payload, budget(tree, profile.guidance_names, profile.import_max_depth), tree)
    try:  # a directory that is not writable, or one already named effective_instructions.json: a runtime failure
        written = save_artifact("effective_instructions", receipt, out_dir / RECEIPT_NAME)
    except OSError as exc:
        print(f"✗ cannot write {out_dir / RECEIPT_NAME}: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(written, indent=2))


def add_skill_reach_parser(repo_sub, positive_int) -> None:
    skill_p = repo_sub.add_parser(
        "skill-reach",
        help="Skill-discovery probe: place token-seeded SKILL.md fixtures (project, user and client dirs, "
        "nesting, symlinks, name collisions, description formats) in a fresh workspace and HOME and report "
        "which skills a harness lists (skill_discovery.v1). Costs one cheap-model call per scenario.",
    )
    skill_p.add_argument("--harness", default="claude-code",
                         help="Built-in profile name or a harness_profile.v1 YAML path (default: claude-code); "
                         "it needs a `skills` block and a `skills` tool mode")
    skill_p.add_argument("--model", required=True, help="Model string passed to the harness as it is")
    skill_p.add_argument("--catalogue", default=None,
                         help="A skill_fixtures.v1 YAML (default: the built-in repos/skill_fixtures.yaml)")
    skill_p.add_argument("--only", action="append", default=None, metavar="CASE",
                         help="Place only this case id; repeatable")
    skill_p.add_argument("--bin", default=None, help="Harness executable (default: the profile's `bin`)")
    skill_p.add_argument("--out", default="skill-reach-out", help="Output directory: skill-discovery.json and traces/")
    skill_p.add_argument("--work-dir", default=None, dest="work_dir",
                         help="Parent of the temporary fixture (default: the system temp dir). No ancestor may "
                         "hold guidance or a skills dir.")
    skill_p.add_argument("--salt", default=None, help="Token salt (default: random per run)")
    skill_p.add_argument("--max-turns", type=positive_int, default=4, dest="max_turns")
    skill_p.add_argument("--budget", type=float, default=0.10, help="Per-scenario USD cap (default: 0.10)")
    skill_p.add_argument("--timeout", type=positive_int, default=300, help="Per-scenario seconds")
    skill_p.add_argument("--keep", action="store_true", help="Keep the temporary fixture")
    skill_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Print the cases, scenarios and argv; run nothing")
    skill_p.set_defaults(func=cmd_repo_skill_reach)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo skill-reach: which Agent Skills a harness lists, from a fixture catalogue (skill_discovery.v1)."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

from dokimi.exit_codes import EXIT_USAGE

DEFAULT_BUDGET = 0.10  # USD per scenario, for a harness whose invoke passes {budget}


def cmd_repo_skill_reach(args: argparse.Namespace) -> None:
    """Skill-discovery probe: which skills one harness/version/model lists, from a fixture catalogue."""
    from dokimi.repos.harness_profile import ProfileError, load_profile
    from dokimi.repos.reach import ReachError
    from dokimi.repos.skill_reach import (
        SkillReachError,
        format_table,
        read_catalogue,
        run_skill_reach,
        save_skill_reach,
        scenario_ids,
    )

    try:  # the arguments as typed: a usage error
        if args.budget is not None and (not math.isfinite(args.budget) or args.budget <= 0):
            raise SkillReachError(f"--budget must be > 0, got {args.budget:g}")
        profile = load_profile(args.harness)
        # Only a profile whose invoke passes {budget} can cap the spend; the others are bounded by the timeout.
        capped = any("{budget}" in element for element in profile.invoke)
        if args.budget is not None and not capped:
            raise SkillReachError(f"--budget: {profile.name} has no spend-cap flag, so the cap would not be "
                                  "enforced; drop --budget (the timeout bounds each run)")
        budget = args.budget if args.budget is not None else DEFAULT_BUDGET
        cases, digest = read_catalogue(Path(args.catalogue) if args.catalogue else None)
        only = tuple(args.only or ())
        missing = sorted(set(only) - {c.id for c in cases})
        if missing:
            raise SkillReachError(f"--only {', '.join(missing)} matches no case")
        selected = [c for c in cases if not only or c.id in only]
        _refuse_what_a_run_would_refuse(args, profile, selected)
    except (ProfileError, SkillReachError, ReachError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if args.dry_run:
        argv = profile.argv(prompt="<ask>", model=args.model, tools="skills", max_turns=args.max_turns,
                            budget_usd=budget, bin_path=args.bin)
        print(json.dumps({"cases": [c.id for c in selected], "scenarios": [s for s, _ in scenario_ids(selected)],
                          "argv": argv, "declared": profile.skills}, indent=2))
        return
    spend = f"at most ${budget:g} each" if capped else "no spend cap (the timeout bounds each run)"
    print(f"{len(selected)} cases, {len(scenario_ids(selected))} scenarios, {spend}", file=sys.stderr)
    out_dir = Path(args.out)
    try:  # the run and the artifact: a missing work dir or an unwritable --out is a diagnostic, not a traceback
        payload = run_skill_reach(
            profile=profile, model=args.model, out_dir=out_dir, cases=selected,
            catalogue=Path(args.catalogue) if args.catalogue else None, catalogue_sha256=digest, bin_path=args.bin,
            work_dir=Path(args.work_dir) if args.work_dir else None, salt=args.salt,
            max_turns=args.max_turns, budget_usd=budget, timeout_s=args.timeout, keep=args.keep,
        )
        path = save_skill_reach(payload, out_dir)
    except (ProfileError, SkillReachError, ReachError, OSError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    print(format_table(payload))
    print(f"wrote {path}")
    if any(s["error"] for s in payload["scenarios"]):
        sys.exit(1)


def _refuse_what_a_run_would_refuse(args: argparse.Namespace, profile, selected: list) -> None:
    """What a run refuses before it places anything, checked before a dry run too (so both refuse the same
    inputs); --out is checked for a run only, since a dry run writes nothing."""
    from dokimi.repos.skill_reach import SkillReachError, check_profile, check_skill_ancestors, resolve_at, skill_dirs

    check_profile(profile)
    dirs = skill_dirs(profile)
    resolved = [resolve_at(case, dirs) for case in selected]  # every case: all() would stop at the first placeable
    if all(r is None for r in resolved):
        raise SkillReachError("no case could be placed: the profile declares no skill dir that a selected case names")
    if args.work_dir and not Path(args.work_dir).is_dir():
        raise SkillReachError(f"--work-dir {args.work_dir} is not a directory")
    # run_skill_reach checks a fixture under a temp dir made inside --work-dir: the same ancestors as this path
    check_skill_ancestors(Path(args.work_dir or tempfile.gettempdir()).resolve() / "_", profile.guidance_names)
    if args.dry_run:
        return
    existing = Path(args.out) / "skill-traces"
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir():
        raise SkillReachError(f"--out {args.out}: {existing} is not a directory")


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
    skill_p.add_argument("--out", default="skill-reach-out",
                         help="Output directory: skill-discovery.json and skill-traces/")
    skill_p.add_argument("--work-dir", default=None, dest="work_dir",
                         help="Parent of the temporary fixture (default: the system temp dir). No ancestor may "
                         "hold guidance or a skills dir.")
    skill_p.add_argument("--salt", default=None, help="Token salt (default: random per run)")
    skill_p.add_argument("--max-turns", type=positive_int, default=4, dest="max_turns")
    skill_p.add_argument("--budget", type=float, default=None,
                         help="Per-scenario USD cap (default: 0.10). Refused for a harness with no spend-cap flag "
                         "(codex, copilot): the timeout is their only bound")
    skill_p.add_argument("--timeout", type=positive_int, default=300, help="Per-scenario seconds")
    skill_p.add_argument("--keep", action="store_true", help="Keep the temporary fixture")
    skill_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Print the cases, scenarios and argv; run nothing")
    skill_p.set_defaults(func=cmd_repo_skill_reach)

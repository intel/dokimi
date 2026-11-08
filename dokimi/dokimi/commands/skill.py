# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""skill subcommand group: measure one skill (#94 M2, M3).

``skill novelty`` (analyze, atoms, probe, items) and ``skill shorten`` are the moved leaves;
``skill check SKILL`` is the catalog check scoped to one skill (#94 M3). ``skill routing`` follows
with the routing moves of #94 §4.
"""

from __future__ import annotations

import argparse


def register(sub, positive_int) -> None:
    """Add the `skill` subcommand group."""
    skill_p = sub.add_parser(
        "skill",
        help="Measure one skill: its content novelty and how it can be shortened",
    )
    skill_sub = skill_p.add_subparsers(dest="skill_cmd", required=True)
    register_skill(skill_sub, positive_int)
    register_check(skill_sub)


def cmd_skill_check(args: argparse.Namespace) -> None:
    """`skill check SKILL --root ROOT`: the `catalog check` of ROOT, reporting SKILL only (#94 M3)."""
    from dokimi.commands.catalog import cmd_catalog_scan

    cmd_catalog_scan(argparse.Namespace(
        root=args.root, kind="skills", strict=args.strict, format=args.format,
        tasks_subdir="evaluation/harbor/tasks", per_skill=False, skip_nested=False,
        jsonl=None, jobs=None, resume=False, only=args.skill))


def register_check(skill_sub) -> None:
    """Add `skill check SKILL`: the same checks as `catalog check`, for one skill."""
    check_p = skill_sub.add_parser(
        "check", help="Check one skill under --root: the catalog check's findings for it (same rules)"
    )
    check_p.add_argument("skill", help="The skill's name: its name in skills.yaml (its directory if the manifest omits it), "
                              "or its SKILL.md frontmatter name when the root has no skills.yaml")
    check_p.add_argument("--root", required=True, help="Local checkout the skill is under")
    check_p.add_argument("--strict", action="store_true",
                         help="Exit 1 on a spec error of the skill as well as on one that does not load")
    check_p.add_argument("--format", choices=["text", "json"], default="text",
                         help="text: one line for the skill; json: the catalog check's row for it")
    check_p.set_defaults(func=cmd_skill_check)


def register_skill(skill_sub, positive_int) -> None:
    """Add `skill novelty` (analyze, atoms, probe, items) and `skill shorten` (#94 M2)."""
    from dokimi.commands.novelty import (
        _add_bundle_args,
        _add_common,
        _add_model_args,
        _add_spend_cap,
        _stops_at_cap,
        cmd_novelty_analyze,
        cmd_novelty_atoms,
        cmd_novelty_items,
        cmd_novelty_probe,
        cmd_novelty_shorten,
    )
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
    from dokimi.llm import ANSWER_PROVIDERS as PROVIDERS
    from dokimi.novelty.tiers import TIERS

    novelty_p = skill_sub.add_parser(
        "novelty",
        help="Content novelty of one skill: what it adds to a model",
    )
    novelty_sub = novelty_p.add_subparsers(dest="skill_novelty_cmd", required=True)

    analyze_p = novelty_sub.add_parser(
        "analyze", help="Full pipeline: atoms → probes → categories → attribution → shortening"
    )
    _add_common(analyze_p, positive_int)
    _add_model_args(analyze_p, positive_int)
    analyze_p.add_argument("--out-dir", default=None, dest="out_dir")
    analyze_p.add_argument("--out-json", default=None, dest="out_json")
    analyze_p.add_argument("--view-format", default="html", choices=["html", "md"],
                           dest="view_format")
    analyze_p.add_argument("--no-classify", action="store_true", dest="no_classify",
                           help="Skip the judged content categories (cheaper; the artifact "
                                "records the skip as a warning)")
    analyze_p.add_argument("--no-shorten", action="store_true", dest="no_shorten")
    analyze_p.add_argument("--no-confirm-carriers", action="store_true",
                           dest="no_confirm_carriers",
                           help="Skip checking each atom against the span cited for it; the "
                                "span views then report the extractor's citation as given "
                                "(0.19 of atoms in the study were not recoverable from it)")
    analyze_p.add_argument("--no-verify", action="store_true", dest="no_verify",
                           help="Skip the per-atom faithfulness check")
    analyze_p.add_argument("--tier", default=None, choices=sorted(TIERS),
                           help="Named cost tier. The measurement is probe-budget bound "
                                "(~4 model calls per paired probe), so a tier is mostly a "
                                "statement about atoms and probe kinds: triage ~100 calls, "
                                "study ~1000 on the same 1,700-word skill. Every tier records "
                                "the claims it forfeits into run_config and the artifact's "
                                "warnings. An explicit flag always wins over the tier")
    analyze_p.add_argument("--estimate-only", action="store_true", dest="estimate_only",
                           help="Print the projected model-call bill and exit without "
                                "spending anything")
    _add_bundle_args(analyze_p, positive_int)
    analyze_p.set_defaults(func=_stops_at_cap(cmd_novelty_analyze))

    atoms_p = novelty_sub.add_parser("atoms", help="Extract knowledge atoms and spans only")
    _add_common(atoms_p, positive_int)
    # The extractor writes the answer key; `analyze` gives that role to the judge, so the
    # two paths default to the same model and a saved atom set matches an analyze run.
    atoms_p.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                         help="Extractor model when --extract-model is not given "
                              f"(default: {DEFAULT_JUDGE_MODEL}, the default judge)")
    atoms_p.add_argument("--extract-model", default=None, dest="extract_model")
    atoms_p.add_argument("--provider", default=DEFAULT_JUDGE_PROVIDER, choices=PROVIDERS)
    atoms_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens")
    atoms_p.add_argument("--no-verify", action="store_true", dest="no_verify")
    atoms_p.add_argument("--out", default=None)
    _add_spend_cap(atoms_p)
    _add_bundle_args(atoms_p, positive_int)
    atoms_p.set_defaults(func=_stops_at_cap(cmd_novelty_atoms))

    probe_p = novelty_sub.add_parser("probe", help="Run paired probes against a saved atom set")
    _add_common(probe_p, positive_int)
    _add_model_args(probe_p, positive_int)
    probe_p.add_argument("--atoms", required=True, help="atoms.json from `skill novelty atoms`")
    probe_p.add_argument("--out", default=None)
    probe_p.set_defaults(func=_stops_at_cap(cmd_novelty_probe))

    items_p = novelty_sub.add_parser(
        "items", help="Item analysis across saved probe runs: which atoms separate the models"
    )
    items_p.add_argument("--probes", nargs="+", required=True,
                         help="probes.json files from `skill novelty probe`, one per model. "
                              "Point-biserial discrimination needs at least three, since the "
                              "models are the respondents")
    items_p.add_argument("--arm", default="full",
                         help="Arm to score discrimination and check probe-kind agreement in "
                              "(default: the artifact arm)")
    items_p.add_argument("--out", default=None)
    items_p.set_defaults(func=cmd_novelty_items)

    shorten_p = skill_sub.add_parser(
        "shorten", help="Score shortening candidates on tokens/knowledge/value/protection/routing"
    )
    _add_common(shorten_p, positive_int)
    shorten_p.add_argument("--atoms", required=True, help="atoms.json from `skill novelty atoms`")
    # Same model as the retention oracle inside `analyze` (its extractor, i.e. the judge):
    # adjudications are cached per model, and a different one would score other verdicts.
    shorten_p.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                           help="Retention adjudicator model "
                                f"(default: {DEFAULT_JUDGE_MODEL}, the default judge)")
    shorten_p.add_argument("--extract-model", default=None, dest="extract_model")
    shorten_p.add_argument("--provider", default=DEFAULT_JUDGE_PROVIDER, choices=PROVIDERS)
    shorten_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens")
    shorten_p.add_argument("--lexical-only", action="store_true", dest="lexical_only",
                           help="Run the retention oracle without an LLM adjudicator. Cheap and "
                                "reproducible, but blind to contradictions")
    shorten_p.add_argument("--allow-unsafe", action="store_true", dest="allow_unsafe",
                           help="Allow a best-candidate pick that drops protected spans")
    shorten_p.add_argument("--out", default=None, help="Write the chosen candidate here")
    _add_spend_cap(shorten_p)
    shorten_p.set_defaults(func=_stops_at_cap(cmd_novelty_shorten))

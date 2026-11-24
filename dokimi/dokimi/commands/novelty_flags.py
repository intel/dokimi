# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The argparse flags shared by `skill novelty analyze|atoms|probe` and `skill shorten`.

Kept apart from `novelty.py` (its 800-line cap): these functions only declare flags, so the
flag set reads in one place. The flag names, defaults and help are unchanged.
"""

from __future__ import annotations

import argparse

from dokimi import cli_flags
from dokimi.cli_types import unit_interval
from dokimi.commands.novelty_run import add_judge2_args, add_panel_args, add_spend_cap
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)
from dokimi.llm import ANSWER_PROVIDERS as PROVIDERS


def _add_bundle_args(parser: argparse.ArgumentParser, positive_int) -> None:
    """Flags that decide *which files of the artifact* are measured.

    On `atoms` as well as `analyze`: the cheap path is `skill novelty atoms` once and
    `skill novelty probe --atoms` per model, so the bundle decision has to be makeable at
    extraction time or the reuse path can only ever see the entry point.
    """
    parser.add_argument("--include-resources", action="store_true", dest="include_resources",
                        help="Measure the whole skill bundle, not just SKILL.md. In the "
                             "33-skill study corpus 0.72 of all words live in bundled "
                             "references/scripts/patterns (worst skill: 0.99), so an "
                             "entry-point-only run says little about a progressive-disclosure "
                             "skill. Costs proportionally more: the bill is ~4 LLM calls per "
                             "paired probe")
    parser.add_argument("--resource-roles", nargs="+", default=None, dest="resource_roles",
                        choices=["reference", "pattern", "eval", "script", "template",
                                 "trigger", "data", "top", "other"],
                        help="Restrict bundled files to these roles (default: every "
                             "role except eval)")
    parser.add_argument("--max-file-words", type=positive_int, default=4000,
                        dest="max_file_words",
                        help="Skip a bundled file larger than this instead of truncating it; a "
                             "cut reference would be attributed as if complete")
    parser.add_argument("--max-resource-words", type=positive_int, default=20000,
                        dest="max_resource_words",
                        help="Total budget across bundled files, taken in role order "
                             "(reference, pattern, eval, rest) and recorded per exclusion")


def _add_common(parser: argparse.ArgumentParser, positive_int) -> None:
    parser.add_argument("--artifact", required=True,
                        help="skill:<path to SKILL.md or its directory>, or text:<path>")
    parser.add_argument("--max-atoms", type=positive_int, default=None, dest="max_atoms",
                        help="Atom cap per artifact (default 120). The measurement is "
                             "LLM-call bound: atoms × probes × arms × (answer + grade). "
                             "Unset by default so that passing the default value explicitly "
                             "still overrides a --tier cap")
    parser.add_argument("--all-atoms", action="store_true", dest="all_atoms",
                        help="Probe every extracted atom (opt-in: the expensive sweep); with "
                             "--atoms, every atom in the file, not only the cap's sample")
    cli_flags.add(parser, "tokens", "--token-budgets", metavar="F", type=unit_interval, nargs="+", default=[0.75, 0.5],
                  dest="budgets", help="Target token fractions for shortening candidates")
    parser.add_argument("--cache-dir", default=None, dest="cache_dir",
                        help="Disk memo of retention adjudications only (not model replies). The "
                             "response cache is separate and opt-in: set DOKIMI_RESPONSE_CACHE to a "
                             "writable directory. Research or a rerun should set it; without it a rerun "
                             "pays for every call again.")
    parser.add_argument("--seed", type=int, default=0,
                        help="Seed for the shuffled control and random selection, so both "
                             "are reproducible across runs")


def _add_model_args(parser: argparse.ArgumentParser, positive_int) -> None:
    parser.add_argument("--model", default=DEFAULT_ANSWER_MODEL,
                        help="The model under measurement — novelty is a property of the "
                             "(artifact, model) pair, not of the file")
    parser.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=PROVIDERS)
    parser.add_argument("--judge-model", default=None, dest="judge_model",
                        help="Grading model; must differ from --model (default: "
                             f"{DEFAULT_JUDGE_PROVIDER}/{DEFAULT_JUDGE_MODEL}, or "
                             f"{DEFAULT_GENERATOR_PROVIDER}/{DEFAULT_GENERATOR_MODEL} when "
                             "--model is that judge). A --judge-model without "
                             "--judge-provider runs on --provider")
    parser.add_argument("--judge-provider", default=None, dest="judge_provider", choices=PROVIDERS,
                        help="Provider for the judge. Without --judge-model, the default judge "
                             "that runs on this provider; if none does, the run exits asking "
                             "for --judge-model")
    add_panel_args(parser)
    parser.add_argument("--extract-model", default=None, dest="extract_model",
                        help="Extractor/adjudicator model (defaults to --judge-model, so the "
                             "answering model never writes its own answer key)")
    parser.add_argument("--extract-provider", default=None, dest="extract_provider",
                        choices=PROVIDERS)
    cli_flags.add(parser, "tokens", "--max-output-tokens", metavar="N", type=positive_int, default=None,
                  dest="max_tokens", help="Output-token ceiling. Raise for reasoning models, which can "
                  "spend the provider default on reasoning and return empty text")
    parser.add_argument("--arms", default="no_artifact,full",
                        help="Arms to run: no_artifact,full[,placebo][,shuffled]"
                             "[,no_artifact_repeat] (the last is the noise floor)")
    parser.add_argument("--context-framing", default="reference", dest="context_framing",
                        choices=("reference", "neutral"),
                        help="How the context is introduced: as reference material, or as a "
                             "document that may be irrelevant (the placebo framing control)")
    parser.add_argument("--placebo-text", default=None, dest="placebo_text",
                        help="File of domain-mismatched text for the placebo control: it "
                             "separates 'this artifact carries knowledge' from 'this model "
                             "reacts to any long context'")
    parser.add_argument("--concurrency", type=positive_int, default=5)
    parser.add_argument("--allow-self-judge", action="store_true", dest="allow_self_judge",
                        help="Let the answering model grade itself (inflates scores), or --judge2 be"
                             " the first judge's model (its agreement is not independent)")
    add_judge2_args(parser, PROVIDERS)
    parser.add_argument("--no-paraphrase", action="store_true", dest="no_paraphrase")
    parser.add_argument("--no-reversal", action="store_true", dest="no_reversal")
    add_spend_cap(parser)

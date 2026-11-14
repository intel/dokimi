# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The argparse leaves of `skill routing` and `catalog routing` (#94 M3).

Both leaves take the same routing options; `cmd_routing` in `dokimi.commands.novelty` runs them.
"""

from __future__ import annotations

from dokimi.commands.novelty import cmd_routing
from dokimi.commands.novelty_run import add_spend_cap, stops_at_cap
from dokimi.defaults import DEFAULT_ANSWER_PROVIDER
from dokimi.llm import ANSWER_PROVIDERS as PROVIDERS


def _skill_ref(text: str) -> str:
    """A skill path as the routing field reads it: a bare path is a skill; `skill:` and `text:` pass through."""
    return text if text.startswith(("skill:", "text:")) else f"skill:{text}"


def add_skill_routing(skill_sub, positive_int) -> None:
    """`skill routing SKILL [--against ROOT]`: can one skill be selected against its competitors (#94 M3)."""
    routing_p = skill_sub.add_parser(
        "routing", help="Can the skill be selected against competitors? (name+description)"
    )
    routing_p.add_argument("artifact", type=_skill_ref, metavar="SKILL",
                           help="The skill: its directory or SKILL.md. text:PATH is also accepted")
    routing_p.add_argument("--against", default=None, dest="catalog",
                           metavar="ROOT",
                           help="Directory of skills to take the competitors from: every "
                                "skill the catalog lists (skills.yaml when present, else every "
                                "SKILL.md under it; a template/ copy and vendored or VCS "
                                "directories like .git are left out, and a byte-identical copy "
                                "under another path counts once) except the artifact itself, the "
                                "siblings a harness lists next to it. Combines with --competitors")
    _add_routing_options(routing_p, positive_int)
    routing_p.set_defaults(all_vs_all=False, func=stops_at_cap(cmd_routing))


def add_catalog_routing(catalog_sub, positive_int) -> None:
    """`catalog routing ROOT`: every skill under ROOT is the gold in turn, against the rest (#94 M3)."""
    routing_p = catalog_sub.add_parser(
        "routing", help="Can each skill under ROOT be selected against the others? (all-vs-all)"
    )
    routing_p.add_argument("catalog", metavar="ROOT",
                           help="Directory of skills: every skill it lists is the gold in turn")
    routing_p.add_argument("--queries-by-skill", default=None, dest="queries_by_skill",
                           help="A JSON object of skill name to the list of "
                                "requests that should select that skill")
    _add_routing_options(routing_p, positive_int, refused=True)
    routing_p.set_defaults(all_vs_all=True, artifact=None, func=stops_at_cap(cmd_routing))


def _add_routing_options(routing_p, positive_int, refused: bool = False) -> None:
    """The options both routing leaves take: the field, the queries, the model and the spend cap.

    ``refused``: the leaf refuses --queries and --queries-from-headings (it takes --queries-by-skill).
    They stay registered, so `--queries` is not read as `--queries-by-skill`, and say so in help.
    """
    routing_p.add_argument("--competitors", nargs="+", default=None, type=_skill_ref,
                           help="Other artifacts the selector sees. One candidate is not "
                                "routing: sibling confusion is the failure mode (check it "
                                "with independently written queries). A bare path is a skill "
                                "(its directory or SKILL.md); text:PATH is also accepted")
    routing_p.add_argument("--include-disabled", action="store_true", dest="include_disabled",
                           help="Keep skills with disable-model-invocation in the field. Off by "
                                "default: no harness lists them, so their card measures a "
                                "choice that does not exist")
    routing_p.add_argument("--keep-copies-under", default=None, dest="keep_copies_under",
                           metavar="DIR",
                           help="DIR under the catalog root (--against, or ROOT for catalog routing), "
                                "relative to it: when a name's copies differ, keep only those "
                                "under DIR, which must agree. Other names are unchanged")
    routing_p.add_argument("--include-invalid", action="store_true", dest="include_invalid",
                           help="Keep catalog and skill: competitors with a spec error. A "
                                "description over 1024 characters alone never drops a skill: "
                                "it is kept and warned about")
    routing_p.add_argument("--queries", default=None,
                           help="Refused here: use --queries-by-skill" if refused else
                                "JSON list (or one per line) of user-shaped requests that "
                                "should select this artifact, written independently of it")
    routing_p.add_argument("--queries-from-headings", action="store_true",
                           dest="queries_from_headings",
                           help="Refused here: use --queries-by-skill" if refused else
                                "Derive queries from the artifact's headings. Triage only: the "
                                "artifact then sets its own problem scope and relevance, and "
                                "the result is stamped as such")
    routing_p.add_argument("--model", default=None,
                           help="Also ask this model to pick from the same cards. Optional: "
                                "without it only the free lexical floor is computed")
    routing_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=PROVIDERS)
    routing_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens")
    routing_p.add_argument("--seed", type=int, default=0,
                           help="Seeds only the card order (a seeded permutation per query, so "
                                "a position-biased model scores about 1/N). Not answer "
                                "repeatability: use --reps")
    routing_p.add_argument("--reps", type=positive_int, default=1,
                           dest="repeats", metavar="N",
                           help="Ask the model N times per query, same card order, and report "
                                "per-query agreement and the run-to-run noise floor. Needs "
                                "--model; N times the calls")
    routing_p.add_argument("--estimate-only", action="store_true", dest="estimate_only",
                           help="Print the calls, tokens and list-price expected_usd that "
                                "--model would make, as JSON, and make no call. Without "
                                "--model the bill is zero")
    routing_p.add_argument("--out", default=None)
    add_spend_cap(routing_p)

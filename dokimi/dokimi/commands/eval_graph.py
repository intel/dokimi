# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval graph``: build the need graph ``eval coverage`` scores against, then freeze it (#331).

The graph is built from the skill's name and description only; the body is read off the
disk with the frontmatter but never reaches the model. The written file is the frozen
input: the command refuses to overwrite it, since every ``needs`` label points at its ids.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE
from dokimi.utils import InputError


def cmd_eval_graph(args: argparse.Namespace) -> None:
    """Write a need_graph.v1 file; exit 2 on a bad input or an existing --out, 1 on an empty graph."""
    from dokimi.artifacts import save_artifact
    from dokimi.commands.novelty_run import spend_cap
    from dokimi.novelty.artifact import load_artifact_ref
    from dokimi.novelty.client import LLMClient, SpendCapReached
    from dokimi.novelty.needs import PROMPT_VERSION, build_need_graph

    out = Path(args.out)
    if out.exists() or out.is_symlink():
        raise InputError(f"{out} exists; a need graph is frozen once written (delete it to rebuild)")
    _kind, name, description, _frontmatter, _body = load_artifact_ref(_as_skill(args.artifact))
    if not description.strip():
        raise InputError(f"{args.artifact} has no description to build a need graph from")
    client = LLMClient(model=args.model, provider=args.provider, role="graph",
                       max_tokens=args.max_tokens, cap=spend_cap(args))
    try:
        graph = build_need_graph(name, description, client=client, strict=True)
    except SpendCapReached:
        raise
    except Exception as exc:  # an unreachable or unauthenticated model: not "an empty graph"
        print(f"Error: the graph-author call failed: {exc}; nothing was written", file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    if not graph.nodes:
        print(f"Error: the model returned no usable need node for {name}; nothing was written",
              file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    graph.provenance = {"model": args.model, "prompt_version": PROMPT_VERSION, "draws": 1}
    try:
        save_artifact("need_graph", graph.as_dict(), out)
    except OSError as exc:
        print(f"Error: cannot write {out}: {exc.strerror or exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    counts = graph.summary()["by_path_class"]
    print(f"{out}: {len(graph.nodes)} nodes ("
          + ", ".join(f"{c} {n}" for c, n in counts.items() if n) + ")")
    print("Every node is a hypothesis: review the file, then label the evals' `needs` with its ids.")


def _as_skill(ref: str) -> str:
    """A bare skill directory or SKILL.md is a ``skill:`` ref; a text artifact has no description."""
    if ref.startswith(("skill:", "text:")):
        return ref
    path = Path(ref)
    return f"skill:{ref}" if path.is_dir() or path.name == "SKILL.md" else ref


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {number}")
    return number


def register(eval_sub) -> None:
    """Add ``eval graph`` to the ``eval`` group."""
    from dokimi.commands.novelty_run import add_spend_cap, stops_at_cap
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
    from dokimi.llm import ANSWER_PROVIDERS

    p = eval_sub.add_parser(
        "graph",
        help="Build the need graph for `eval coverage` from a skill's name and description (one model call)",
    )
    p.add_argument("--artifact", required=True,
                   help="Skill directory, SKILL.md or skill:<ref>; only its name and description are sent")
    p.add_argument("--out", required=True,
                   help="need_graph.v1 file to write; refused when it exists (the graph is frozen)")
    p.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                   help=f"Graph author model (default: {DEFAULT_JUDGE_MODEL})")
    p.add_argument("--provider", default=DEFAULT_JUDGE_PROVIDER, choices=ANSWER_PROVIDERS)
    p.add_argument("--max-tokens", type=_positive, default=None, dest="max_tokens")
    add_spend_cap(p)
    p.set_defaults(func=stops_at_cap(cmd_eval_graph))

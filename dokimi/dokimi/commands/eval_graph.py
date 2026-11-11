# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval graph``: build the need graph ``eval coverage`` scores against, then freeze it (#331).

The graph is built from the skill's name and description only; the body is read off the
disk with the frontmatter but never reaches the model. The written file is the frozen
input: the command never replaces an existing file, since every ``needs`` label points at
its ids.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_FAILURE
from dokimi.utils import InputError


def _frozen(out: Path) -> InputError:
    return InputError(f"{out} exists; a need graph is frozen once written (delete it to rebuild)")


def _publish(graph: dict, out: Path) -> None:
    """Validate and write ``graph`` to a staged sibling, then publish it without replacing ``out``.

    ``os.link`` fails when ``out`` exists, so a file that appeared during the model call
    (another run, an editor) is refused rather than replaced. A filesystem with no hard
    links (vfat, some SMB and FUSE mounts) gets an ``O_EXCL`` create of the same bytes.
    """
    from dokimi.artifacts import save_artifact

    staged = out.with_name(f".{out.name}.{os.getpid()}.staged")
    try:
        save_artifact("need_graph", graph, staged)
        try:
            os.link(staged, out)
        except FileExistsError:
            raise
        except OSError:
            payload = staged.read_bytes()
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
            except BaseException:  # a half-written file would be frozen and unreadable
                out.unlink(missing_ok=True)
                raise
    except FileExistsError:
        raise _frozen(out) from None
    except OSError as exc:
        raise InputError(f"cannot write {out}: {exc.strerror or exc}") from exc
    finally:
        staged.unlink(missing_ok=True)


def cmd_eval_graph(args: argparse.Namespace) -> None:
    """Write a need_graph.v1 file; exit 2 on a bad input or an existing --out, 1 on a failed or empty build."""
    from dokimi.commands.novelty_run import spend_cap
    from dokimi.novelty.artifact import load_artifact_ref
    from dokimi.novelty.client import LLMClient, SpendCapReached
    from dokimi.novelty.needs import PROMPT_VERSION, build_need_graph

    out = Path(args.out)
    try:
        present = out.exists() or out.is_symlink()
    except OSError as exc:  # a parent this user cannot search
        raise InputError(f"cannot stat {out}: {exc.strerror or exc}") from exc
    if present:
        raise _frozen(out)
    _kind, name, description, _frontmatter, _body = load_artifact_ref(_as_skill(args.skill))
    if not description.strip():
        raise InputError(f"{args.skill} has no description to build a need graph from")
    try:  # before the paid call, not after it: a missing directory is made, as save_artifact does
        out.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(out.parent, os.W_OK | os.X_OK)
    except OSError as exc:
        raise InputError(f"cannot write to {out.parent}: {exc.strerror or exc}") from exc
    if not writable:
        raise InputError(f"cannot write to {out.parent}: checked before the paid call")
    from dokimi.llm import PROVIDER_ALIASES

    provider, model = args.extract_model
    provider = PROVIDER_ALIASES.get(provider, provider)  # provenance records gemini/x as google-vertex/x
    client = LLMClient(model=model, provider=provider, role="graph", cap=spend_cap(args))
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
    graph.provenance = {"model": f"{provider}/{model}", "prompt_version": PROMPT_VERSION, "draws": 1}
    _publish(graph.as_dict(), out)
    counts = graph.summary()["by_path_class"]
    print(f"{out}: {len(graph.nodes)} nodes ("
          + ", ".join(f"{c} {n}" for c, n in counts.items() if n) + ")")
    print("Every node is a hypothesis: review the file, then label the evals' `needs` with its ids.")


def _as_skill(ref: str) -> str:
    """A bare path is a ``skill:`` ref: a text artifact has no description to build from."""
    return ref if ref.startswith("skill:") else f"skill:{ref}"


def register(eval_sub) -> None:
    """Add ``eval graph`` to the ``eval`` group."""
    from dokimi.commands.novelty_run import add_spend_cap, stops_at_cap
    from dokimi.commands.repo_hits import _judge_ref as model_ref
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER

    default = f"{DEFAULT_JUDGE_PROVIDER}/{DEFAULT_JUDGE_MODEL}"
    p = eval_sub.add_parser(
        "graph",
        help="Build the need graph for `eval coverage` from a skill's name and description (one model call)",
    )
    p.add_argument("skill", help="Skill directory or SKILL.md; only its name and description are sent")
    p.add_argument("--out", required=True,
                   help="need_graph.v1 file to write; refused when it exists (the graph is frozen)")
    p.add_argument("--extract-model", type=model_ref, default=model_ref(default), dest="extract_model",
                   metavar="PROVIDER/MODEL", help=f"Graph author (default: {default})")
    add_spend_cap(p)
    p.set_defaults(func=stops_at_cap(cmd_eval_graph))

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
import errno
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dokimi.exit_codes import EXIT_FAILURE
from dokimi.utils import InputError


def _frozen(out: Path) -> InputError:
    return InputError(f"{out} exists; a need graph is frozen once written (delete it to rebuild)")


def _publish(graph: dict, out: Path) -> None:
    """Validate and write ``graph`` to a staged sibling, then publish it without replacing ``out``.

    ``os.link`` fails when ``out`` exists, so a file that appeared during the model call
    (another run, an editor) is refused rather than replaced. A filesystem with no hard
    links (vfat, some SMB and FUSE mounts) is refused with exit 2 and ``out`` is never
    created there: a create-then-write could be killed half-way, and the truncated file
    would be frozen, so every later run refused it.

    The staging happens in a directory made fresh for this write, so its name cannot be
    guessed and planted: a symlink at a predictable staging name would send the write to
    an unrelated file in a shared directory.
    """
    from dokimi.artifacts import ArtifactValidationError, save_artifact

    stage_dir = None
    keep_stage = False  # a rejected payload is kept beside the staged file, and the message names it
    try:
        stage_dir = Path(tempfile.mkdtemp(prefix=".eval-graph-", dir=out.parent))
        staged = stage_dir / out.name
        save_artifact("need_graph", graph, staged)
        try:
            os.link(staged, out)
        except FileExistsError:
            raise
        except OSError as exc:
            raise InputError(
                f"cannot write {out}: no hard links on this filesystem, so the graph is not "
                f"published there ({exc.strerror or exc}); write it to a filesystem with hard links"
            ) from exc
    except ArtifactValidationError:
        keep_stage = True
        raise
    except FileExistsError:
        raise _frozen(out) from None
    except OSError as exc:
        raise InputError(f"cannot write {out}: {exc.strerror or exc}") from exc
    finally:
        if stage_dir is not None and not keep_stage:
            shutil.rmtree(stage_dir, ignore_errors=True)


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
    try:
        _kind, name, description, _frontmatter, _body = load_artifact_ref(_as_skill(args.skill))
    except InputError as exc:  # the loader's message names --artifact, which this command does not have
        raise InputError(f"cannot load skill {args.skill}: {str(exc).partition(': ')[2] or exc}") from exc
    try:  # before the paid call, not after it: a missing directory is made, as save_artifact does
        out.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(out.parent, os.W_OK | os.X_OK)
    except OSError as exc:
        raise InputError(f"cannot write to {out.parent}: {exc.strerror or exc}") from exc
    if not writable:
        raise InputError(f"cannot write to {out.parent}: checked before the paid call")
    _check_hard_links(out)
    from dokimi.llm import PROVIDER_ALIASES

    provider, model = args.extract_model
    provider = PROVIDER_ALIASES.get(provider, provider)  # provenance records gemini/x as google-vertex/x
    client = LLMClient(model=model, provider=provider, role="graph", cap=spend_cap(args))
    try:
        graph = build_need_graph(name, description, client=client, strict=True)
    except SpendCapReached as exc:
        if exc.reason != "no_price" or not (exc.reply or "").strip():
            raise
        # The call is paid for and its reply is the graph, as measure.py keeps such a reply.
        print(f"Warning: no price is known for {provider}/{model}, so this call could not be metered; "
              "its reply is the graph", file=sys.stderr)
        graph = _graph_from_unmetered(name, description, exc.reply)
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


_NO_HARD_LINKS = {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}


def _check_hard_links(out: Path) -> None:
    """Refuse a filesystem with no hard links before the paid call: the graph is published by one."""
    probe = Path(tempfile.mkdtemp(prefix=".eval-graph-probe-", dir=out.parent))
    try:
        (probe / "a").write_bytes(b"")
        os.link(probe / "a", probe / "b")
    except OSError as exc:
        if exc.errno in _NO_HARD_LINKS:
            raise InputError(
                f"cannot write {out}: no hard links on this filesystem, so the graph is not "
                f"published there ({exc.strerror or exc}); write it to a filesystem with hard links"
            ) from exc
        raise InputError(f"cannot write {out}: {exc.strerror or exc}") from exc
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def _graph_from_unmetered(name: str, description: str, reply: str):
    """The graph of a paid reply whose call had no known price (the reply is kept, as measure.py keeps it)."""
    from dokimi.novelty.client import parse_json_object
    from dokimi.novelty.needs import graph_from_reply

    try:
        data = parse_json_object(reply)
    except ValueError:  # not a JSON object: no node, so the caller refuses it
        data = {}
    return graph_from_reply(name, description, data)


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

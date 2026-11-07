# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval tasks generate: build repository tasks from a source (contract §4.7).

``--from graph`` reads a SCIP code index; ``--from replay`` reads a threads file of merged pull requests
(the former ``repo tasks replay``, #94 D11 and M3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.repos.graph_tasks import Plan

SOURCES = ("graph", "replay")
SOURCE_HELP = ("Repository source pinned at the revision the index was built from: <dir>@<rev> or "
               "<https-or-ssh-url>@<rev> (cloned bare into the cache). The revision's full SHA is each task's base")


def cmd_eval_tasks_generate(args: argparse.Namespace) -> None:
    """Build tasks from ``--from``: graph (enumeration tasks, gold from a SCIP index) or replay (review threads)."""
    if args.from_kind == "replay":
        _check_replay_flags(args)
        from .repo_run import cmd_repo_tasks_replay

        return cmd_repo_tasks_replay(args)
    if args.threads is not None:
        raise InputError("--threads is for --from replay")
    _generate_graph(args)


def _check_replay_flags(args: argparse.Namespace) -> None:
    """``--from replay`` reads a threads file and no code index."""
    if args.threads is None:
        raise InputError("--from replay needs --threads FILE, the merged pull requests with their reviewThreads")
    if args.index is not None:
        raise InputError("--index is for --from graph")
    if args.dry_run:
        raise InputError("--dry-run is for --from graph")


def _generate_graph(args: argparse.Namespace) -> None:
    """Enumeration tasks with gold from a SCIP index (no LLM, no docker)."""
    from dokimi.repos.graph_tasks import Options, plan, write
    from dokimi.repos.source import GitTree

    from .repo import _code_index, _normalize_whitespace, _source_tree

    if not args.index:
        raise InputError("--from graph needs --index FILE, a SCIP index of the source")
    if not args.references and not args.derives:
        raise InputError("--references 0 --derives 0 asks for no task")
    index = _code_index(args.index)  # before the source: a bad index must not cost a clone first
    _, tree = _source_tree(args)
    if not isinstance(tree, GitTree):
        raise InputError(f"pin the revision the index was built from: {args.source}@<rev>; a task's base is a commit")
    opts = Options(out=Path(args.out), base=tree.sha, references=args.references, derives=args.derives, seed=args.seed,
                   max_turns=args.max_turns, force=args.force)
    p = plan(index, tree.files(), opts, tree.read_text)
    if not p.documents_in_tree:
        raise InputError(f"no document of the code index {args.index} is a file of {tree.label}")
    _print_coverage(p)
    if not args.dry_run:
        try:
            write(p, opts, {"tool": index.tool, "sha256": _sha256(args.index)})
        except OSError as exc:
            print(f"✗ cannot write the tasks to {args.out}: {exc}", file=sys.stderr)
            sys.exit(1)
    for o in p.outcomes:
        c = o.candidate
        if o.status == "kept":  # what is on disk may come from another revision: say nothing about its gold
            print(f"kept {o.task}")
            continue
        print(f"{'would write' if args.dry_run else 'written'} {o.task}: {len(c.gold)} gold, "
              f"`{_normalize_whitespace(c.qualified, max_len=None)}`"
              + (f" ({c.same_name} symbols of this name)" if c.ambiguous else ""))
    n = {k: sum(o.status == k for o in p.outcomes) for k in ("written", "kept")}
    verb = "to write" if args.dry_run else "written"
    print(f"{n['written']} task(s) {verb}, {n['kept']} kept; in {opts.out}")
    if not p.outcomes:
        print("✗ no target of the index has a gold list of the allowed size", file=sys.stderr)
        sys.exit(1)


def _print_coverage(p: Plan) -> None:
    print(f"{p.documents_in_tree}/{p.documents} index documents in the tree"
          + (f" under {p.root_prefix}" if p.root_prefix else "")
          + f"; {p.granularity}; candidates: {p.candidates['references']} references, {p.candidates['derives']} derives")
    if p.unindexed:
        print(f"⚠ {len(p.unindexed)} tree file(s) of the indexed kinds are not in the index (first: "
              f"{p.unindexed[0]}): {p.dropped} target(s) whose name is a word in one dropped, as a gold could miss "
              "the file; index the whole tree", file=sys.stderr)


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got '{value}'") from None
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, not {value}")
    return n


def add_parser(eval_tasks_sub, positive_int) -> None:
    gen_p = eval_tasks_sub.add_parser(
        "generate", help="Write repository tasks from a source, no LLM: --from graph writes dokimi:graph_enum "
        "tasks (which files or functions reference a function, which classes derive from a class) with gold "
        "from a SCIP code index; --from replay writes one dokimi:review_replay task per merged pull request of a "
        "threads file. Exits 2 on a usage error or an index that does not read or matches no file "
        "of the source, 1 when no target gave a task, a task cannot be written or the source cannot be resolved at "
        "the revision")
    gen_p.add_argument("source", help=SOURCE_HELP)
    gen_p.add_argument("--from", required=True, choices=SOURCES, dest="from_kind",
                       help="What the tasks are built from: graph (a code index) or replay (a threads file)")
    gen_p.add_argument("--threads", default=None, metavar="FILE",
                       help="--from replay: JSON holding the merged pull requests with their reviewThreads, as the "
                       "query in the contract (§4.6) returns them; pages of --paginate --slurp are read too")
    gen_p.add_argument("--manifest", default=".agent-eval/repo-eval.yaml",
                       help="--from replay: manifest path relative to the repository root: its guidance globs and "
                       "eval_dir are left out of every patch")
    gen_p.add_argument("--manifest-file", default=None, dest="manifest_file",
                       help="--from replay: read the manifest from this local file instead of from the tree")
    gen_p.add_argument("--window", type=positive_int, default=5,
                       help="--from replay: task.json `window`, and how near a thread's line a later commit of the PR "
                       "must change the file for `addressed` (default 5)")
    gen_p.add_argument("--prompt-file", default=None, dest="prompt_file",
                       help="--from replay: the agent's prompt (default: a review prompt asking for the findings JSON)")
    gen_p.add_argument("--index", default=None, metavar="FILE",
                       help="A SCIP code index of the source at its revision (scip-python, scip-clang, ...); "
                       "dokimi never builds one")
    gen_p.add_argument("--out", required=True,
                       help="Directory that gets one <task id>/ per task, usually the manifest's `tasks`")
    gen_p.add_argument("--references", type=_count, default=9, metavar="N",
                       help="Reference tasks to write (default 9); about half with a name other symbols share")
    gen_p.add_argument("--derives", type=_count, default=3, metavar="N",
                       help="Direct-subclass tasks to write (default 3)")
    gen_p.add_argument("--seed", type=int, default=7, help="Seed of the choice among the candidates (default 7)")
    gen_p.add_argument("--max-turns", type=positive_int, default=40, dest="max_turns",
                       help="task.json max_turns (default 40)")
    gen_p.add_argument("--force", action="store_true",
                       help="Rewrite a task directory that has a task.json (default: keep it and its edits)")
    gen_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print the chosen tasks without writing them")
    gen_p.add_argument("--cache-dir", default=None, dest="cache_dir",
                       help="Clone cache directory (default: $DOKIMI_REPO_CACHE or ~/.cache/dokimi/repos)")
    gen_p.set_defaults(func=cmd_eval_tasks_generate)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval tasks generate: build repository tasks from a source (contract §4.7). ``--from graph`` reads a code index."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_USAGE

SOURCES = ("graph",)
SOURCE_HELP = ("Repository source pinned at the revision the index was built from: <dir>@<rev> or "
               "<https-or-ssh-url>@<rev> (cloned bare into the cache). The revision's full SHA is each task's base")


def cmd_eval_tasks_generate(args: argparse.Namespace) -> None:
    """Enumeration tasks with gold from a SCIP index (no LLM, no docker)."""
    from dokimi.repos.graph_tasks import Options, plan, write
    from dokimi.repos.source import GitTree

    from .repo import _code_index, _normalize_whitespace, _source_tree

    if not args.index:
        print("✗ --from graph needs --index FILE, a SCIP index of the source", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if not args.references and not args.derives:
        print("✗ --references 0 --derives 0 asks for no task", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    index = _code_index(args.index)  # before the source: a bad index must not cost a clone first
    _, tree = _source_tree(args)
    if not isinstance(tree, GitTree):
        print(f"✗ pin the revision the index was built from: {args.source}@<rev>; a task's base is a commit",
              file=sys.stderr)
        sys.exit(EXIT_USAGE)
    opts = Options(out=Path(args.out), base=tree.sha, references=args.references, derives=args.derives, seed=args.seed,
                   max_turns=args.max_turns, force=args.force)
    p = plan(index, tree.files(), opts)
    if not p.documents_in_tree:
        print(f"✗ no document of the code index {args.index} is a file of {tree.label}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    print(f"{p.documents_in_tree}/{p.documents} index documents in the tree"
          + (f" under {p.root_prefix}" if p.root_prefix else "")
          + f"; {p.granularity}; candidates: {p.candidates['references']} references, {p.candidates['derives']} derives")
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
        "from a SCIP code index. Exits 2 on a usage error or an index that does not read or matches no file "
        "of the source, 1 when no target gave a task, a task cannot be written or the source cannot be resolved at "
        "the revision")
    gen_p.add_argument("source", help=SOURCE_HELP)
    gen_p.add_argument("--from", required=True, choices=SOURCES, dest="from_kind",
                       help="What the tasks are built from: graph (a code index)")
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

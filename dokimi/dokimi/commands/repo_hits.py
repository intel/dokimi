# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo hits judge / repo hits sheet: judged recall over the credited pairs of a batch's replay cells
(contract §4.5, §9; https://github.com/intel/dokimi/issues/291)."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, NoReturn

from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE

from ._output import exit_on_unwritable


def _judge_ref(text: str) -> tuple[str, str]:
    """``PROVIDER/MODEL`` as ``(provider, model)``; the model may hold more slashes (``openrouter/a/b``)."""
    from dokimi.llm import ANSWER_PROVIDERS, PROVIDER_ALIASES

    provider, sep, model = text.partition("/")
    known = (*ANSWER_PROVIDERS, *PROVIDER_ALIASES)
    if not sep or not model.strip() or provider not in known:
        raise argparse.ArgumentTypeError(f"takes PROVIDER/MODEL, PROVIDER one of {', '.join(known)}; got {text!r}")
    return provider, model


def _batch(args: argparse.Namespace) -> Path:
    batch_dir = Path(args.batch_dir)
    if not batch_dir.is_dir():
        print(f"✗ not a directory: {batch_dir}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    return batch_dir


def _read(batch_dir: Path, args: argparse.Namespace) -> tuple[dict[str, Any], list[Any], Any]:
    """The batch's pairs, the judge checked against its answer models, and the labels; exit 2 on a bad input."""
    from dokimi.repos.hits_calibration import read_labels
    from dokimi.repos.hits_judge import HitsInputError, answer_models, check_judge, read_pairs
    from dokimi.repos.report import CellFileError

    try:
        found, pairs = read_pairs(batch_dir)
        check_judge(args.judge, answer_models(batch_dir))
        labels = read_labels(Path(args.labels), pairs) if args.labels else None
    except (CellFileError, HitsInputError) as exc:  # before any judge call
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    return found, pairs, labels


def _stopped(exc: Exception, done: Any, code: int, more: str = "") -> NoReturn:
    """A ``✗`` line for a run :func:`.hits_judge.judge_cells` stopped: the calls made, their cost, what stays."""
    from dokimi.repos.hits_judge import HITS_JUDGED

    print(f"✗ {exc}; stopped there after {done.meter.calls} judge call(s), {done.meter.spend()}; "
          f"the {len(done.docs)} cell(s) written before keep their {HITS_JUDGED}{more}", file=sys.stderr)
    sys.exit(code)


@contextlib.contextmanager
def _locked(batch_dir: Path) -> Iterator[None]:
    """``repo run``'s :func:`.runner.batch_lock` while the run reads and writes the cells; exit 2 when another
    holds it (``repo run`` rewrites ``_eval/content`` and regrades cells) or it cannot be taken."""
    from dokimi.repos.runner import BatchBusy, batch_lock

    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(batch_lock(batch_dir))
        except BatchBusy as exc:
            print(f"✗ {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        except OSError as exc:  # a read-only batch: an output that cannot be written, as `sheet`'s
            print(f"✗ cannot lock {batch_dir / '_eval' / 'lock'}: {exc.strerror or exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        yield


def cmd_repo_hits_judge(args: argparse.Namespace) -> None:
    """Label each credited replay pair with a judge model; write each cell's hits_judged.json."""
    from dokimi.repos.hits_judge import estimate, format_estimate

    batch_dir = _batch(args)
    if args.dry_run:  # writes nothing: no lock
        found, pairs, _ = _read(batch_dir, args)
        planned = estimate(found, pairs, args.judge)
        print(json.dumps(planned, indent=2) if args.json else format_estimate(planned))
        return
    with _locked(batch_dir):  # before the pairs are read, so a `repo run` cannot change them mid-run
        _judge(batch_dir, args)


def _judge(batch_dir: Path, args: argparse.Namespace) -> None:
    from dokimi.repos.hits_calibration import calibrate
    from dokimi.repos.hits_judge import HitsWriteError, JudgeFailing, format_summary, judge_cells, summary

    found, pairs, labels = _read(batch_dir, args)
    try:
        done = judge_cells(batch_dir, found, pairs, args.judge, args.max_usd)
    except HitsWriteError as exc:  # an output write, as `sheet`'s: exit 2
        _stopped(exc, exc.done, EXIT_USAGE)
    except JudgeFailing as exc:  # the provider refuses every call (an auth error, a missing key): exit 1
        _stopped(exc, exc.done, EXIT_FAILURE, ", and the cell in progress gets none")
    calibration = calibrate(labels, done.labels(), pairs) if labels is not None else None
    result = summary(found, pairs, args.judge, done, calibration)
    print(json.dumps(result, indent=2) if args.json else format_summary(result))
    if done.stopped:  # as repo run: a cap that left work undone is not a finished run
        sys.exit(EXIT_FAILURE)


def cmd_repo_hits_sheet(args: argparse.Namespace) -> None:
    """Sample credited replay pairs, stratified by arm and answer model, for a person to label (no LLM)."""
    from dokimi.repos.hits_calibration import labelled_entries, sheet
    from dokimi.repos.hits_judge import HitsInputError, read_pairs
    from dokimi.repos.report import CellFileError

    batch_dir = _batch(args)
    out = Path(args.out)
    try:
        _, pairs = read_pairs(batch_dir)
        labelled = labelled_entries(out)  # an existing file that cannot be read is not replaced
    except (CellFileError, HitsInputError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    entries = sheet(pairs, args.per_arm, args.seed)
    if labelled:  # a person's labels are not replaced by a fresh sheet
        print(f"✗ {out} holds a sheet with {labelled} labelled entr{'y' if labelled == 1 else 'ies'}; move it aside "
              "or pass another --out", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    with exit_on_unwritable(out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    left = [p for p in pairs if p.problem is not None]
    if left:
        print(f"{len(left)} pair(s) left out, full text not found (see `repo hits judge`)", file=sys.stderr)
    per_row = Counter((e["arm"], e["model"]) for e in entries)
    print(f"wrote {len(entries)} pair(s) to label to {out}"
          + "".join(f"; {arm} ({model}): {n} of {sum(p.row == (arm, model) and p.problem is None for p in pairs)}"
                    for (arm, model), n in sorted(per_row.items())))


def register(repo_sub, positive_int, usd: Callable[[str], float]) -> None:
    hits_p = repo_sub.add_parser(
        "hits", help="Judged recall over the credited pairs of dokimi:review_replay cells: a judge's labels, "
        "and a sample of the pairs for a person to label")
    hits_sub = hits_p.add_subparsers(dest="hits_cmd", required=True)

    judge_p = hits_sub.add_parser(
        "judge", help="Label each credited (thread, finding) pair same point, related or unrelated with a judge "
        "model, reading the full texts; write <cell>/hits_judged.json with recall_judged (<= recall)")
    judge_p.add_argument("batch_dir", help="<out>/<batch> of `repo run`")
    judge_p.add_argument("--judge", required=True, type=_judge_ref, metavar="PROVIDER/MODEL",
                         help="The judge; refused when it runs the model of any answer of the batch")
    judge_p.add_argument("--labels", default=None, metavar="FILE",
                         help="A `repo hits sheet` file with `label` filled in: report agreement and Cohen's kappa "
                         "per arm and answer model between the person and the judge")
    judge_p.add_argument("--max-usd", type=usd, default=None, dest="max_usd", metavar="X",
                         help="Start no new judge call once this much has been spent (exit 1 if a cell was left); "
                         "after a judge reply with no known price no further call starts, since the cap cannot "
                         "be enforced (drop --max-usd to run unmetered)")
    judge_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Count the pairs and estimate the cost at list price; make no call")
    judge_p.add_argument("--json", action="store_true", help="Print the summary as JSON")
    judge_p.set_defaults(func=cmd_repo_hits_judge)

    sheet_p = hits_sub.add_parser(
        "sheet", help="Write a JSON list of credited pairs with their full texts and `label: null` for a person "
        "to label, N per arm and answer model (no LLM)")
    sheet_p.add_argument("batch_dir", help="<out>/<batch> of `repo run`")
    sheet_p.add_argument("--out", required=True, metavar="FILE",
                         help="The sheet to write; refused (exit 2) when FILE holds a sheet with a label set or "
                         "cannot be read")
    sheet_p.add_argument("--per-arm", type=positive_int, default=25, dest="per_arm", metavar="N",
                         help="Pairs per arm and answer model, all of theirs when they have fewer (default 25)")
    sheet_p.add_argument("--seed", type=int, default=0, metavar="N",
                         help="Seed of the draw: the same seed gives the same sheet (default 0)")
    sheet_p.set_defaults(func=cmd_repo_hits_sheet)

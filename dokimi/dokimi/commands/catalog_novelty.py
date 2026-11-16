# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog novelty ROOT``: the corpus novelty sweep, one ``skill novelty analyze`` per skill.

Every ``SKILL.md`` under ROOT is analysed with the flags of ``skill novelty analyze``, and the
model calls are paid. ``--max-usd`` is one budget for the whole sweep and ``--max-usd-per-skill``
a cap on each skill; ``--jobs`` runs skills in parallel threads that share the budget. Each skill
writes one row to ``--jsonl`` as it finishes, so a killed sweep keeps its rows, and ``--resume``
runs only the skills without a measured row (same directory digest, same settings).
``--summary-only`` prints the summary of a JSONL file without any model call.

A skill the budget stops mid-analysis is a ``partial`` row and leaves no novelty file. Set
``DOKIMI_RESPONSE_CACHE`` so a resumed run replays the calls already paid for at $0.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from dokimi import __version__
from dokimi.commands.novelty_run import stops_at_cap
from dokimi.novelty.sweep import read_rows, resumable, summarize, summary_lines
from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.novelty.client import SpendCap

#: The JSONL file a sweep writes unless ``--jsonl`` names another.
DEFAULT_JSONL = "sweep.jsonl"


@dataclass(frozen=True)
class _Item:
    """One skill of the root: its key (its directory relative to ROOT), SKILL.md, digest and load error."""

    key: str
    name: str
    skill_md: Path
    sha256: Optional[str]
    error: Optional[str] = None  # a SKILL.md that does not load: its row is an error, and no call is made

    @property
    def directory(self) -> Path:
        return self.skill_md.parent


def _items(root: Path, limit: Optional[int]) -> list[_Item]:
    """The skills under *root* in path order, the first *limit* of them."""
    from dokimi.skills.loader import load_skill, skill_files
    from dokimi.utils import tree_digest

    items: list[_Item] = []
    for skill_md in skill_files(root):
        directory = skill_md.parent
        error: Optional[str] = None
        try:
            name = load_skill(skill_md).name
        except (OSError, ValueError, RecursionError) as exc:
            name, error = directory.name, str(exc)
        try:
            sha: Optional[str] = tree_digest(directory, tracked={})
        except OSError:
            sha = None
        items.append(_Item(key=directory.relative_to(root).as_posix(), name=name, skill_md=skill_md,
                           sha256=sha, error=error))
        if limit is not None and len(items) == limit:
            break
    return items


def _dirname(key: str) -> str:
    """A directory name for one skill's outputs: readable, and unique even when two keys read alike."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_") or "root"
    return f"{readable}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}"


def _fingerprint(args: argparse.Namespace, placebo_text: Optional[str]) -> dict[str, Any]:
    """The settings a measured row was made under; a resumed run keeps a row only when these are unchanged."""
    from dokimi.commands.novelty import _settings
    from dokimi.novelty import cost

    fingerprint = {
        "roles": {role: list(pair) for role, pair in cost.role_models(args).items()},
        "settings": dict(_settings(args).values),
        "arms": args.arms,
        "budgets": list(args.budgets),
        "max_atoms": args.max_atoms,
        "seed": args.seed,
        "max_tokens": args.max_tokens,
        "context_framing": args.context_framing,
        "include_resources": args.include_resources,
        "resource_roles": args.resource_roles,
        "no_paraphrase": args.no_paraphrase,
        "no_reversal": args.no_reversal,
        "allow_self_judge": args.allow_self_judge,
        "max_file_words": args.max_file_words,
        "max_resource_words": args.max_resource_words,
        "placebo_text": hashlib.sha256(placebo_text.encode("utf-8")).hexdigest() if placebo_text else None,
    }
    return json.loads(json.dumps(fingerprint, default=str))


def _analyze_skill(args: argparse.Namespace, placebo_text: Optional[str], cache_dir: Optional[Path]):
    """One skill through the analyze pipeline, as ``cmd_novelty_analyze`` runs it, without the writes.

    Returns the :class:`~dokimi.novelty.analyze.AnalyzeResult`; the caller decides what to save.
    """
    from dokimi.commands.novelty import _arms, _bundle_from_args, _settings, stamp
    from dokimi.novelty import cost
    from dokimi.novelty.analyze import AnalyzeConfig, analyze

    bundle = _bundle_from_args(args)
    settings = _settings(args)
    extractor, answerer, judge = cost.clients(args)
    config = AnalyzeConfig(
        artifact_ref=args.artifact, artifact_kind=bundle.kind, name=bundle.name,
        description=bundle.description, frontmatter=bundle.frontmatter, arms=_arms(args.arms),
        budgets=tuple(args.budgets), concurrency=args.concurrency,
        allow_self_judge=args.allow_self_judge, placebo_text=placebo_text,
        context_framing=args.context_framing, cache_dir=cache_dir, seed=args.seed,
        include_resources=args.include_resources,
        resource_roles=tuple(args.resource_roles) if args.resource_roles else None,
        **settings.config_fields(),
    )
    result = analyze(bundle.text, config, extractor=extractor, answerer=answerer, judge=judge,
                     artifact_bundle=bundle)
    if not result.partial:
        stamp(result.artifact, args, result.measure_result, answerer=answerer, judge=judge)
    return result


class _Sink:
    """The JSONL file: one flushed line per row. A resumed run rewrites it with the kept rows first."""

    def __init__(self, path: Path, kept: list[dict], resume: bool) -> None:
        self.path = path
        try:
            if resume:
                tmp = path.with_name(path.name + ".part")
                with tmp.open("w", encoding="utf-8") as fh:
                    for row in kept:
                        fh.write(json.dumps(row) + "\n")
                tmp.replace(path)
            self._fh = path.open("a" if resume else "w", encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot write --jsonl {path}: {exc}") from exc

    def write(self, row: dict) -> None:
        self._fh.write(json.dumps(row) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _run_sweep(items: list[_Item], *, jobs: int, review: Callable[[_Item], Optional[dict]],
               write: Callable[[dict], None], out_of_budget: Callable[[], bool]) -> None:
    """Review the items with up to *jobs* threads, handing each row to *write* as it finishes.

    ``review`` returns None for a skill it did not run (the budget was spent before it started);
    no new skill is submitted once ``out_of_budget()`` holds. A worker's unexpected failure
    propagates after the rows finished so far are written.
    """
    pending = iter(items)
    running: set = set()
    with ThreadPoolExecutor(max_workers=jobs) as pool:

        def fill() -> None:
            while len(running) < jobs * 2 and not out_of_budget():
                item = next(pending, None)
                if item is None:
                    return
                running.add(pool.submit(review, item))

        fill()
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                running.discard(future)
                row = future.result()
                if row is not None:
                    write(row)
            fill()


def _review_one(item: _Item, *, args: argparse.Namespace, run_cap: "SpendCap", per_skill: Optional[float],
                placebo_text: Optional[str], cache_dir: Optional[Path], out_dir: Path,
                fingerprint: dict) -> Optional[dict]:
    """The row of one skill: analyse it under its own cap and save its novelty file, or say why it has none."""
    from dokimi.artifacts import save_artifact
    from dokimi.novelty.atoms import ExtractionError
    from dokimi.novelty.client import NestedSpendCap, SpendCapReached
    from dokimi.novelty.measure import SelfJudgeError
    from dokimi.novelty.report import render_novelty_markdown
    from dokimi.novelty.sweep import make_row, measured_fields

    if run_cap.spent_usd >= run_cap.limit_usd:
        return None
    cap = NestedSpendCap(limit_usd=per_skill if per_skill is not None else math.inf, parent=run_cap)

    def row(status: str, **fields: Any) -> dict:
        return make_row(key=item.key, name=item.name, path=str(item.skill_md),
                        skill_sha256=item.sha256, status=status, settings=fingerprint,
                        version=__version__, usd=cap.spent_usd, calls=cap.calls, **fields)

    if item.error is not None:
        return row("error", reason="not_loaded", message=item.error)
    skill_args = copy.copy(args)
    skill_args.artifact = f"skill:{item.directory}"
    skill_args._spend_cap = cap  # cost.spend_cap() hands this cap to every role client of the skill
    try:
        result = _analyze_skill(skill_args, placebo_text, cache_dir)
    except SpendCapReached as stop:  # a stop outside analyze's own partial record
        return row("partial", reason=stop.reason, message=str(stop))
    except (ExtractionError, SelfJudgeError, ValueError, InputError, OSError) as exc:
        return row("error", reason="error", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - one skill's failure is its row's error; the sweep goes on
        return row("error", reason="unexpected", message=f"{type(exc).__name__}: {exc}")
    if result.partial:
        return row("partial", reason=result.partial["reason"], message=result.partial["message"])
    measured = measured_fields(result.artifact)
    # k0 reads 0.0 when no baseline probe answered, so a zero pair count is the signal, not the share.
    if measured["new_lenient"] is None or not measured["n_paired"]:
        return row("error", reason="no_pairs", message="no probe was paired, so there is no share")
    novelty_json = out_dir / _dirname(item.key) / "novelty.json"
    try:
        save_artifact("novelty", result.artifact, novelty_json)
        novelty_json.with_suffix(".md").write_text(render_novelty_markdown(result.artifact), encoding="utf-8")
    except (OSError, ValueError) as exc:  # the paid analysis is done: a failed write is this skill's error
        return row("error", reason="error", message=f"could not save its novelty file: {exc}")
    return row("ok", measured=measured, novelty_json=str(novelty_json))


def _emit_summary(summary: dict, args: argparse.Namespace) -> None:
    for line in summary_lines(summary):
        print(line)
    if args.summary_out:
        Path(args.summary_out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"summary written to {args.summary_out}")


def cmd_catalog_novelty(args: argparse.Namespace) -> None:
    from dokimi.commands.inputs import placebo_text_for_arms_or_exit
    from dokimi.commands.novelty import _arms
    from dokimi.commands.novelty_run import usable_cache_dir
    from dokimi.novelty.client import SpendCap
    from dokimi.novelty.sweep import load_strata

    root = Path(args.root)
    if not root.is_dir():
        raise InputError(f"ROOT is not a directory: {root}")
    strata = load_strata(Path(args.strata), args.stratum_column) if args.strata else None
    out_dir = Path(args.out_dir)
    jsonl = Path(args.jsonl) if args.jsonl else out_dir / DEFAULT_JSONL
    if args.summary_only:
        _summary_only(jsonl, strata, args)
        return
    _refuse_unsafe_run(args, jsonl)
    placebo_text = placebo_text_for_arms_or_exit(args.placebo_text, _arms(args.arms))
    cache_dir = usable_cache_dir(args.cache_dir)
    items = _items(root, args.limit)
    if not items:
        raise InputError(f"no SKILL.md under {root}")
    fingerprint = _fingerprint(args, placebo_text)
    kept, todo, others = _plan(items, jsonl, args, fingerprint)
    print(f"# catalog novelty: {len(items)} skills under {root}; {len(kept)} measured and kept, "
          f"{len(todo)} to run", file=sys.stderr)
    run_cap = SpendCap(limit_usd=args.max_usd)
    per = f", ${args.max_usd_per_skill:g} per skill" if args.max_usd_per_skill else ""
    print(f"# budget ${args.max_usd:g} for the whole run{per}", file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    new_rows = _sweep_rows(todo, args=args, run_cap=run_cap, kept=kept, others=others, total=len(items),
                           jsonl=jsonl,
                           out_dir=out_dir, placebo_text=placebo_text, cache_dir=cache_dir,
                           fingerprint=fingerprint)
    _emit_summary(summarize(kept + new_rows, strata, resamples=args.bootstrap_resamples), args)


def _summary_only(jsonl: Path, strata, args: argparse.Namespace) -> None:
    rows = read_rows(jsonl)
    if not rows:
        raise InputError(f"--jsonl {jsonl} has no rows to summarise")
    _emit_summary(summarize(rows, strata, resamples=args.bootstrap_resamples), args)


def _refuse_unsafe_run(args: argparse.Namespace, jsonl: Path) -> None:
    """Refuse a paid run that would not be a clean one: a judge2 it cannot run, no budget,
    an overwrite of a sweep in progress, or a model flag that does not resolve."""
    from dokimi.novelty import cost

    if args.judge2:
        raise InputError("--judge2 is not part of catalog novelty: run it on one skill with skill novelty analyze")
    if args.max_usd is None:
        raise InputError("catalog novelty makes paid calls: pass --max-usd, the whole-run budget "
                         "(--max-usd-per-skill caps one skill inside it)")
    if jsonl.exists() and not args.resume:
        raise InputError(f"--jsonl {jsonl} exists: pass --resume to continue it, or remove it to start over")
    try:
        cost.role_models(args)
    except ValueError as exc:
        raise InputError(str(exc)) from exc


def _plan(items: list[_Item], jsonl: Path, args: argparse.Namespace,
          fingerprint: dict[str, Any]) -> tuple[list[dict], list[_Item], list[dict]]:
    """The measured rows a resumed run keeps, the skills it still has to run, and the prior rows of
    skills this run does not select (a narrower --limit, a moved skill): those stay in the file as they are."""
    selected = {item.key: item.sha256 for item in items}
    prior = read_rows(jsonl) if args.resume else []
    kept = resumable(prior, selected, fingerprint, __version__)
    done = {row["key"] for row in kept}
    others = [row for row in prior if row["key"] not in selected]
    return kept, [item for item in items if item.key not in done], others


def _sweep_rows(todo: list[_Item], *, args: argparse.Namespace, run_cap, kept: list[dict],
                others: list[dict], total: int, jsonl: Path, out_dir: Path, placebo_text: Optional[str], cache_dir: Optional[Path],
                fingerprint: dict[str, Any]) -> list[dict]:
    """Run the skills still to do, writing each row as it finishes; returns the rows this run made."""
    sink = _Sink(jsonl, others + kept, resume=args.resume)
    review = _bound(args, run_cap=run_cap, placebo_text=placebo_text, cache_dir=cache_dir,
                    out_dir=out_dir, fingerprint=fingerprint)
    new_rows: list[dict] = []

    def write(row: dict) -> None:
        sink.write(row)
        new_rows.append(row)
        detail = f" new {row['new_lenient']:.2f}" if row["status"] == "ok" else f" {row['reason']}"
        print(f"[{len(kept) + len(new_rows)}/{total}] {row['key']}: {row['status']}{detail} "
              f"${row['usd']:.3f}", file=sys.stderr)

    def out_of_budget() -> bool:
        return run_cap.spent_usd >= run_cap.limit_usd

    try:
        _run_sweep(todo, jobs=args.jobs, review=review, write=write, out_of_budget=out_of_budget)
    finally:
        sink.close()
    not_run = len(todo) - len(new_rows)
    if not_run:
        print(f"# {not_run} skill(s) not run: the budget was spent. Rerun with --resume and a higher "
              "--max-usd; set DOKIMI_RESPONSE_CACHE so the calls already paid replay at $0", file=sys.stderr)
    return new_rows


def _bound(args: argparse.Namespace, **shared: Any) -> Callable[[_Item], Optional[dict]]:
    """The per-skill review with this run's shared pieces bound: the arguments, the budget and the output dir."""
    per_skill = args.max_usd_per_skill

    def review(item: _Item) -> Optional[dict]:
        return _review_one(item, args=args, per_skill=per_skill, **shared)

    return review


def register(catalog_sub, positive_int) -> None:
    """Add ``catalog novelty``: the corpus sweep, a catalog verb like ``catalog routing``."""
    from dokimi.commands.novelty import _add_bundle_args, _add_model_args
    from dokimi.commands.novelty_run import usd_amount
    from dokimi.novelty.sweep import RESAMPLES
    from dokimi.novelty.tiers import TIERS

    p = catalog_sub.add_parser(
        "novelty",
        help="Corpus novelty sweep: analyze every skill under a catalog root under a budget, resumable, "
             "with bootstrap CIs per stratum [paid]",
    )
    p.add_argument("root", help="Catalog root: every SKILL.md under it is one skill")
    p.add_argument("--out-dir", required=True, dest="out_dir",
                   help="Where each skill's novelty.json and report go (OUT_DIR/<skill>/)")
    p.add_argument("--jsonl", default=None,
                   help=f"One row per skill, written as each finishes (default: OUT_DIR/{DEFAULT_JSONL})")
    p.add_argument("--jobs", type=positive_int, default=1,
                   help="Skills analysed in parallel threads, sharing --max-usd (default 1)")
    p.add_argument("--resume", action="store_true",
                   help="Keep the measured rows in --jsonl and run only the other skills")
    p.add_argument("--max-usd-per-skill", type=usd_amount, default=None, dest="max_usd_per_skill",
                   help="Stop one skill once its calls cost this much (USD); the rest of the sweep goes on")
    p.add_argument("--limit", type=positive_int, default=None,
                   help="Only the first N skills in path order (a pilot)")
    p.add_argument("--strata", default=None,
                   help="CSV with a `skill` column (the skill's directory under ROOT, or its name) and a "
                        "stratum column; the summary gets bootstrap CIs per stratum")
    p.add_argument("--stratum-column", default="stratum", dest="stratum_column",
                   help="The stratum column of --strata (default: stratum)")
    p.add_argument("--summary-only", action="store_true", dest="summary_only",
                   help="Print the summary of --jsonl without running anything")
    p.add_argument("--summary-out", default=None, dest="summary_out",
                   help="Also write the summary as JSON here")
    p.add_argument("--bootstrap-resamples", type=positive_int, default=RESAMPLES,
                   dest="bootstrap_resamples", help=f"Bootstrap resamples per interval (default {RESAMPLES})")
    p.add_argument("--tier", default=None, choices=sorted(TIERS),
                   help="Named cost tier for every skill (see skill novelty analyze --tier)")
    p.add_argument("--budgets", type=float, nargs="+", default=[0.75, 0.5],
                   help="Target token fractions for shortening candidates")
    p.add_argument("--max-atoms", type=positive_int, default=None, dest="max_atoms",
                   help="Atom cap per skill (see skill novelty analyze)")
    p.add_argument("--seed", type=int, default=0, help="Seed for the controls and the selection, per skill")
    p.add_argument("--cache-dir", default=None, dest="cache_dir",
                   help="Disk cache for retention adjudications")
    _add_model_args(p, positive_int)  # --model, --judge-model, ... and --max-usd: the whole-run budget
    _add_bundle_args(p, positive_int)
    p.set_defaults(func=stops_at_cap(cmd_catalog_novelty))

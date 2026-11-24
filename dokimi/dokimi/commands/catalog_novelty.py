# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog novelty ROOT``: the corpus novelty sweep, one ``skill novelty analyze`` per skill.

Every ``SKILL.md`` under ROOT is analysed with the flags of ``skill novelty analyze``, and the
model calls are paid. ``--max-usd`` is one budget for the whole sweep and ``--max-usd-per-skill``
a cap on each skill; ``--jobs`` runs skills in parallel threads that share the budget. Each skill
writes one row to ``--jsonl`` as it finishes, so a killed sweep keeps its rows, and ``--resume``
runs only the skills without a measured row (same directory digest, same settings).
``--summary-only`` prints the summary of a JSONL file without any model call. Directories with the
same contents (``SKILL.md`` and bundled files) that name the same skill count as one (the shallowest path
is kept, the others are named on stderr). Ctrl-C or SIGTERM
cancels the skills not yet started, lets the running ones finish, keeps the rows done and exits nonzero
(a second signal kills the subprocess calls in flight and stops at once; an API call in flight cannot be killed but no call after it starts, and its row is not written).

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
import signal
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from dokimi import __version__, cli_flags
from dokimi.atomic_write import write_text_atomic
from dokimi.cli_types import unit_interval
from dokimi.commands.novelty_run import stops_at_cap
from dokimi.novelty.sweep import bundle_instances, read_rows, resumable, summarize, summary_lines
from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.novelty.client import SpendCap

#: The JSONL file a sweep writes unless ``--jsonl`` names another.
DEFAULT_JSONL = "sweep.jsonl"


class _Stop:
    """A stop request: the signal (SIGINT or SIGTERM) that asked the sweep to stop, once it has."""

    def __init__(self) -> None:
        self.signum: Optional[int] = None
        self.hard = threading.Event()  # set by a second signal: no skill in flight starts another call

    @property
    def requested(self) -> bool:
        return self.signum is not None

    @property
    def exit_code(self) -> int:
        return 128 + (self.signum or signal.SIGINT)  # the shell's code for a process a signal stopped


@contextmanager
def _interruptible(stop: _Stop):
    """While the sweep runs, the first SIGINT or SIGTERM only sets *stop*: the sweep cancels the skills
    not started, and the skills in flight finish. A second signal kills the subprocess calls in flight
    (``process.kill_children``), sets ``stop.hard`` so no skill in flight starts another call, and raises
    KeyboardInterrupt. The handlers are restored after.
    Only the main thread can install them."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def on_signal(signum: int, frame: object) -> None:
        if stop.requested:  # a second signal: stop at once, the calls in flight too
            from dokimi.process import kill_children

            stop.hard.set()  # the API calls in flight end on their own, but the next one never starts
            kill_children()
            raise KeyboardInterrupt
        stop.signum = signum

    previous = {signum: signal.signal(signum, on_signal) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


@dataclass(frozen=True)
class _Item:
    """One skill of the root: its key (its directory relative to ROOT), SKILL.md, digest and load error."""

    key: str
    name: str
    skill_md: Path
    sha256: Optional[str]
    error: Optional[str] = None  # a SKILL.md that does not load: its row is an error, and no call is made

    bundle_instance: int = 0

    @property
    def directory(self) -> Path:
        return self.skill_md.parent


def _skill_digest(skill_md: Path) -> str:
    """The digest a resumed run compares: the directory tree, and the entry point's loaded content
    (a symlinked SKILL.md changes its target's text without changing the link the tree digest reads)."""
    from dokimi.utils import tree_digest

    entry = hashlib.sha256(skill_md.read_bytes()).hexdigest()
    tree = tree_digest(skill_md.parent.resolve(), tracked={})  # resolved: tree_digest hashes a symlinked ROOT by its link text
    return hashlib.sha256(f"{tree}:{entry}".encode("utf-8")).hexdigest()


def _items(root: Path, limit: Optional[int]) -> list[_Item]:
    """The skills under *root* in path order, the first *limit* of them.

    A bundle identical to a shallower one (same name, same files) is left out and named on stderr;
    two bundles with the same SKILL.md and different bundled files are two skills.
    """
    from dokimi.skills.loader import TEMPLATE_DIR_NAMES, load_skill, walk_skill_files

    candidates: list[_Item] = []
    for skill_md in walk_skill_files(root):
        directory = skill_md.parent
        if not (skill_md.is_file() or skill_md.is_symlink()) or TEMPLATE_DIR_NAMES.intersection(directory.relative_to(root).parts):
            continue
        error: Optional[str] = None
        try:
            name = load_skill(skill_md).name
        except (OSError, ValueError, RecursionError) as exc:
            name, error = directory.name, str(exc)
        try:
            sha: Optional[str] = _skill_digest(skill_md)
        except OSError:
            sha = None
        candidates.append(_Item(key=directory.relative_to(root).as_posix(), name=name, skill_md=skill_md,
                                sha256=sha, error=error))
    keeper: dict[tuple[str, str], _Item] = {}
    for item in sorted(candidates, key=lambda i: (len(i.skill_md.parts), i.skill_md.as_posix())):
        if item.sha256 is not None and item.error is None:
            keeper.setdefault((item.name, item.sha256), item)
    items: list[_Item] = []
    for item in candidates:
        kept = keeper.get((item.name, item.sha256 or "")) if item.sha256 is not None and item.error is None else item
        if kept is not None and kept is not item:
            print(f"# left out {item.skill_md}: identical to {kept.skill_md}", file=sys.stderr)
            continue
        items.append(item)
        if limit is not None and len(items) == limit:
            break
    return items


#: The most characters of a skill's key kept in its output directory name (the hash of the whole key follows).
DIRNAME_CHARS = 80


def _dirname(key: str) -> str:
    """A directory name for one skill's outputs: readable, and unique even when two keys read alike."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_")[-DIRNAME_CHARS:].strip("_") or "root"
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


#: How often the sweep's main thread looks at the stop request while it waits for a skill.
POLL_SECONDS = 0.2


def _run_sweep(items: list[_Item], *, jobs: int, review: Callable[[_Item], Optional[dict]],
               write: Callable[[dict], None], out_of_budget: Callable[[], bool],
               stop_requested: Callable[[], bool]) -> None:
    """Review the items with up to *jobs* threads, handing each row to *write* as it finishes.

    ``review`` returns None for a skill it did not run (the budget was spent before it started);
    no new skill is submitted once ``out_of_budget()`` holds. A stop request (``stop_requested()``)
    does the same, and cancels the skills queued but not started, so they make no call. The skills
    in flight finish and their rows are written. A worker's unexpected failure cancels the queue the
    same way and propagates after the rows finished so far are written. A KeyboardInterrupt (the second
    signal) waits for nothing: it writes the rows already done and propagates.
    """
    pending = iter(items)
    running: set = set()
    pool = ThreadPoolExecutor(max_workers=jobs)

    def guarded(item: _Item) -> Optional[dict]:
        # a stop that landed while this skill was queued: it makes no call, even if a worker freed up first
        return None if stop_requested() else review(item)

    def fill() -> None:
        while len(running) < jobs * 2 and not out_of_budget() and not stop_requested():
            item = next(pending, None)
            if item is None:
                return
            running.add(pool.submit(guarded, item))

    try:
        fill()
        while running and not stop_requested():
            finished, _ = wait(running, timeout=POLL_SECONDS, return_when=FIRST_COMPLETED)
            for future in finished:
                running.discard(future)  # before the write: a write that raises is not written again
                row = future.result()
                if row is not None:
                    write(row)
            fill()
    except BaseException as exc:
        _unwind(exc, pool, running, write)
        raise
    _settle(pool, running, write)


def _unwind(exc: BaseException, pool: ThreadPoolExecutor, running: set, write: Callable[[dict], None]) -> None:
    """What the sweep does on its way out of *exc*. A KeyboardInterrupt (the second signal) waits for nothing:
    the queue is cancelled and only the rows already done are written. Any other failure settles."""
    if not isinstance(exc, KeyboardInterrupt):
        _settle(pool, running, write)
        return
    pool.shutdown(wait=False, cancel_futures=True)
    for future in list(running):
        if future.done():
            _keep(future, write)
    running.clear()


def _settle(pool: ThreadPoolExecutor, running: set, write: Callable[[dict], None]) -> None:
    """Cancel the queued skills, keep the rows already done, then wait for the ones in flight and keep
    theirs. The done rows are written before the wait, so an interrupt during the wait keeps them."""
    pool.shutdown(wait=False, cancel_futures=True)
    pending = set()
    for future in running:
        if future.done():
            _keep(future, write)
        else:
            pending.add(future)
    while pending:  # each row is written as its skill finishes, so an interrupt keeps the ones done
        finished, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in finished:
            _keep(future, write)
    running.clear()


def _keep(future, write: Callable[[dict], None]) -> None:
    if not future.cancelled() and future.exception() is None and future.result() is not None:
        write(future.result())


def _refuse_to_run(item: _Item, novelty_json: Path) -> Optional[tuple[str, str]]:
    """The (reason, message) of an error row when the skill is not analysed at all, else None. Runs first:
    the files an earlier run left for this skill describe content this attempt may not measure, so they go."""
    try:
        novelty_json.unlink(missing_ok=True)
        novelty_json.with_suffix(".md").unlink(missing_ok=True)
    except OSError as exc:
        return "error", f"could not remove its earlier novelty file: {exc}"
    return None if item.error is None else ("not_loaded", item.error)


def _save_novelty(artifact: dict, novelty_json: Path) -> Optional[str]:
    """Write the novelty file and its report; the reason it could not, else None. The paid analysis is
    done by now, so a failed write is this skill's error, not the sweep's."""
    from dokimi.artifacts import save_artifact
    from dokimi.novelty.report import render_novelty_markdown

    try:
        save_artifact("novelty", artifact, novelty_json)
        novelty_json.with_suffix(".md").write_text(render_novelty_markdown(artifact), encoding="utf-8")
    except (OSError, ValueError) as exc:
        return f"could not save its novelty file: {exc}"
    return None


def _review_one(item: _Item, *, args: argparse.Namespace, run_cap: "SpendCap", per_skill: float,
                hard_stop: Optional[threading.Event] = None,
                placebo_text: Optional[str], cache_dir: Optional[Path], out_dir: Path,
                fingerprint: dict) -> Optional[dict]:
    """The row of one skill: analyse it under its own cap and save its novelty file, or say why it has none."""
    from dokimi.novelty.atoms import ExtractionError
    from dokimi.novelty.client import NestedSpendCap, SpendCapReached
    from dokimi.novelty.measure import SelfJudgeError
    from dokimi.novelty.sweep import has_measure, make_row, measured_fields

    if run_cap.spent_usd >= run_cap.limit_usd:
        return None
    cap = NestedSpendCap(limit_usd=per_skill, parent=run_cap, hard_stop=hard_stop)

    def row(status: str, **fields: Any) -> dict:
        return make_row(key=item.key, name=item.name, path=str(item.skill_md),
                        skill_sha256=item.sha256, bundle_instance=item.bundle_instance, status=status, settings=fingerprint,
                        version=__version__, usd=cap.spent_usd, calls=cap.calls, **fields)

    novelty_json = out_dir / _dirname(item.key) / "novelty.json"
    if (refusal := _refuse_to_run(item, novelty_json)) is not None:
        return row("error", reason=refusal[0], message=refusal[1])
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
    if not has_measure(measured):
        return row("error", reason="no_pairs", message="no probe was paired, so there is no share")
    if (failure := _save_novelty(result.artifact, novelty_json)) is not None:
        return row("error", reason="error", message=failure)
    return row("ok", measured=measured, novelty_json=str(novelty_json))


def _emit_summary(summary: dict, args: argparse.Namespace) -> None:
    for line in summary_lines(summary):
        print(line)
    if args.summary_out:
        write_text_atomic(args.summary_out, json.dumps(summary, indent=2) + "\n")
        print(f"summary written to {args.summary_out}")


def cmd_catalog_novelty(args: argparse.Namespace) -> None:
    from dokimi.commands.inputs import placebo_text_for_arms_or_exit
    from dokimi.commands.novelty import _arms
    from dokimi.commands.novelty_run import usable_cache_dir
    from dokimi.novelty.client import SpendCap
    from dokimi.novelty.sweep import load_strata

    root = Path(args.root)
    strata = load_strata(Path(args.strata), args.stratum_column) if args.strata else None
    out_dir = Path(args.out_dir)
    jsonl = Path(args.jsonl) if args.jsonl else out_dir / DEFAULT_JSONL
    _refuse_unsafe_summary_out(args, jsonl, out_dir)
    if args.summary_only:
        _summary_only(jsonl, strata, args)
        return
    if not root.is_dir():
        raise InputError(f"ROOT is not a directory: {root}")
    _refuse_unsafe_run(args, jsonl, out_dir)
    placebo_text = placebo_text_for_arms_or_exit(args.placebo_text, _arms(args.arms))
    cache_dir = usable_cache_dir(args.cache_dir)
    all_items = _items(root, None)
    if not all_items:
        raise InputError(f"no SKILL.md under {root}")
    _refuse_outputs_inside_a_skill(all_items, out_dir, jsonl)
    items = all_items[:args.limit]
    fingerprint = _fingerprint(args, placebo_text)
    kept, todo, others = _plan(items, all_items, jsonl, args, fingerprint)
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
    _exit_if_short(len(todo), new_rows, jsonl)


def _exit_if_short(run: int, new_rows: list[dict], jsonl: Path) -> None:
    """Exit 1 once the rows and the summary are written when a skill this run took on was not measured
    (partial, error or not run), as the novelty commands do when the budget or a failed call stops them."""
    short = run - sum(row["status"] == "ok" for row in new_rows)
    if short:
        raise SystemExit(f"{short} of {run} skill(s) this run took on were not measured (partial, error or "
                         f"not run); the rows are in {jsonl}, and --resume runs them again")


def _refuse_outputs_inside_a_skill(items: list[_Item], out_dir: Path, jsonl: Path) -> None:
    """Refuse an --out-dir or --jsonl inside a skill's directory: what the sweep writes there changes the
    skill's digest, so every --resume would pay for that skill again."""
    for flag, path in (("--out-dir", out_dir), ("--jsonl", jsonl)):
        for item in items:
            if path.resolve().is_relative_to(item.directory.resolve()):
                raise InputError(f"{flag} {path} is inside the skill {item.key!r}: its files would change the "
                                 "skill's digest, so every --resume would measure it again; write it elsewhere")


def _is_a_novelty_file(path: Path, out_dir: Path) -> bool:
    """Whether ``path`` is the novelty.json or novelty.md the sweep writes for a skill under ``out_dir``."""
    resolved = path.resolve()
    if resolved.name not in ("novelty.json", "novelty.md"):
        return False
    if resolved.parent.parent == out_dir.resolve():
        return True
    try:  # a skill's directory under --out-dir may be a symlink: its files live where it points
        return any((child / resolved.name).resolve() == resolved for child in out_dir.iterdir() if child.is_dir())
    except OSError:
        return False


def _refuse_unsafe_summary_out(args: argparse.Namespace, jsonl: Path, out_dir: Path) -> None:
    """Refuse a --summary-out that is the --jsonl file (the summary would replace the paid rows), a skill's
    novelty.json or novelty.md under --out-dir, or whose directory cannot be made, before any paid work."""
    if not args.summary_out:
        return
    summary_out = Path(args.summary_out)
    if summary_out.resolve() == jsonl.resolve():
        raise InputError(f"--summary-out {summary_out} is the --jsonl file: the summary would replace the rows")
    if _is_a_novelty_file(summary_out, out_dir):
        raise InputError(f"--summary-out {summary_out} is a skill's novelty file under --out-dir: the summary would replace it")
    try:
        summary_out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InputError(f"cannot write --summary-out {summary_out}: {exc}") from exc


def _summary_only(jsonl: Path, strata, args: argparse.Namespace) -> None:
    rows = read_rows(jsonl)
    if not rows:
        raise InputError(f"--jsonl {jsonl} has no rows to summarise")
    measured_under = {json.dumps([row.get("settings"), row.get("dokimi_version")], sort_keys=True)
                      for row in rows if row["status"] == "ok"}
    if len(measured_under) > 1:  # one interval over two answerers or settings is no one model's share
        raise InputError(f"--jsonl {jsonl} holds ok rows measured under {len(measured_under)} different settings or "
                         "dokimi versions; a --resume without --limit measures them all alike")
    _emit_summary(summarize(rows, strata, resamples=args.bootstrap_resamples), args)


def _refuse_unsafe_run(args: argparse.Namespace, jsonl: Path, out_dir: Path) -> None:
    """Refuse a paid run that would not be a clean one: a judge2 it cannot run, no budget,
    an overwrite of a sweep in progress, or a model flag that does not resolve."""
    from dokimi.novelty import cost

    if args.judge2:
        raise InputError("--judge2 is not part of catalog novelty: run it on one skill with skill novelty analyze")
    if len(args.judges or ()) > 1 or args.judge_aggregate != "median":
        raise InputError("a judge panel (--judge given more than once, --judge-aggregate) is not part of catalog "
                         "novelty: the sweep grades with one judge; run a panel on one skill with skill novelty analyze")
    if args.max_usd is None:
        raise InputError("catalog novelty makes paid calls: pass --max-usd, the whole-run budget "
                         "(--max-usd-per-skill caps one skill inside it)")
    if _is_a_novelty_file(jsonl, out_dir):
        raise InputError(f"--jsonl {jsonl} is a skill's novelty file under --out-dir: the sweep would delete or replace it")
    if jsonl.exists() and not args.resume:
        raise InputError(f"--jsonl {jsonl} exists: pass --resume to continue it, or remove it to start over")
    from dokimi.novelty.measure import SelfJudgeError, check_self_judge

    try:
        cost.role_models(args)
        _, answerer, judge = cost.clients(copy.copy(args))  # a copy: the run's own cap is set up later
        check_self_judge(judge, answerer, args.allow_self_judge)  # before a resume drops a row or a file
    except (ValueError, SelfJudgeError) as exc:
        raise InputError(str(exc)) from exc


def _refuse_changed_settings(prior: list[dict], selected: dict[str, Optional[str]],
                             fingerprint: dict[str, Any]) -> None:
    """Refuse a --resume that would drop the ``ok`` row of a selected skill measured under other settings or
    another dokimi version: the rewrite of the JSONL deletes it before the budget may reach its rerun."""
    for row in prior:
        if row["status"] != "ok" or row["key"] not in selected:
            continue
        old = row.get("settings") or {}
        differ = sorted(name for name in fingerprint.keys() | old.keys() if old.get(name) != fingerprint.get(name))
        if row.get("dokimi_version") != __version__:
            differ.append("dokimi_version")
        if differ:
            raise InputError(f"--resume: the measured row of {row['key']!r} was made under other settings "
                             f"({', '.join(differ)}); resuming would delete it before it is measured again. Use "
                             "another --jsonl, or pass --rerun-changed to measure those skills again")


def _plan(items: list[_Item], all_items: list[_Item], jsonl: Path, args: argparse.Namespace,
          fingerprint: dict[str, Any]) -> tuple[list[dict], list[_Item], list[dict]]:
    """The measured rows a resumed run keeps, the skills it still has to run, and the prior rows of
    skills this run does not select (a narrower --limit, a moved skill): those stay in the file as they are."""
    selected = {item.key: item.sha256 for item in items}
    prior = read_rows(jsonl) if args.resume else []
    if args.resume and not args.rerun_changed:
        _refuse_changed_settings(prior, selected, fingerprint)
    kept = resumable(prior, selected, fingerprint, __version__)
    current = {item.key: item.sha256 for item in all_items}
    instances = bundle_instances(current, prior)
    others = [row for row in prior if row["key"] not in selected]
    for row in others + kept:
        if row["key"] not in instances or row.get("skill_sha256") != current[row["key"]]:
            continue
        if slot := instances.get(row["key"]):
            row["bundle_instance"] = slot
        else:
            row.pop("bundle_instance", None)
    done = {row["key"] for row in kept}
    todo = [replace(item, bundle_instance=instances.get(item.key, 0)) for item in items if item.key not in done]
    return kept, todo, others


def _sweep_rows(todo: list[_Item], *, args: argparse.Namespace, run_cap, kept: list[dict],
                others: list[dict], total: int, jsonl: Path, out_dir: Path, placebo_text: Optional[str],
                cache_dir: Optional[Path], fingerprint: dict[str, Any]) -> list[dict]:
    """Run the skills still to do, writing each row as it finishes; returns the rows this run made."""
    sink = _Sink(jsonl, others + kept, resume=args.resume)
    stop = _Stop()
    review = _bound(args, run_cap=run_cap, hard_stop=stop.hard, placebo_text=placebo_text, cache_dir=cache_dir,
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
        with _interruptible(stop):
            _run_sweep(todo, jobs=args.jobs, review=review, write=write, out_of_budget=out_of_budget,
                       stop_requested=lambda: stop.requested)
    except KeyboardInterrupt:  # a second signal: stop at once, the rows written so far are kept
        stop.signum = stop.signum or signal.SIGINT
    finally:
        sink.close()
    not_run = len(todo) - len(new_rows)
    if not_run:
        print(f"# {not_run} skill(s) not run: the budget was spent, a call had no price, a provider error every "
              "call would meet stopped it, or the run was stopped. Rerun with "
              "--resume (and a higher --max-usd if the budget was spent); set DOKIMI_RESPONSE_CACHE so the "
              "calls already paid replay at $0", file=sys.stderr)
    if stop.requested:
        print(f"# stopped by signal {stop.signum}: queued skills were cancelled, {len(new_rows)} row(s) "
              f"written to {jsonl}; rerun with --resume to continue", file=sys.stderr)
        raise SystemExit(stop.exit_code)
    return new_rows


def _bound(args: argparse.Namespace, **shared: Any) -> Callable[[_Item], Optional[dict]]:
    """The per-skill review with this run's shared pieces bound: the arguments, the budget and the output dir."""
    per_skill = args.max_usd_per_skill or math.inf  # unset: no cap of its own, only the run's
    halted = threading.Event()  # a skill met a stop every later skill would meet: none starts after it

    def review(item: _Item) -> Optional[dict]:
        if halted.is_set():
            return None
        row = _review_one(item, args=args, per_skill=per_skill, **shared)
        if row is not None and row["reason"] in RUN_WIDE_STOPS:
            halted.set()
        return row

    return review


#: The stop reasons a higher budget does not cure: every later skill would meet them on its first calls.
RUN_WIDE_STOPS = ("no_price", "permanent_error")


def register(catalog_sub, positive_int) -> None:
    """Add ``catalog novelty``: the corpus sweep, a catalog verb like ``catalog routing``."""
    from dokimi.commands.novelty_flags import _add_bundle_args, _add_model_args
    from dokimi.cli_types import positive_usd
    from dokimi.novelty.sweep import RESAMPLES
    from dokimi.novelty.tiers import TIERS

    p = catalog_sub.add_parser(
        "novelty",
        help="Corpus novelty sweep: analyze every skill under a catalog root under a budget, resumable, "
             "with bootstrap CIs per stratum [paid]",
    )
    p.add_argument("root", help="Catalog root: every SKILL.md under it is one skill. Directories with identical "
                                "contents (SKILL.md and bundled files) that name the same skill count once: the "
                                "shallowest path is kept, the other copies are named on stderr (not an error, no row)")
    p.add_argument("--out-dir", required=True, dest="out_dir",
                   help="Where each skill's novelty.json and report go (OUT_DIR/<skill>/)")
    p.add_argument("--jsonl", default=None,
                   help=f"One row per skill, written as each finishes (default: OUT_DIR/{DEFAULT_JSONL}). "
                        "Ctrl-C or SIGTERM cancels the skills not yet started (no new call), lets the "
                        "running ones finish, keeps the rows done and exits nonzero (130, 143); "
                        "a second signal kills the subprocess calls in flight too")
    p.add_argument("--jobs", type=positive_int, default=1,
                   help="Skills analysed in parallel threads, sharing --max-usd (default 1)")
    p.add_argument("--resume", action="store_true",
                   help="Keep the measured rows in --jsonl and run only the other skills")
    p.add_argument("--rerun-changed", action="store_true", dest="rerun_changed",
                   help="With --resume, measure again the skills whose measured row was made under other settings "
                        "or another dokimi version, replacing the row (without it such a --resume is refused)")
    p.add_argument("--max-usd-per-skill", type=positive_usd, default=None, dest="max_usd_per_skill",
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
    cli_flags.add(p, "tokens", "--token-budgets", dest="budgets", metavar="F", type=unit_interval, nargs="+",
                  default=[0.75, 0.5],
                  help="Target token fractions for shortening candidates")
    p.add_argument("--max-atoms", type=positive_int, default=None, dest="max_atoms",
                   help="Atom cap per skill (see skill novelty analyze)")
    p.add_argument("--seed", type=int, default=0, help="Seed for the controls and the selection, per skill")
    p.add_argument("--cache-dir", default=None, dest="cache_dir",
                   help="Disk cache for retention adjudications")
    _add_model_args(p, positive_int)  # --model, --judge-model, ... and --max-usd: the whole-run budget
    _add_bundle_args(p, positive_int)
    p.set_defaults(func=stops_at_cap(cmd_catalog_novelty))

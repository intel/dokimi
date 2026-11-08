# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --per-skill``: each skill reviewed alone, streamed to JSONL, with ``--jobs`` and ``--resume``.

The cross-catalog checks (``duplicate``, ``name-conflict``, ``mirror-drift``,
``description-near-duplicate``) need every skill reviewed together. This mode gives them up
and reviews one skill per row. Every SKILL.md body the scan finds is loaded before the first row (eager, so
memory grows with the catalog; lazy per-skill loading is #434). Rows are written in
catalog order within one run (``--resume`` appends the rest after the rows it kept), a run
killed midway keeps the rows it wrote, and ``--resume`` reviews only the skills that have no
row yet or whose directory changed since their row was written (its ``skill_sha256``).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from dokimi.utils import InputError


def skill_row(skill, review, nested_in: Optional[str] = None, outputs: Iterable[Path] = ()) -> dict:
    """One skill of the scan report. ``state`` is ``error`` for a load error or an error finding,
    ``warn`` for a warning finding only, else ``ok``. A skill that did not load has a review only
    when its frontmatter is broken YAML, and that review's finding is its only finding.
    ``nested_in`` is the skill whose directory holds this one (None when it is not nested).
    ``outputs`` are the scan's own files, left out of ``skill_sha256``."""
    from dokimi.external.catalog_review import entry_relpath

    if not skill.loaded:
        state, findings = "error", (review.findings if review else [])
    else:
        state, findings = review.state, review.findings
    return {
        "name": skill.name,
        "manifest_status": None if skill.status is None else str(skill.status),
        "path": str(skill.path) if skill.path else None,
        "relpath": entry_relpath(skill),
        "nested_in": nested_in,
        "state": state,
        "error": skill.error,
        "disable_model_invocation": bool(review and review.disable_model_invocation),
        "content_sha256": review.content_sha256 if review else None,
        "skill_sha256": skill_dir_digest(skill, outputs),
        "duplicate_of": review.duplicate_of if review else None,
        "findings": [{"level": f.level, "code": f.code, "message": f.message} for f in findings],
    }


def skill_dir_digest(entry, outputs: Iterable[Path] = ()) -> Optional[str]:
    """``tree_digest`` of the entry's skill directory by the bytes and mode bits on disk, with no git call.

    Files in *outputs* (resolved paths) that sit in the directory are left out. None when it cannot be read.
    """
    from dokimi.utils import tree_digest

    path = entry.skill.path.parent if entry.loaded and entry.skill is not None else entry.path
    if path is not None and path.name == "SKILL.md":
        path = path.parent
    if path is None or not path.is_dir():
        return None
    try:
        base = path.resolve()
        skip = [out.relative_to(base).as_posix() for out in outputs if out.is_relative_to(base)]
        return tree_digest(path, exclude=skip, tracked={})
    except OSError:
        return None


def _outputs(jsonl: Optional[Path]) -> tuple[Path, ...]:
    """The files a ``--jsonl`` scan writes, resolved: the file and the temporary one :func:`_rewrite` replaces it with."""
    if jsonl is None:
        return ()
    out = jsonl.resolve()
    return (out, out.with_name(out.name + ".part"))


def print_skill_row(row: dict) -> None:
    if row["error"] is not None:
        state = f"ERROR: {row['error']}"
    elif row["state"] == "ok":
        state = "ok"
    else:
        worst = next(f for f in row["findings"] if f["level"] == row["state"])
        state = f"{'ERROR' if row['state'] == 'error' else 'warn'}: {worst['message']}"
    print(f"{row['name']}\t{row['manifest_status'] or '-'}\t{row['path'] or '-'}\t{state}")
    for f in row["findings"]:
        print(f"  {f['level']} {f['code']}: {f['message']}")
    if row["disable_model_invocation"]:
        print("  note disable-model-invocation: true (the model is never offered this skill)")
    if row["nested_in"] is not None:
        print(f"  note nested in {row['nested_in']}: a SKILL.md inside another skill's directory")


def mark_nested(args, skills: list, root: Path) -> list:
    """The *skills* of *root* as ``(entry, nested_in)`` pairs; nested ones are dropped under ``--skip-nested``."""
    from dokimi.external import nested_in_bundles

    pairs = list(zip(skills, nested_in_bundles(skills, root), strict=True))
    if args.skip_nested:
        pairs = [(entry, mark) for entry, mark in pairs if mark is None]
    return pairs


def _entry_key(entry) -> str:
    from dokimi.external.catalog_review import entry_relpath

    return entry_relpath(entry) or f"name:{entry.name}"


def _row_key(row: dict) -> str:
    return row["relpath"] or f"name:{row['name']}"


def _review_row(item: tuple) -> dict:
    """One ``(entry, nested_in, outputs)`` item reviewed alone, as a report row. Module level, so a worker can run it."""
    from dokimi.external.validate import review_skills

    entry, nested_in, outputs = item
    return skill_row(entry, review_skills([entry])[0], nested_in, outputs)


def _review_rows(pairs: list, jobs: int, review: Callable = _review_row) -> Iterator[dict]:
    """The rows of *pairs* in order; ``jobs`` worker processes when more than one.

    A worker that dies (killed, out of memory) ends the scan with exit 1 instead of a hang.
    """
    if jobs <= 1:
        for pair in pairs:
            yield review(pair)
        return
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool

    # spawn on every platform: Python 3.14 defaults Linux to forkserver, which talks to its server
    # over a socket, and spawn is what Windows and macOS use already.
    pool = ProcessPoolExecutor(jobs, mp_context=multiprocessing.get_context("spawn"))
    try:
        yield from pool.map(review, pairs, chunksize=32)
    except BrokenProcessPool as exc:
        raise SystemExit("a --jobs worker process died; the rows written so far are kept: rerun with --resume") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class _Tally:
    """Counts of the rows by state, and whether the scan must exit 1 (a load error, or a spec error under --strict)."""

    def __init__(self, strict: bool) -> None:
        self.strict = strict
        self.counts = {"ok": 0, "warn": 0, "error": 0}
        self.failed = False

    def add(self, row: dict) -> None:
        self.counts[row["state"]] += 1
        if row["error"] is not None or (self.strict and row["state"] == "error"):
            self.failed = True


_ROW_STATES = ("ok", "warn", "error")


def _is_row(row: object) -> bool:
    return (
        isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and isinstance(row.get("relpath"), (str, type(None)))
        and row.get("state") in _ROW_STATES
        and "error" in row
        and isinstance(row.get("findings"), list)
    )


def _parse_row(raw: bytes, path: Path, lineno: int) -> Optional[dict]:
    """The scan row a line of the JSONL file holds; None for a line a killed run left half-written.

    A complete line that is not a scan row is not a cut-off tail, so it is a usage error.
    """
    if not raw.strip():
        return None
    try:
        row = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        row = None
    if _is_row(row):
        return row
    if not raw.endswith(b"\n"):
        return None
    raise InputError(f"--jsonl {path} line {lineno} is not a scan row; fix or remove it before --resume")


def _load_prior(path: Path, selected: dict[str, Optional[str]]) -> tuple[list[dict], bool]:
    """The rows of *path* still current for the skills this run selects, and whether the file must be rewritten.

    *selected* maps each skill's key to its ``skill_sha256`` now. The file is read line by line and only
    the kept rows are held. A rewrite is due when a line is half-written, when the last line has no
    newline (the next row would be glued onto it), or when a row is for a skill this run does not
    select or whose directory changed (the file must match the counts printed).
    """
    rows: list[dict] = []
    dirty = False
    try:
        with path.open("rb") as fh:
            for lineno, raw in enumerate(fh, 1):
                row = _parse_row(raw, path, lineno)
                if row is None or not raw.endswith(b"\n"):
                    dirty = True
                if row is None:
                    continue
                key = _row_key(row)
                if key in selected and "skill_sha256" in row and row["skill_sha256"] == selected[key]:
                    rows.append(row)
                else:
                    dirty = True
    except OSError as exc:
        raise InputError(f"cannot read --jsonl {path} to resume: {exc}") from exc
    return rows, dirty


def _rewrite(path: Path, rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".part")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        tmp.replace(path)
    except OSError as exc:
        raise InputError(f"cannot rewrite --jsonl {path} to resume: {exc}") from exc


class _Jsonl:
    """The ``--jsonl`` file: one flushed line per row, so a killed run leaves whole rows behind.

    Rows are written with ``ensure_ascii`` on, so every line is ASCII and a kill cannot cut a character
    in half, and a directory name that is not valid UTF-8 is escaped rather than a crash.
    """

    def __init__(self, path: Path, resume: bool, selected: dict[str, Optional[str]]) -> None:
        self.path = path
        self.prior: list[dict] = []
        if resume and path.is_file():
            self.prior, dirty = _load_prior(path, selected)
            if dirty:
                _rewrite(path, self.prior)
        try:
            self._fh = path.open("a" if resume else "w", encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot write --jsonl {path}: {exc}") from exc

    def write(self, row: dict) -> None:
        self._fh.write(json.dumps(row) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _print_text(root: Path, declared: int, tally: _Tally, resumed: int,
                jsonl: Optional[Path], rows: Iterable[dict]) -> None:
    counts = tally.counts
    where = f"; {resumed} resumed from {jsonl}" if jsonl is not None and resumed else ""
    print(f"# skills ({declared} declared under {root}; {counts['ok']} ok, {counts['warn']} warn, "
          f"{counts['error']} error{where})")
    if jsonl is not None:
        print(f"rows written to {jsonl}")
    else:
        for row in rows:
            print_skill_row(row)
    print("# granularity: not run with --per-skill (run without it for the merge and split hints)")
    print()


def scan_per_skill(args, root: Path, report: dict, as_json: bool, pairs: list) -> tuple[bool, int]:
    """Review each ``(entry, nested_in)`` pair alone; returns (exit 1 wanted, skills declared).

    Without ``--jsonl`` the rows go into ``report["skills"]`` as the cross-catalog check puts them.
    With it they stream to that file, and stdout gets the counts only.
    """
    declared = len(pairs)
    jsonl = Path(args.jsonl) if args.jsonl else None
    # Only the rows of the skills this run selects count: --skip-nested must hide a nested row an
    # earlier run wrote, and a skill removed from the catalog is not part of this scan.
    keys = [_entry_key(entry) for entry, _ in pairs]
    if args.resume:
        # A key shared by two skills would let --resume count one of them as done when only the other has a row.
        shared = sorted(key for key, n in Counter(keys).items() if n > 1)
        if shared:
            raise InputError(f"--resume needs one row per skill, but these keys are shared: {', '.join(shared[:3])}")
    outputs = _outputs(jsonl)
    selected = {key: skill_dir_digest(entry, outputs) if args.resume else None
                for key, (entry, _) in zip(keys, pairs, strict=True)}
    sink = _Jsonl(jsonl, args.resume, selected) if jsonl is not None else None
    tally = _Tally(args.strict)
    prior = sink.prior if sink is not None else []
    for row in prior:
        tally.add(row)
    done = {_row_key(row) for row in prior}
    todo = [(entry, mark, outputs) for entry, mark in pairs if _entry_key(entry) not in done]
    try:
        for row in _review_rows(todo, args.jobs or 1):
            tally.add(row)
            if sink is not None:
                sink.write(row)
            else:
                report["skills"].append(row)
    finally:
        if sink is not None:
            sink.close()
    if not as_json:
        _print_text(root, declared, tally, len(prior), jsonl, report["skills"])
    return tally.failed, declared

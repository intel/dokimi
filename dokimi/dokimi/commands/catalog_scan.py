# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --per-skill``: each skill reviewed alone, streamed to JSONL, with ``--jobs`` and ``--resume``.

The cross-catalog checks (``duplicate``, ``name-conflict``, ``mirror-drift``,
``description-near-duplicate``) need every skill loaded at once. This mode gives them up,
so a corpus of a million skills can be scanned one row at a time: rows are written in
catalog order, a run killed midway keeps the rows it wrote, and ``--resume`` reviews only
the skills that have no row yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator, Optional

from dokimi.exit_codes import usage_exit


def skill_row(skill, review, nested_in: Optional[str] = None) -> dict:
    """One skill of the scan report. ``state`` is ``error`` for a load error or an error finding,
    ``warn`` for a warning finding only, else ``ok``. A skill that did not load has a review only
    when its frontmatter is broken YAML, and that review's finding is its only finding.
    ``nested_in`` is the skill whose directory holds this one (None when it is not nested)."""
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
        "duplicate_of": review.duplicate_of if review else None,
        "findings": [{"level": f.level, "code": f.code, "message": f.message} for f in findings],
    }


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


def read_marked_skills(args, root: Path) -> list:
    """The skills of *root* as ``(entry, nested_in)`` pairs; nested ones are dropped under ``--skip-nested``."""
    from dokimi.external import nested_in_bundles, read_skill_manifest

    try:
        skills = read_skill_manifest(root)
    except ValueError as exc:
        usage_exit(f"skills.yaml: {exc}")
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
    """One ``(entry, nested_in)`` pair reviewed alone, as a report row. Module level, so a worker can run it."""
    from dokimi.external.validate import review_skills

    entry, nested_in = item
    return skill_row(entry, review_skills([entry])[0], nested_in)


def _review_rows(pairs: list, jobs: int) -> Iterator[dict]:
    """The rows of *pairs* in order; ``jobs`` worker processes when more than one."""
    if jobs <= 1:
        for pair in pairs:
            yield _review_row(pair)
        return
    import multiprocessing

    # spawn on every platform: Python 3.14 defaults Linux to forkserver, which talks to its server
    # over a socket, and spawn is what Windows and macOS use already.
    with multiprocessing.get_context("spawn").Pool(jobs) as pool:
        yield from pool.imap(_review_row, pairs, chunksize=32)


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


def _load_prior(path: Path) -> list[dict]:
    """The rows already in the JSONL file. A line a killed run left half-written is dropped from the file."""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        usage_exit(f"cannot read --jsonl {path} to resume: {exc}")
    rows: list[dict] = []
    kept: list[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
            kept.append(line)
    if len(kept) != len(lines):
        tmp = path.with_name(path.name + ".part")
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        tmp.replace(path)
    return rows


class _Jsonl:
    """The ``--jsonl`` file: one flushed line per row, so a killed run leaves whole rows behind."""

    def __init__(self, path: Path, resume: bool) -> None:
        self.path = path
        self.prior = _load_prior(path) if resume else []
        try:
            self._fh = path.open("a" if resume else "w", encoding="utf-8")
        except OSError as exc:
            usage_exit(f"cannot write --jsonl {path}: {exc}")

    def write(self, row: dict) -> None:
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
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
    print()


def scan_per_skill(args, root: Path, report: dict, as_json: bool, pairs: list) -> tuple[bool, int]:
    """Review each ``(entry, nested_in)`` pair alone; returns (exit 1 wanted, skills declared).

    Without ``--jsonl`` the rows go into ``report["skills"]`` as the cross-catalog check puts them.
    With it they stream to that file, and stdout gets the counts only.
    """
    declared = len(pairs)
    jsonl = Path(args.jsonl) if args.jsonl else None
    sink = _Jsonl(jsonl, args.resume) if jsonl is not None else None
    tally = _Tally(args.strict)
    prior = sink.prior if sink is not None else []
    # Only the rows of the skills this run selects count: --skip-nested must hide a nested row an
    # earlier run wrote, and a skill removed from the catalog is not part of this scan.
    selected = {_entry_key(entry) for entry, _ in pairs}
    prior = [row for row in prior if _row_key(row) in selected]
    for row in prior:
        tally.add(row)
    done = {_row_key(row) for row in prior}
    todo = [pair for pair in pairs if _entry_key(pair[0]) not in done]
    try:
        for row in _review_rows(todo, args.jobs):
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

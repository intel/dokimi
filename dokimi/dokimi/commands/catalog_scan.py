# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --per-skill``: each skill reviewed alone, streamed to JSONL, with ``--concurrency`` and ``--resume``.

The cross-catalog checks (``duplicate``, ``name-conflict``, ``mirror-drift``,
``description-near-duplicate``, ``similar-skills-exist``) need every skill reviewed together. This mode gives them up
and reviews one skill per row. Every SKILL.md body the scan finds is loaded before the first row (eager, so
memory grows with the catalog; lazy per-skill loading is #434). Rows are written in
catalog order within one run (``--resume`` appends the rest after the rows it kept), a run
killed midway keeps the rows it wrote, and ``--resume`` reviews only the skills that have no
row yet, whose directory changed since their row was written (its ``skill_sha256``), or whose row
another dokimi version wrote (``dokimi_version``).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from dokimi import __version__
from dokimi.external.listing import listed_chars
from dokimi.external.skill_format import UNSUPPORTED_CODE
from dokimi.utils import InputError


def _listing_source(entry):
    """``(file, meta, body, directory name, at root, sha256 of the bytes read)`` of the SKILL.md ``repo budget`` would read for the entry, None when
    it is not named ``SKILL.md``, cannot be read or resolves outside the catalog root. The directory name and ``at root`` are
    those of the directory the skill is listed in, not of a symlink target."""
    from dokimi.utils import parse_frontmatter

    if entry.path is None:
        return None
    try:
        listed = entry.skill.path if entry.skill is not None else entry.path if entry.path.is_file() else entry.path / "SKILL.md"
        if listed.name != "SKILL.md":
            return None
        file = listed.resolve(strict=True)
        root = entry.source_root.resolve()
        if not file.is_file() or not file.is_relative_to(root):
            return None
        raw = file.read_bytes()
        meta, body = parse_frontmatter(raw.decode("utf-8"))
    except (OSError, ValueError, RuntimeError):  # UnicodeDecodeError is a ValueError
        return None
    # The default name is the directory the skill is listed in, not the one a symlinked SKILL.md points into.
    return file, meta if isinstance(meta, dict) else {}, body, Path(os.path.normpath(listed.parent)).name, listed.parent.resolve() == root, hashlib.sha256(raw).hexdigest()


def _name_fits_spec(read) -> bool:
    """Whether the declared name breaks no name rule (``name-format``, ``name-mismatch``): a skill that also fails those is
    not listed once its description is added, so the missing-description fallback does not charge it."""
    from dokimi.external.validate import NAME_MAX, NAME_PATTERN

    _, meta, _, dir_name, at_root, _ = read
    declared = str(meta.get("name") or "").strip()
    if not declared:
        return True
    return bool(NAME_PATTERN.fullmatch(declared)) and len(declared) <= NAME_MAX and (at_root or declared == dir_name)


_READ = object()  # ``listing_preview`` was given no source: read the file itself


def listing_preview(entry, source=_READ) -> Optional[dict]:
    """What ``repo budget`` would list for the skill: its text, where it came from, and its characters.

    ``source`` is ``frontmatter`` or ``body`` (the first non-empty line, used when ``description`` is
    omitted, #943). ``listing_chars`` is the ``repo budget`` charge (name plus the text, capped), whether
    or not the skill is listed now. None when SKILL.md cannot be read, resolves outside the catalog root,
    or there is no text to list. ``source`` may be the result of ``_listing_source`` (None included), to read the file once.
    """
    from dokimi.repos.budget import LISTING_TEXT_CAP
    from dokimi.skills.listing import listing_description, listing_text_chars

    if source is _READ:
        source = _listing_source(entry)
    if source is None:
        return None
    _, meta, body, dir_name, _, _ = source
    description = listing_description(meta, body)
    if not description:
        return None
    chars = listing_text_chars(description, meta.get("when_to_use"))
    return {"description": description,
            "source": "frontmatter" if meta.get("description") else "body",
            "description_chars": chars,
            "listing_chars": len(str(meta.get("name") or dir_name)) + min(chars, LISTING_TEXT_CAP)}


def skill_row(skill, review, nested_in: Optional[str] = None, outputs: Iterable[Path] = (),
              ignore: frozenset = frozenset()) -> dict:
    """One skill of the scan report. ``state`` is ``error`` for a load error or an error finding,
    ``warn`` for a warning finding only, ``skipped`` for an unloaded skill that no format rule read (its
    findings left no warning or error, whether or not ``--ignore`` dropped its note), else ``ok``. A skill
    that did not load has a review only when its SKILL.md is plain Markdown or its frontmatter is broken YAML,
    and that review's finding is its only finding.
    ``nested_in`` is the skill whose directory holds this one (None when it is not nested).
    ``outputs`` are the scan's own files, left out of ``skill_sha256``. ``ignore`` drops the
    findings with those codes before the state is taken; ``ignored`` records the codes in effect.
    ``listing_chars`` is the skill's entry in the routing field's listing, None when it is not listed (a skill whose only
    fault is a missing ``description`` is listed by the body's first line, so it has the ``listing`` charge, #1087);
    ``listing`` previews the entry ``repo budget`` charges (``listing_preview``), None when there is none;
    ``listing_name`` is the name the routing field knows the skill by (the loaded name, not the manifest ``name``)."""
    from dokimi.external.catalog_review import entry_relpath
    from dokimi.external.listing import ENTRY_SEP
    from dokimi.external.skill_format import not_a_failed_load
    from dokimi.external.findings import state_of
    from dokimi.external.validate import name_risky_commands

    # An unloaded SKILL.md that is plain Markdown under OpenCode or an unsupported host is a warning or a note here, not a failed load (skill_format).
    exempt = not skill.loaded and not_a_failed_load([f.code for f in review.findings] if review else [])  # before --ignore
    read = _listing_source(skill)
    preview = listing_preview(skill, read)
    listing_chars = listed_chars(review)
    listing_name = skill.skill.name if skill.skill is not None else None
    content_sha256 = review.content_sha256 if review else None
    hidden = read is not None and str(read[1].get("disable-model-invocation", "")).strip().lower() in ("1", "true", "yes", "on")
    if (listing_chars is None and preview is not None and not skill.loaded and not exempt
            and _name_fits_spec(read)
            and "missing a 'description'" in (skill.error or "") and not hidden):
        # Claude Code lists a skill without ``description`` by its body's first line (#943, #1087); the row charges it
        # as ``entry_chars`` charges a loaded skill's ``name: description``, with the separator.
        listing_chars = preview["listing_chars"] + len(ENTRY_SEP)
        # It never loaded, so the listing total groups it by the name it would be listed under and its SKILL.md digest.
        listing_name = str(read[1].get("name") or "").strip() or read[3]
        content_sha256 = read[5]  # the digest of the bytes the preview came from
    kept = name_risky_commands([f for f in review.findings if f.code not in ignore]) if review else []
    state = "error" if not skill.loaded and not exempt else state_of(kept)
    if not skill.loaded and exempt and state == "ok":
        state = "skipped"  # not read by any format rule: not a pass, even when --ignore dropped its note
    return {
        "name": skill.name,
        "manifest_status": None if skill.status is None else str(skill.status),
        "path": str(skill.path) if skill.path else None,
        "relpath": entry_relpath(skill),
        "nested_in": nested_in,
        "state": state,
        "error": None if exempt else skill.error,
        "disable_model_invocation": bool(review and review.disable_model_invocation),
        "content_sha256": content_sha256,
        "skill_sha256": skill_dir_digest(skill, outputs),
        "dokimi_version": __version__,
        "duplicate_of": review.duplicate_of if review else None,
        "listing_chars": listing_chars,
        "listing": preview,
        "listing_name": listing_name,
        "ignored": sorted(ignore),
        "findings": [{"level": f.level, "code": f.code, "message": f.message, **({"script": f.script} if f.script else {})}
                     for f in kept],
    }


def is_plain_markdown(row: dict) -> bool:
    """Whether the row is a ``SKILL.md`` with no frontmatter: plain Markdown, not a broken skill."""
    from dokimi.external.no_frontmatter import NO_FRONTMATTER

    return any(f.get("code") == NO_FRONTMATTER for f in row["findings"])


def plain_note(plain: int) -> str:
    """The summary's note on the errors that are plain Markdown with no frontmatter; empty for none."""
    return f", of them {plain} plain Markdown with no frontmatter" if plain else ""


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
        return tree_digest(path, exclude=skip, strict=True, tracked={})
    except OSError:
        return None


def _outputs(jsonl: Optional[Path]) -> tuple[Path, ...]:
    """The files a ``--jsonl`` scan writes, resolved: the file and the temporary one :func:`_rewrite` replaces it with."""
    if jsonl is None:
        return ()
    out = jsonl.resolve()
    return (out, out.with_name(out.name + ".part"))


def _continued(message: str) -> str:
    """``message`` with its continuation lines indented under the row, so a multi-line message stays in it."""
    return message.replace("\n", "\n    ")


NOT_CHECKED_CODES = ("description-when-not-checked", UNSUPPORTED_CODE)  # the notes that say a skill was not read


def not_checked(row: dict) -> bool:
    """Whether an ``ok`` row is only ok because the check did not read it (a ``NOT_CHECKED_CODES`` note)."""
    return row["state"] == "ok" and any(f["code"] in NOT_CHECKED_CODES for f in row["findings"])


def skipped_note(skipped: int) -> str:
    """The summary's ``N skipped`` for the unloaded skills no format rule read; empty for none, as ``plain_note``."""
    return f", {skipped} skipped" if skipped else ""


def ok_text(ok: int, unchecked: int) -> str:
    """``N ok`` for the counts line, with ``(M not checked)`` when some of them were not read, so they do not read as a pass."""
    return f"{ok} ok ({unchecked} not checked)" if unchecked else f"{ok} ok"


def print_skill_row(row: dict) -> None:
    if row["error"] is not None:
        state = f"ERROR: {_continued(row['error'])}"
    elif row["state"] == "ok" and not not_checked(row):
        state = "ok"
    elif row["state"] == "skipped":
        # An unloaded skill no format rule read: its note says why, and the state does not say ok.
        note = row["findings"][0] if row["findings"] else None
        state = "skipped" if note is None else f"skipped: {note['message']}"
    elif row["state"] == "ok":
        # A "not checked" note: the skill was not read, so it is not a pass and the state does not say ok.
        note = next(f for f in row["findings"] if f["code"] in NOT_CHECKED_CODES)
        state = f"note: {note['message']}"
    else:
        worst = next(f for f in row["findings"] if f["level"] == row["state"])
        state = f"{'ERROR' if row['state'] == 'error' else 'warn'}: {_continued(worst['message'])}"
    print(f"{row['name']}\t{row['manifest_status'] or '-'}\t{row['path'] or '-'}\t{state}")
    for f in row["findings"]:
        print(f"  {f['level']} {f['code']}: {_continued(f['message'])}")
    if row["disable_model_invocation"]:
        print("  note the model is never offered this skill"
              " (disable-model-invocation, or opencode/autoinvoke: false under OpenCode)")
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
    """One ``(entry, nested_in, outputs, ignore)`` item reviewed alone, as a report row. Module level, so a worker can run it."""
    from dokimi.external.validate import review_skills

    entry, nested_in, outputs, ignore = item
    return skill_row(entry, review_skills([entry])[0], nested_in, outputs, ignore)


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
        raise SystemExit("a --concurrency worker process died; the rows written so far are kept: rerun with --resume") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def fails_on_finding(row: dict, fail_on: frozenset[str]) -> bool:
    """Whether a finding left in the row (after ``--ignore``) has a code selected by ``--fail-on-code`` (#942)."""
    return bool(fail_on) and any(f["code"] in fail_on for f in row["findings"])


def unrestricted_and_risky(row: dict) -> bool:
    """Whether the row has ``allowed-tools-unrestricted-bash`` and a risky-command finding: a warn that fails ``--strict``."""
    from dokimi.external.allowed_tools import RISKY_COMMAND_CODES

    codes = {f["code"] for f in row["findings"]}
    return "allowed-tools-unrestricted-bash" in codes and bool(codes & set(RISKY_COMMAND_CODES))


def fail_on_reason(row: dict, fail_on: frozenset[str]) -> Optional[str]:
    """The ``fail-on-code:`` line for a row with a finding selected by ``--fail-on-code`` (#942), else None."""
    if not fails_on_finding(row, fail_on):
        return None
    codes = sorted({f["code"] for f in row["findings"]} & fail_on)
    return f"fail-on-code: {row['name']} has {', '.join(codes)}"


def strict_reason(row: dict) -> Optional[str]:
    """``strict: <skill> ...``, the line that says why ``--strict`` fails a row that is otherwise only a warning
    (unrestricted Bash with a risky command), or None when the row is not such a failure."""
    from dokimi.external.allowed_tools import RISKY_COMMAND_CODES

    if row["error"] is not None or row["state"] == "error" or not unrestricted_and_risky(row):
        return None
    codes = sorted({f["code"] for f in row["findings"]} & set(RISKY_COMMAND_CODES))
    return f"strict: {row['name']} grants unrestricted Bash and has {', '.join(codes)}"


class _Tally:
    """Counts of the rows by state, and whether the scan must exit 1.

    It must on a load error; under ``--strict`` also on a spec error, and on a skill that grants
    unrestricted Bash and also has a risky command (:func:`unrestricted_and_risky`), whose
    :func:`strict_reason` line goes into ``reasons``; and on any row with a finding selected by
    ``--fail-on-code`` (:func:`fails_on_finding`), with a ``fail-on-code:`` line in ``reasons``.
    """

    def __init__(self, strict: bool, fail_on: frozenset[str] = frozenset()) -> None:
        self.strict = strict
        self.fail_on = fail_on
        self.counts = {"ok": 0, "warn": 0, "error": 0, "skipped": 0}
        self.plain = 0  # error rows that are plain Markdown with no frontmatter (is_plain_markdown)
        self.not_checked = 0
        self.listing = ListingTotal()  # the compact count of the rows with a listing_chars; no row is kept for it
        self.failed = False
        self.reasons: list[str] = []

    def add(self, row: dict) -> None:
        self.counts[row["state"]] += 1
        self.plain += is_plain_markdown(row)
        self.not_checked += not_checked(row)
        self.listing.add(row)
        # A SKILL.md the OS will not read after it loaded has no row error, only its unreadable finding: it fails
        # the scan as a load error does, as the whole-catalog path decides it (catalog.py).
        unreadable = any(f.get("code") == "unreadable" for f in row["findings"])
        if row["error"] is not None or unreadable or fails_on_finding(row, self.fail_on) or (self.strict and (row["state"] == "error" or unrestricted_and_risky(row))):  # row error: None for a skill_format exemption
            self.failed = True
        reason = strict_reason(row) if self.strict else None
        if reason is not None:
            self.reasons.append(reason)
        gate = fail_on_reason(row, self.fail_on)
        if gate is not None:
            self.reasons.append(gate)


ROW_STATES = ("ok", "warn", "error", "skipped")


def _is_row(row: object) -> bool:
    return (
        isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and isinstance(row.get("relpath"), (str, type(None)))
        and row.get("state") in ROW_STATES
        and "error" in row
        and isinstance(row.get("findings"), list)
        and _count_ok(row.get("listing_chars"))
        and isinstance(row.get("content_sha256"), (str, type(None)))
        and isinstance(row.get("listing_name"), (str, type(None)))
    )


def _count_ok(value: object) -> bool:
    """A ``listing_chars`` that is null or a nonnegative integer (a bool is not one); absent reads as null."""
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


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


def _load_prior(path: Path, selected: dict[str, Optional[str]], ignore: frozenset) -> tuple[list[dict], bool]:
    """The rows of *path* still current for the skills this run selects, and whether the file must be rewritten.

    *selected* maps each skill's key to its ``skill_sha256`` now. The file is read line by line and only
    the kept rows are held. A rewrite is due when a line is half-written, when the last line has no
    newline (the next row would be glued onto it), or when a row is for a skill this run does not
    select, whose directory changed, was written under other ``--ignore`` codes (the file must
    match the counts printed), was written by another dokimi version (or before the version was
    recorded): a finding a newer version adds is not in the old row, or has no ``listing_chars`` or ``listing_name``.
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
                if (key in selected and "skill_sha256" in row and "listing_chars" in row and "listing" in row and "listing_name" in row and selected[key] is not None and row["skill_sha256"] == selected[key]
                        and row.get("ignored", []) == sorted(ignore) and row.get("dokimi_version") == __version__):
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

    def __init__(self, path: Path, resume: bool, selected: dict[str, Optional[str]], ignore: frozenset) -> None:
        self.path = path
        self.prior: list[dict] = []
        if resume and path.is_file():
            self.prior, dirty = _load_prior(path, selected, ignore)
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
    print(f"# skills ({declared} declared under {root}; {ok_text(counts['ok'], tally.not_checked)}, {counts['warn']} warn, "
          f"{counts['error']} error{plain_note(tally.plain)}{skipped_note(counts['skipped'])}{where})")
    if jsonl is not None:
        print(f"rows written to {jsonl}")
    else:
        for row in rows:
            print_skill_row(row)
    print("# granularity: not run with --per-skill (run without it for the merge and split hints)")
    print()


class ListingTotal:
    """The listing characters of the rows added so far, kept as counts so a streamed row can be freed (#966).

    A byte-identical ``SKILL.md`` under another path (a mirror) is one entry, as ``routing.catalog_competitors``
    keeps one card for it. A skill is a copy of another when the loaded name (``listing_name``, not the manifest
    name) and ``content_sha256`` match; the first row added stands for the group. A name whose copies differ is
    left out whole, as ``routing.catalog_competitors`` drops a conflicting name.
    """

    def __init__(self) -> None:
        self.kept: dict[tuple, int] = {}
        self.digests: dict[str, set] = {}
        self.unkeyed = 0  # rows with no digest or no loaded name: each counts on its own

    def add(self, row: dict) -> None:
        chars = row.get("listing_chars")
        if chars is None:
            return
        sha = row.get("content_sha256")
        name = row.get("listing_name")
        if sha is None or name is None:
            self.unkeyed += chars
            return
        self.digests.setdefault(name, set()).add(sha)
        self.kept.setdefault((name, sha), chars)

    def total(self) -> int:
        return self.unkeyed + sum(chars for (name, _), chars in self.kept.items() if len(self.digests[name]) == 1)


def listing_total(rows: Iterable[dict]) -> int:
    """The characters the routing field lists for ``rows`` (see :class:`ListingTotal`)."""
    listing = ListingTotal()
    for row in rows:
        listing.add(row)
    return listing.total()


def row_ignore(args) -> frozenset:
    """The ``--ignore`` codes a row can carry: ``listing-over-budget`` is the scan's, no row has it, so it is not
    in ``ignored`` and adding or dropping it does not make ``--resume`` review a skill again."""
    return frozenset(args.ignore or ()) - {"listing-over-budget"}


def listing_failed(args, report: dict, as_json: bool) -> bool:
    """Apply ``--listing-budget`` to ``report["listing"]["chars"]``: one rule for every scan path (#966).

    The whole-catalog scan, ``--per-skill``, ``--concurrency`` and ``--resume`` all pass the total of their rows'
    ``listing_chars`` here, so a kept row is checked against the cap of this run, not the one it was written under.
    Over the cap is a ``listing-over-budget`` warning, and exit 1 under ``--strict`` or ``--fail-on-code listing-over-budget``; ``--ignore listing-over-budget``
    drops both and the line that carries the total. ``over_budget`` stays in the report either way.
    Returns True when the scan must exit 1.
    """
    cap = getattr(args, "listing_budget", None)
    listing = report.get("listing")
    if cap is None or listing is None:
        return False
    listing["budget"] = cap
    listing["over_budget"] = listing["chars"] > cap
    if not listing["over_budget"] or "listing-over-budget" in (args.ignore or ()):
        return False
    if not as_json:
        print(f"warn listing-over-budget: the skill listing is {listing['chars']} characters, over the "
              f"--listing-budget of {cap}")
    return bool(args.strict) or "listing-over-budget" in (getattr(args, "fail_on", None) or ())


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
    ignore = row_ignore(args)
    sink = _Jsonl(jsonl, args.resume, selected, ignore) if jsonl is not None else None
    tally = _Tally(args.strict, frozenset(args.fail_on or ()))
    prior = sink.prior if sink is not None else []
    for row in prior:
        tally.add(row)
    done = {_row_key(row) for row in prior}
    todo = [(entry, mark, outputs, ignore) for entry, mark in pairs if _entry_key(entry) not in done]
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
        for line in tally.reasons:
            print(line)
    report["listing"] = {"chars": tally.listing.total()}
    return listing_failed(args, report, as_json) or tally.failed, declared

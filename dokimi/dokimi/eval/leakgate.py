# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Leak gate and split lock for a candidate skill: does it carry held-out answers, and is the split still the one measured?

Leak gate. The expected answers and checks of each eval item are the literal values of its ``grading``
block that are not negated, plus the judge checks' ``reference``. A judge check with no ``reference`` counts
its ``criterion`` instead, at full weight (it is a stand-in for an answer, used only where there is no
reference; there is no lower weight for it). Each text is cut into runs of ``ngram`` words (8 by default;
``\\w+``, lower-cased). An item is flagged when at least ``min_fraction`` of its runs also occur in the skill's
text (SKILL.md and every other UTF-8 file beside it). A second rule flags an item on any single verbatim run of
``verbatim`` words (12 by default) from its expected text, whatever the share: a copied passage can be a small
share of a long answer. Both rules cut the allowlist first. Questions are not compared: a skill may restate a
question. An allowlist of terms (API names, file paths, identifiers that legitimately appear in both) is cut
out of both texts first: its words are dropped, and the words on either side run on, so a copied sentence is
still caught when it holds a term. An item with no run left (fewer than ``ngram`` words once the terms are
cut) is ``unchecked`` and says why; that is not a pass.

Split lock. A split is a JSON object ``{"dev": [ids], "val": [ids], "test": [ids]}`` that puts every question
in exactly one of the three. The lock records, per question id, its split and the SHA-256 of its ``grading``
block; the digest is over the sorted (id, split, grading digest) triples. The lock is written at first use. A
later run whose digest differs is blocked and names the items whose split or grading block changed. With
``relock`` the lock is overwritten instead, and the report keeps the digest it replaced; but only when the run
passes (no leak, no test item given to the proposer, nothing unchecked). A blocked or inconclusive run leaves
the old lock as it was. A run that gives the proposer a test item (a JSON list of question ids) is blocked as well.

The lock is an accidental-edit check, not tamper-proof. Its digest is stored beside the assignment it covers, so
a hand edit that does not recompute the digest is refused; anyone who can write the lock can also recompute it.

Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from dokimi.eval.reference_checks import check_values

DEFAULT_NGRAM = 8
DEFAULT_MIN_FRACTION = 0.10
DEFAULT_VERBATIM = 12
SPLITS = ("dev", "val", "test")
LOCK_VERSION = "split_lock.v1"
# The literal check types whose values are expected answers (regex patterns are not answers, so they are left out).
LITERAL = frozenset({"match", "fuzzy", "exact", "choice", "json"})
PASS, BLOCKED, INCONCLUSIVE = "pass", "blocked", "inconclusive"
_WORD = re.compile(r"\w+")
_SKIP_DIRS = frozenset({".git", "__pycache__"})


def read_skill(path: Path | str, skipped: Optional[list[str]] = None) -> dict[str, str]:
    """The UTF-8 text files of a skill, keyed by path relative to its directory.

    ``path`` is the skill directory, or a file (SKILL.md) whose directory is the skill. Files that are not
    UTF-8 text and symbolic links are not read; each one is named in ``skipped`` (a list the caller passes)
    as a warning, since a file that is not read is not checked. Raises OSError when there is no such directory,
    and ValueError when it holds no text file at all (nothing could be checked).
    """
    root = Path(path)
    if root.is_file():
        root = root.parent
    if not root.is_dir():
        raise OSError(f"{path} is not a skill directory or a SKILL.md file")
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or not (p.is_symlink() or p.is_file()):
            continue
        text = _text_of(p, rel.as_posix(), skipped)
        if text is not None:
            files[rel.as_posix()] = text
    if not files:
        raise ValueError(f"{path}: no UTF-8 text file to check in the skill")
    return files


def _text_of(p: Path, rel: str, skipped: Optional[list[str]]) -> Optional[str]:
    """The text of one skill file, or None when it is a symbolic link or not UTF-8 (named in ``skipped``)."""
    if p.is_symlink():
        if skipped is not None:
            skipped.append(f"{rel}: symbolic link, not read")
        return None
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        if skipped is not None:
            skipped.append(f"{rel}: not UTF-8 text, not read")
        return None


def read_terms(terms: Iterable[str], allow_file: Optional[Path | str] = None) -> list[str]:
    """The allowlist: the terms given, then the non-blank lines of ``allow_file`` that do not start with ``#``."""
    out = [t for t in terms if t.strip()]
    if allow_file is not None:
        for line in Path(allow_file).read_text(encoding="utf-8-sig").splitlines():  # a BOM is not a comment mark
            if line.strip() and not line.strip().startswith("#"):
                out.append(line.strip())
    return out


def read_ids(path: Path | str) -> list[str]:
    """A JSON list of question ids, as strings (the proposer's list)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(_is_id(i) for i in raw):
        raise ValueError(f"{path}: expected a JSON list of question ids")
    return [str(i) for i in raw]


def read_split(path: Path | str) -> dict[str, str]:
    """A split file as {question id: split name}. Each id is in one split; ids are read as strings."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not set(raw) <= set(SPLITS):
        raise ValueError(f"{path}: a split is an object with the keys {', '.join(SPLITS)}")
    assignment: dict[str, str] = {}
    for name in SPLITS:
        ids = raw.get(name, [])
        if not isinstance(ids, list) or not all(_is_id(i) for i in ids):
            raise ValueError(f"{path}: {name} must be a list of question ids")
        for i in ids:
            key = str(i)
            if key in assignment:
                raise ValueError(f"{path}: question id {key} is in both {assignment[key]} and {name}")
            assignment[key] = name
    return assignment


def _is_id(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def match_split(questions: Sequence[Mapping[str, Any]], assignment: Mapping[str, str]) -> None:
    """Raise ValueError unless the split places each question id once and names no other id.

    A question id repeated in the set is refused too: the split cannot say which of its rows is in which split.
    """
    ids = [str(q.get("id")) for q in questions]
    repeated = sorted(i for i, n in Counter(ids).items() if n > 1)
    missing = sorted(set(ids) - assignment.keys())
    extra = sorted(assignment.keys() - set(ids))
    if repeated:
        raise ValueError(f"question ids repeat, so no split can place them: {_listed(repeated)}")
    if missing:
        raise ValueError(f"the split places no question id {_listed(missing)}")
    if extra:
        raise ValueError(f"the split names question ids that are not in the set: {_listed(extra)}")


def _listed(ids: Sequence[str], limit: int = 10) -> str:
    more = f" and {len(ids) - limit} more" if len(ids) > limit else ""
    return ", ".join(ids[:limit]) + more


def item_digest(question: Mapping[str, Any]) -> str:
    """SHA-256 of the question's ``grading`` block, canonically dumped: the expected answers and checks."""
    body = json.dumps(question.get("grading"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()


def item_digests(questions: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Each question id mapped to its :func:`item_digest`."""
    return {str(q.get("id")): item_digest(q) for q in questions}


def assignment_digest(assignment: Mapping[str, str], contents: Mapping[str, str]) -> str:
    """SHA-256 of the sorted (id, split, grading digest) triples. The same split and the same grading blocks,
    however the split file lists them, have the same digest; a changed answer or check changes it too."""
    triples = [[i, assignment[i], contents[i]] for i in sorted(assignment)]
    body = json.dumps(triples, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()


def _counts(assignment: Mapping[str, str]) -> dict[str, int]:
    counts = Counter(assignment.values())
    return {name: counts.get(name, 0) for name in SPLITS}


def _parse_lock(path: Path) -> tuple[dict[str, str], dict[str, str], Any]:
    """The assignment, the grading digests and the stored digest of a split lock. Raises ValueError when the file
    is not a split lock."""
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not a split lock ({e})") from e
    if (not isinstance(lock, dict) or lock.get("schema_version") != LOCK_VERSION
            or not isinstance(lock.get("assignment"), dict) or not isinstance(lock.get("contents"), dict)):
        raise ValueError(f"{path}: not a split lock ({LOCK_VERSION})")
    locked = {str(k): str(v) for k, v in lock["assignment"].items()}
    contents = {str(k): str(v) for k, v in lock["contents"].items()}
    return locked, contents, lock.get("digest")


def _moved(old: Mapping[str, str], old_contents: Mapping[str, str],
           new: Mapping[str, str], new_contents: Mapping[str, str]) -> list[str]:
    """The ids whose split or grading digest differs between the two."""
    return sorted(i for i in set(old) | set(new)
                  if old.get(i) != new.get(i) or old_contents.get(i) != new_contents.get(i))


def lock_split(assignment: Mapping[str, str], contents: Mapping[str, str], lock_path: Path | str, now: str, *,
               relock: bool = False, write_relock: bool = True) -> dict[str, Any]:
    """Lock the split at first use. Returns ``{"state", "moved", "previous_digest"}``.

    ``created``: no lock file existed, so this split is written to it. ``matched``: the lock has the same digest.
    ``changed``: the lock has another digest; nothing is written and ``moved`` lists the ids whose split or grading
    block differs from the lock. ``relocked`` (``relock`` only): the lock is overwritten with this split and
    ``previous_digest`` is the digest it had (None when the old file was not a readable split lock).
    ``not_relocked`` (``relock`` with ``write_relock`` False): the lock would have been overwritten, but the
    caller says the run did not pass, so the old lock stays; ``moved`` and ``previous_digest`` are as for
    ``relocked``. Without ``relock``, raises ValueError when the file is not a split lock, or its digest does not
    match its own assignment (it was edited by hand); with ``relock`` those are overwritten like any other old lock.
    """
    path = Path(lock_path)
    digest = assignment_digest(assignment, contents)
    record = {"schema_version": LOCK_VERSION, "digest": digest, "locked_at": now, "counts": _counts(assignment),
              "assignment": dict(sorted(assignment.items())), "contents": dict(sorted(contents.items()))}

    def write() -> None:
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not path.exists():
        write()
        return {"state": "created", "moved": [], "previous_digest": None}
    try:
        locked, locked_contents, stored = _parse_lock(path)
    except ValueError:
        if not relock:
            raise
        if not write_relock:
            return {"state": "not_relocked", "moved": [], "previous_digest": None}
        write()
        return {"state": "relocked", "moved": [], "previous_digest": None}
    tampered = assignment_digest(locked, locked_contents) != stored if set(locked) == set(locked_contents) else True
    if tampered and not relock:
        raise ValueError(f"{path}: the digest does not match the assignment in the lock; the lock was edited")
    if stored == digest and not tampered:
        return {"state": "matched", "moved": [], "previous_digest": None}
    moved = _moved(locked, locked_contents, assignment, contents)
    if not relock:
        return {"state": "changed", "moved": moved, "previous_digest": None}
    previous = stored if isinstance(stored, str) and re.fullmatch(r"[0-9a-f]{64}", stored) else None
    if not write_relock:
        return {"state": "not_relocked", "moved": moved, "previous_digest": previous}
    write()
    return {"state": "relocked", "moved": moved, "previous_digest": previous}


def exposed_test_items(assignment: Mapping[str, str], proposer_ids: Iterable[str]) -> list[str]:
    """The test items among the question ids the proposer sees. Raises ValueError for an id that is not in the split."""
    ids = {str(i) for i in proposer_ids}
    unknown = sorted(ids - assignment.keys())
    if unknown:
        raise ValueError(f"the proposer list names question ids that are not in the set: {_listed(unknown)}")
    return sorted(i for i in ids if assignment[i] == "test")


def expected_texts(question: Mapping[str, Any]) -> list[str]:
    """The expected answers and checks of one question: the values of its positive literal checks, and the
    reference of its positive judge checks. A judge check with no reference counts its criterion instead: the
    criterion stands in for an answer only where there is none. A negated check names what must be absent, so
    it is not an expected answer and is left out."""
    grading = question.get("grading")
    checks = grading.get("checks") if isinstance(grading, Mapping) else None
    if not isinstance(checks, list):
        return []
    texts: list[str] = []
    for check in checks:
        if not isinstance(check, Mapping) or check.get("negate"):
            continue
        kind = check.get("type")
        # A non-string type (a list, say) is unhashable: refuse it here, as validate_grading does, not at the membership test.
        if not isinstance(kind, str):
            raise ValueError(f"a grading check's type must be a string, not {kind!r}")
        if kind == "judge":
            # judge_point strips both texts and falls back to the criterion when the reference is blank: the gate does too
            reference = str(check.get("reference") or "").strip()
            criterion = str(check.get("criterion") or "").strip()
            if reference or criterion:
                texts.append(reference or criterion)
        elif kind in LITERAL:
            texts += [v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False) for v in check_values(check)]
    return texts


def words(text: str) -> list[str]:
    # NFC first: a combining accent is not a word character, so an NFD copy would split its word
    return _WORD.findall(unicodedata.normalize("NFC", text).lower())


def _allow_runs(terms: Iterable[str]) -> list[tuple[str, ...]]:
    """The allowlist as word tuples, longest first, so a term is cut before a shorter term inside it."""
    runs = {tuple(words(t)) for t in terms}
    runs.discard(())
    # equal lengths are ordered by their words, so the cut does not depend on the hash seed
    return sorted(runs, key=lambda r: (-len(r), r))


def _cut(tokens: list[str], runs: Sequence[tuple[str, ...]]) -> list[str]:
    """``tokens`` with every occurrence of an allowlisted run removed (whole words only)."""
    for run in runs:
        k, pattern, out, i = len(run), list(run), [], 0
        while i < len(tokens):
            if tokens[i:i + k] == pattern:
                i += k
            else:
                out.append(tokens[i])
                i += 1
        tokens = out
    return tokens


def _grams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def skill_index(files: Mapping[str, str], allow: Iterable[str] = (), n: int = DEFAULT_NGRAM) -> dict[tuple[str, ...], set[str]]:
    """Each n-gram of the skill's text (outside the allowlist) mapped to the files that hold it. Files are cut
    separately, so no gram spans two files."""
    runs = _allow_runs(allow)
    index: dict[tuple[str, ...], set[str]] = {}
    for rel, text in files.items():
        for gram in _grams(_cut(words(text), runs), n):
            index.setdefault(gram, set()).add(rel)
    return index


def assess(questions: Sequence[Mapping[str, Any]], index: Mapping[tuple[str, ...], set[str]], allow: Iterable[str] = (), *,
           n: int = DEFAULT_NGRAM, min_fraction: float = DEFAULT_MIN_FRACTION,
           assignment: Optional[Mapping[str, str]] = None,
           verbatim_index: Optional[Mapping[tuple[str, ...], set[str]]] = None,
           verbatim: int = DEFAULT_VERBATIM) -> list[dict[str, Any]]:
    """One row per question: its share of answer n-grams that the skill holds, and whether that flags it.

    With ``verbatim_index`` (the skill's runs of ``verbatim`` words, see :func:`skill_index`), a row also counts
    its verbatim runs and is flagged by any one of them, whatever its share. ``flagged_by`` names the rules that
    flagged it: ``share`` and ``verbatim``.
    """
    runs = _allow_runs(allow)
    rows: list[dict[str, Any]] = []
    for q in questions:
        qid = str(q.get("id"))
        texts = expected_texts(q)
        grams: set[tuple[str, ...]] = set()
        vgrams: set[tuple[str, ...]] = set()
        for text in texts:
            cut = _cut(words(text), runs)
            grams |= _grams(cut, n)
            if verbatim_index is not None:
                vgrams |= _grams(cut, verbatim)
        row: dict[str, Any] = {"question_id": qid, "split": assignment.get(qid) if assignment is not None else None,
                               "grams": len(grams), "shared": 0, "fraction": None, "status": "unchecked",
                               "reason": None, "files": [], "verbatim": 0, "flagged_by": []}
        if not texts:
            row["reason"] = "no literal or judge check to compare"
        elif not grams:
            row["reason"] = f"no {n}-word run left after the allowlist"
        else:
            hit = [g for g in grams if g in index]
            fraction = len(hit) / len(grams)
            row.update(shared=len(hit), fraction=round(fraction, 4),
                       files=sorted({f for g in hit for f in index[g]}), status="clear")
            if hit and fraction >= min_fraction:
                row["flagged_by"].append("share")
        if verbatim_index is not None:
            hits = [g for g in vgrams if g in verbatim_index]
            if hits:
                row["verbatim"] = len(hits)
                row["flagged_by"].append("verbatim")
                row["files"] = sorted(set(row["files"]) | {f for g in hits for f in verbatim_index[g]})
        if row["flagged_by"]:
            row.update(status="flagged", reason=None)
        rows.append(row)
    return rows


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(files: Mapping[str, str], questions: Sequence[Mapping[str, Any]], *, allow: Iterable[str] = (),
        n: int = DEFAULT_NGRAM, min_fraction: float = DEFAULT_MIN_FRACTION, verbatim: int = DEFAULT_VERBATIM,
        assignment: Optional[Mapping[str, str]] = None, lock_path: Optional[Path | str] = None,
        proposer_ids: Optional[Iterable[str]] = None, now: Optional[str] = None,
        relock: bool = False) -> dict[str, Any]:
    """Check the skill against the items, and the split against its lock. Raises ValueError for input that cannot
    be checked (a split that does not match the set, a proposer list with no split, a bad threshold or verbatim
    length, a relock with no split or no lock).

    ``lock_path`` locks the split (created, matched, changed or, with ``relock``, relocked); without it the split
    is only read. ``relock`` overwrites a lock whose digest differs and records the digest it replaced, but only
    when the run passes (no violation, nothing unchecked); a blocked or inconclusive run leaves the old lock as it
    was (state ``not_relocked``). ``proposer_ids`` are the question ids the proposer is given: a test item among
    them is a violation. ``verbatim`` is the run length of the verbatim rule.
    """
    if not 0 < min_fraction <= 1:
        raise ValueError(f"min_fraction must be above 0 and at most 1, got {min_fraction}")
    if verbatim < 1:
        raise ValueError(f"verbatim must be at least 1 word, got {verbatim}")
    if proposer_ids is not None and assignment is None:
        raise ValueError("a proposer list needs a split to tell the test items")
    if relock and (assignment is None or lock_path is None):
        raise ValueError("relock needs a split and its lock")
    allow = list(allow)
    violations: list[dict[str, Any]] = []
    split: Optional[dict[str, Any]] = None
    exposed: list[str] = []
    contents: dict[str, str] = {}
    if assignment is not None:
        match_split(questions, assignment)
        contents = item_digests(questions)
        split = {"state": "not_locked", "digest": assignment_digest(assignment, contents),
                 "previous_digest": None, "counts": _counts(assignment), "moved": []}
        if proposer_ids is not None:
            exposed = exposed_test_items(assignment, proposer_ids)
            violations += [{"kind": "test_exposed", "question_id": i} for i in exposed]
    rows = assess(questions, skill_index(files, allow, n), allow, n=n, min_fraction=min_fraction,
                  assignment=assignment, verbatim_index=skill_index(files, allow, verbatim), verbatim=verbatim)
    violations += [{"kind": "leak", "question_id": r["question_id"]} for r in rows if r["status"] == "flagged"]
    unchecked = sum(r["status"] == "unchecked" for r in rows)
    if assignment is not None and split is not None and lock_path is not None:
        # decided after the leak and proposer checks: a relock is written only by a run that passes
        passing = not violations and bool(rows) and not unchecked
        result_lock = lock_split(assignment, contents, lock_path, now or _utc_now(), relock=relock,
                                 write_relock=passing)
        split.update(state=result_lock["state"], moved=result_lock["moved"],
                     previous_digest=result_lock["previous_digest"])
        if result_lock["state"] == "changed":
            violations.append({"kind": "split_changed", "moved": result_lock["moved"]})
    summary = {"items": len(rows), "checked": len(rows) - unchecked,
               "flagged": sum(r["status"] == "flagged" for r in rows), "unchecked": unchecked,
               "exposed": len(exposed), "violations": len(violations)}
    if violations:
        status = BLOCKED
    else:
        status = PASS if rows and not unchecked else INCONCLUSIVE
    return {
        "status": status,
        "ngram": n,
        "min_fraction": min_fraction,
        "verbatim_words": verbatim,
        "allow": sorted(set(allow)),
        "skill_files": sorted(files),
        "split": split,
        "exposed_test_items": exposed,
        "items": rows,
        "violations": violations,
        "summary": summary,
    }


def _split_lines(split: Mapping[str, Any]) -> list[str]:
    """The split line and what the lock did or would do (changed, relocked, not relocked)."""
    counts = ", ".join(f"{name} {n}" for name, n in split["counts"].items())
    lines = [f"split {split['state'].replace('_', ' ')}: digest {split['digest'][:12]} ({counts})"]
    old = split["previous_digest"]
    shown = old[:12] if old else "unreadable"
    moved = split["moved"]
    if split["state"] == "changed":
        lines.append(f"  split changed since its lock: {len(moved)} items moved: {_listed(moved)}")
    if split["state"] == "relocked":
        lines.append(f"  lock overwritten (--relock): previous digest {shown}")
        if moved:
            lines.append(f"  {len(moved)} items moved since the old lock: {_listed(moved)}")
    if split["state"] == "not_relocked":
        lines.append(f"  lock not overwritten: the run did not pass, so the old lock stands (digest {shown})")
        if moved:
            lines.append(f"  {len(moved)} items would move at relock: {_listed(moved)}")
    return lines


def _flagged_lines(flagged: Sequence[Mapping[str, Any]], output: Any) -> list[str]:
    """The flagged items as a table: at most 20 rows, the rest named as in the artifact or the rerun hint."""
    lines: list[str] = []
    if flagged:
        lines.append(f"{'item':<24} {'split':<6} {'shared':>9} {'frac':>6} {'verbatim':>8}  files")
    for r in flagged[:20]:
        shared = f"{r['shared']}/{r['grams']}"
        frac = "-" if r["fraction"] is None else f"{r['fraction']:.2f}"
        files = ", ".join(r["files"][:3]) + (f" and {len(r['files']) - 3} more" if len(r["files"]) > 3 else "")
        lines.append(f"{r['question_id'][:24]:<24} {r['split'] or '-':<6} {shared:>9} {frac:>6} "
                     f"{r['verbatim']:>8}  {files}")
    if len(flagged) > 20:
        more = len(flagged) - 20
        lines.append(f"  ... {more} more flagged items in the artifact" if output
                     else f"  ... {more} more flagged items; rerun with --out FILE to list them all")
    return lines


def format_table(result: Mapping[str, Any], output: Any = None) -> str:
    """The report: the split line, the warnings, the flagged items, the unchecked items and the status."""
    lines: list[str] = []
    if result["split"] is not None:
        lines.extend(_split_lines(result["split"]))
    for warning in result.get("warnings") or []:
        lines.append(f"warning: {warning}")
    if result["exposed_test_items"]:
        lines.append(f"  test items given to the proposer: {_listed(result['exposed_test_items'])}")
    lines.extend(_flagged_lines([r for r in result["items"] if r["status"] == "flagged"], output))
    unchecked = [f"question {r['question_id']} ({r['reason']})" for r in result["items"] if r["status"] == "unchecked"]
    if unchecked:
        lines.append(f"  not checked: {', '.join(unchecked[:20])}"
                     + (f", and {len(unchecked) - 20} more" if len(unchecked) > 20 else ""))
    s = result["summary"]
    lines.append(f"status {result['status']}: {s['items']} items, {s['checked']} checked, {s['flagged']} flagged, "
                 f"{s['unchecked']} unchecked, {s['exposed']} test items given to the proposer, "
                 f"{s['violations']} violations")
    return "\n".join(lines)

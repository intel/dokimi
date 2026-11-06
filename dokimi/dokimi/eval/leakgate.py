# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Leak gate and split lock for a candidate skill: does it carry held-out answers, and is the split still the one measured?

Leak gate. The expected answers and checks of each eval item are the literal values of its ``grading``
block that are not negated, plus the ``reference`` and ``criterion`` of its judge checks. Each text is cut
into runs of ``ngram`` words (8 by default; ``\\w+``, lower-cased). An item is flagged when at least
``min_fraction`` of its runs also occur in the skill's text (SKILL.md and every other UTF-8 file beside it).
Questions are not compared: a skill may restate a question. An allowlist of terms (API names, file paths,
identifiers that legitimately appear in both) is cut out of both texts first: its words are dropped, and the
words on either side run on, so a copied sentence is still caught when it holds a term. An item with no run
left (fewer than ``ngram`` words once the terms are cut) is ``unchecked`` and says why; that is not a pass.

Split lock. A split is a JSON object ``{"dev": [ids], "val": [ids], "test": [ids]}`` that puts every question
in exactly one of the three. Its digest (over the sorted id -> split pairs) is written to a lock file at first
use; a later run whose assignment digests differently is blocked and names the items that moved. A run that
gives the proposer a test item (a JSON list of question ids) is blocked as well.

Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from dokimi.eval.reference_checks import check_values

DEFAULT_NGRAM = 8
DEFAULT_MIN_FRACTION = 0.15
SPLITS = ("dev", "val", "test")
LOCK_VERSION = "split_lock.v1"
# The literal check types whose values are expected answers (regex patterns are not answers, so they are left out).
LITERAL = frozenset({"match", "fuzzy", "exact", "choice", "json"})
PASS, BLOCKED, INCONCLUSIVE = "pass", "blocked", "inconclusive"
_WORD = re.compile(r"\w+")
_SKIP_DIRS = frozenset({".git", "__pycache__"})


def read_skill(path: Path | str) -> dict[str, str]:
    """The UTF-8 text files of a skill, keyed by path relative to its directory.

    ``path`` is the skill directory, or a file (SKILL.md) whose directory is the skill. Files that are not
    UTF-8 text and symbolic links are not read. Raises OSError when there is no such directory, and
    ValueError when it holds no text file at all (nothing could be checked).
    """
    root = Path(path)
    if root.is_file():
        root = root.parent
    if not root.is_dir():
        raise OSError(f"{path} is not a skill directory or a SKILL.md file")
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or p.is_symlink() or not p.is_file():
            continue
        try:
            files[rel.as_posix()] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    if not files:
        raise ValueError(f"{path}: no UTF-8 text file to check in the skill")
    return files


def read_terms(terms: Iterable[str], allow_file: Optional[Path | str] = None) -> list[str]:
    """The allowlist: the terms given, then the non-blank lines of ``allow_file`` that do not start with ``#``."""
    out = [t for t in terms if t.strip()]
    if allow_file is not None:
        for line in Path(allow_file).read_text(encoding="utf-8").splitlines():
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


def assignment_digest(assignment: Mapping[str, str]) -> str:
    """SHA-256 of the sorted (id, split) pairs: the same split, however the file lists it, has the same digest."""
    body = json.dumps(sorted(assignment.items()), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _counts(assignment: Mapping[str, str]) -> dict[str, int]:
    counts = Counter(assignment.values())
    return {name: counts.get(name, 0) for name in SPLITS}


def lock_split(assignment: Mapping[str, str], lock_path: Path | str, now: str) -> tuple[str, list[str]]:
    """Lock the assignment at first use. Returns ``(state, moved)``.

    ``created``: no lock file existed, so this assignment is written to it. ``matched``: the lock has the same
    digest. ``changed``: the lock has another digest; nothing is written and ``moved`` lists the ids whose split
    differs from the lock. Raises ValueError when the file is not a split lock, or its digest does not match its
    own assignment (it was edited by hand).
    """
    path = Path(lock_path)
    digest = assignment_digest(assignment)
    if not path.exists():
        record = {"schema_version": LOCK_VERSION, "digest": digest, "locked_at": now,
                  "counts": _counts(assignment), "assignment": dict(sorted(assignment.items()))}
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return "created", []
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not a split lock ({e})") from e
    if not isinstance(lock, dict) or lock.get("schema_version") != LOCK_VERSION or not isinstance(lock.get("assignment"), dict):
        raise ValueError(f"{path}: not a split lock ({LOCK_VERSION})")
    locked = {str(k): str(v) for k, v in lock["assignment"].items()}
    if assignment_digest(locked) != lock.get("digest"):
        raise ValueError(f"{path}: the digest does not match the assignment in the lock; the lock was edited")
    if lock["digest"] == digest:
        return "matched", []
    moved = sorted(i for i in set(locked) | set(assignment) if locked.get(i) != assignment.get(i))
    return "changed", moved


def exposed_test_items(assignment: Mapping[str, str], proposer_ids: Iterable[str]) -> list[str]:
    """The test items among the question ids the proposer sees. Raises ValueError for an id that is not in the split."""
    ids = {str(i) for i in proposer_ids}
    unknown = sorted(ids - assignment.keys())
    if unknown:
        raise ValueError(f"the proposer list names question ids that are not in the set: {_listed(unknown)}")
    return sorted(i for i in ids if assignment[i] == "test")


def expected_texts(question: Mapping[str, Any]) -> list[str]:
    """The expected answers and checks of one question: the values of its positive literal checks, and the
    reference and criterion of its positive judge checks. A negated check names what must be absent, so it is
    not an expected answer and is left out."""
    grading = question.get("grading")
    checks = grading.get("checks") if isinstance(grading, Mapping) else None
    if not isinstance(checks, list):
        return []
    texts: list[str] = []
    for check in checks:
        if not isinstance(check, Mapping) or check.get("negate"):
            continue
        kind = check.get("type")
        if kind == "judge":
            texts += [str(check[key]) for key in ("reference", "criterion") if check.get(key)]
        elif kind in LITERAL:
            texts += [v if isinstance(v, str) else json.dumps(v, sort_keys=True) for v in check_values(check)]
    return texts


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _allow_runs(terms: Iterable[str]) -> list[tuple[str, ...]]:
    """The allowlist as word tuples, longest first, so a term is cut before a shorter term inside it."""
    runs = {tuple(words(t)) for t in terms}
    runs.discard(())
    return sorted(runs, key=len, reverse=True)


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
           assignment: Optional[Mapping[str, str]] = None) -> list[dict[str, Any]]:
    """One row per question: its share of answer n-grams that the skill holds, and whether that flags it."""
    runs = _allow_runs(allow)
    rows: list[dict[str, Any]] = []
    for q in questions:
        qid = str(q.get("id"))
        texts = expected_texts(q)
        grams: set[tuple[str, ...]] = set()
        for text in texts:
            grams |= _grams(_cut(words(text), runs), n)
        row: dict[str, Any] = {"question_id": qid, "split": assignment.get(qid) if assignment is not None else None,
                               "grams": len(grams), "shared": 0, "fraction": None, "status": "unchecked",
                               "reason": None, "files": []}
        if not texts:
            row["reason"] = "no literal or judge check to compare"
        elif not grams:
            row["reason"] = f"no {n}-word run left after the allowlist"
        else:
            hit = [g for g in grams if g in index]
            fraction = len(hit) / len(grams)
            row.update(shared=len(hit), fraction=round(fraction, 4),
                       files=sorted({f for g in hit for f in index[g]}),
                       status="flagged" if hit and fraction >= min_fraction else "clear")
        rows.append(row)
    return rows


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(files: Mapping[str, str], questions: Sequence[Mapping[str, Any]], *, allow: Iterable[str] = (),
        n: int = DEFAULT_NGRAM, min_fraction: float = DEFAULT_MIN_FRACTION,
        assignment: Optional[Mapping[str, str]] = None, lock_path: Optional[Path | str] = None,
        proposer_ids: Optional[Iterable[str]] = None, now: Optional[str] = None) -> dict[str, Any]:
    """Check the skill against the items, and the split against its lock. Raises ValueError for input that cannot
    be checked (a split that does not match the set, a proposer list with no split, a bad threshold).

    ``lock_path`` locks the split (created, matched or changed); without it the split is only read. ``proposer_ids``
    are the question ids the proposer is given: a test item among them is a violation.
    """
    if not 0 < min_fraction <= 1:
        raise ValueError(f"min_fraction must be above 0 and at most 1, got {min_fraction}")
    if proposer_ids is not None and assignment is None:
        raise ValueError("a proposer list needs a split to tell the test items")
    allow = list(allow)
    violations: list[dict[str, Any]] = []
    split: Optional[dict[str, Any]] = None
    exposed: list[str] = []
    if assignment is not None:
        match_split(questions, assignment)
        split = {"state": "not_locked", "digest": assignment_digest(assignment), "counts": _counts(assignment), "moved": []}
        if lock_path is not None:
            state, moved = lock_split(assignment, lock_path, now or _utc_now())
            split.update(state=state, moved=moved)
            if state == "changed":
                violations.append({"kind": "split_changed", "moved": moved})
        if proposer_ids is not None:
            exposed = exposed_test_items(assignment, proposer_ids)
            violations += [{"kind": "test_exposed", "question_id": i} for i in exposed]
    rows = assess(questions, skill_index(files, allow, n), allow, n=n, min_fraction=min_fraction, assignment=assignment)
    violations += [{"kind": "leak", "question_id": r["question_id"]} for r in rows if r["status"] == "flagged"]
    unchecked = sum(r["status"] == "unchecked" for r in rows)
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
        "allow": sorted(set(allow)),
        "skill_files": sorted(files),
        "split": split,
        "exposed_test_items": exposed,
        "items": rows,
        "violations": violations,
        "summary": summary,
    }


def format_table(result: Mapping[str, Any], output: Any = None) -> str:
    """The report: the split line, the flagged items, the unchecked items and the status."""
    lines: list[str] = []
    split = result["split"]
    if split is not None:
        counts = ", ".join(f"{name} {n}" for name, n in split["counts"].items())
        lines.append(f"split {split['state'].replace('_', ' ')}: digest {split['digest'][:12]} ({counts})")
        if split["state"] == "changed":
            lines.append(f"  split changed since its lock: {len(split['moved'])} items moved: {_listed(split['moved'])}")
    if result["exposed_test_items"]:
        lines.append(f"  test items given to the proposer: {_listed(result['exposed_test_items'])}")
    flagged = [r for r in result["items"] if r["status"] == "flagged"]
    if flagged:
        lines.append(f"{'item':<24} {'split':<6} {'shared':>9} {'frac':>6}  files")
    for r in flagged[:20]:
        shared = f"{r['shared']}/{r['grams']}"
        files = ", ".join(r["files"][:3]) + (f" and {len(r['files']) - 3} more" if len(r["files"]) > 3 else "")
        lines.append(f"{r['question_id'][:24]:<24} {r['split'] or '-':<6} {shared:>9} {r['fraction']:>6.2f}  {files}")
    if len(flagged) > 20:
        more = len(flagged) - 20
        lines.append(f"  ... {more} more flagged items in the artifact" if output
                     else f"  ... {more} more flagged items; rerun with --out FILE to list them all")
    unchecked = [f"question {r['question_id']} ({r['reason']})" for r in result["items"] if r["status"] == "unchecked"]
    if unchecked:
        lines.append(f"  not checked: {', '.join(unchecked[:20])}"
                     + (f", and {len(unchecked) - 20} more" if len(unchecked) > 20 else ""))
    s = result["summary"]
    lines.append(f"status {result['status']}: {s['items']} items, {s['checked']} checked, {s['flagged']} flagged, "
                 f"{s['unchecked']} unchecked, {s['exposed']} test items given to the proposer, "
                 f"{s['violations']} violations")
    return "\n".join(lines)

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exploration-quality metrics: citation parsing and localization scoring.

Scores whether an agent (or an exploration subagent) located the *right* files
and line ranges, independent of whether it went on to answer or solve.

A FastContext-style explorer returns a compact citation block::

    <final_answer>
    src/pkg/mod.py:42-58
    tests/test_mod.py:101-119
    </final_answer>

This module parses that block and scores the predicted citations against a set
of reference locations (oracle-diff-derived or hand-curated) at file and line
granularity, using precision / recall / F1 plus a compactness ratio.

The module is deliberately pure — no filesystem or network access — so it is
fully unit-testable. Line-level scoring uses interval arithmetic rather than
materializing per-line sets, so a citation spanning thousands of lines costs
nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Optional, Sequence

# A citation line: a path token, optionally followed by ``:start`` or
# ``:start-end`` (also accepts an en-dash and ``start:end``). The path may be
# wrapped in backticks or surrounded by list/bullet noise, which we strip first.
_RANGE_RE = re.compile(
    r"^(?P<path>[^\s:`]+)"
    r"(?::(?P<start>\d+)(?:\s*[-–:]\s*(?P<end>\d+))?)?$"
)
_FINAL_ANSWER_RE = re.compile(
    r"<final_answer>(.*?)</final_answer>", re.IGNORECASE | re.DOTALL
)
# Fallback when no <final_answer> block exists: scan for path:line(-line) tokens. A
# wrapping pair around the whole citation, ``(src/a.py:10)``, is not part of the path;
# a leading bracket with no closer after the range, ``(app)/page.tsx:10``, is.
_LOOSE_CITATION_RE = re.compile(
    r"(?P<open>[(\[{<\"'])?"
    r"(?P<path>[^\s:`]+\.[A-Za-z0-9_+]+)`?"
    r":(?P<start>\d+)(?:\s*[-–:]\s*(?P<end>\d+))?"
    r"(?P<close>[)\]}>\"'])?"
)
_WRAPPERS = {"(": ")", "[": "]", "{": "}", "<": ">", '"': '"', "'": "'"}


@dataclass(frozen=True)
class Citation:
    """One parsed citation. ``start``/``end`` are ``None`` for a whole-file cite."""

    path: str
    start: Optional[int] = None
    end: Optional[int] = None

    @property
    def is_whole_file(self) -> bool:
        return self.start is None

    @property
    def line_count(self) -> int:
        if self.start is None:
            return 0
        end = self.end if self.end is not None else self.start
        return max(0, end - self.start + 1)


@dataclass(frozen=True)
class CitationSet:
    """The result of parsing a ``<final_answer>`` block."""

    citations: tuple[Citation, ...] = ()
    malformed: tuple[str, ...] = ()
    overlapping: int = 0

    @property
    def files(self) -> set[str]:
        return {c.path for c in self.citations}

    @property
    def validity_rate(self) -> float:
        """Well-formed citations / (well-formed + malformed).

        ``1.0`` when nothing was emitted (vacuously valid) — there is no
        invalid citation to penalize. Use the ``malformed`` count directly when
        an empty answer should itself be treated as a failure.
        """
        total = len(self.citations) + len(self.malformed)
        return 1.0 if total == 0 else len(self.citations) / total


@dataclass(frozen=True)
class ReferenceLocations:
    """Ground-truth locations for one task.

    ``files`` maps a (normalized) path to its reference line ranges. An empty
    range tuple means a whole-file reference: it scores at file granularity but
    contributes no reference lines to line-level metrics.
    """

    files: Mapping[str, tuple[tuple[int, int], ...]]

    @property
    def paths(self) -> set[str]:
        return {normalize_path(p) for p in self.files}


@dataclass(frozen=True)
class LocalizationScore:
    """Precision/recall/F1 at file and line granularity, plus compactness."""

    citation_validity_rate: float
    file_precision: float
    file_recall: float
    file_f1: float
    # Line metrics are ``None`` when the reference declares no line ranges
    # (whole-file-only task): there is no line-level ground truth, so an empty or
    # whole-file prediction must not score as a perfect line match.
    line_precision: Optional[float]
    line_recall: Optional[float]
    line_f1: Optional[float]
    # Cited lines / reference lines. <1 is tight, >1 over-broad. ``None`` when
    # the reference has no line ranges to compare against.
    citation_compactness: Optional[float]

    def as_dict(self) -> dict:
        return {
            "citation_validity_rate": round(self.citation_validity_rate, 4),
            "file_precision": round(self.file_precision, 4),
            "file_recall": round(self.file_recall, 4),
            "file_f1": round(self.file_f1, 4),
            "line_precision": _round_opt(self.line_precision),
            "line_recall": _round_opt(self.line_recall),
            "line_f1": _round_opt(self.line_f1),
            "citation_compactness": _round_opt(self.citation_compactness),
        }


def _round_opt(value: Optional[float]) -> Optional[float]:
    return round(value, 4) if value is not None else None


def normalize_path(path: str) -> str:
    """Normalize a cited path for comparison.

    Strips wrapping backticks/quotes, leading ``./`` and ``/``, and collapses
    backslashes to forward slashes. Deliberately does not touch the filesystem.
    """
    p = path.strip().strip("`").strip("'\"")
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _clean_citation_line(line: str) -> str:
    """Strip list bullets, numbering, and surrounding markup from a line."""
    text = line.strip()
    # leading bullets / numbering: "- ", "* ", "1. ", "1) ", or a bare bullet
    # with nothing after it (``\s*`` — the outer strip already removed trailing
    # space, so a bullet-only line like "- " arrives as "-").
    text = re.sub(r"^([-*+]\s*|\d+[.)]\s*)", "", text)
    # The model quoted only the path, so the backticks close before the range.
    text = re.sub(r"^`([^\s:`]+)`(?=:\d)", r"\1", text.strip())
    text = text.strip().strip("`").strip()
    # A line with no alphanumerics (a stray/unicode bullet) is not a citation;
    # treat it as blank so it never parses into a phantom Citation(path="-").
    return text if any(c.isalnum() for c in text) else ""


def parse_final_answer(text: str) -> CitationSet:
    """Parse a ``<final_answer>`` citation block from ``text``.

    Uses the last ``<final_answer>…</final_answer>`` block when present (the
    explorer may reason before committing). Each non-empty line inside is
    expected to be a citation; lines that fail to parse are recorded as
    ``malformed`` so a validity rate can be computed. When no block is present,
    falls back to scanning the whole text for ``path:line`` tokens (and reports
    no malformed lines, since intent cannot be inferred outside the block).
    """
    blocks = _FINAL_ANSWER_RE.findall(text or "")
    if blocks:
        return _parse_block(blocks[-1])
    return _parse_loose(text or "")


def _parse_block(block: str) -> CitationSet:
    citations: list[Citation] = []
    malformed: list[str] = []
    for raw in block.splitlines():
        cleaned = _clean_citation_line(raw)
        if not cleaned:
            continue
        cite = _parse_one(cleaned)
        if cite is None:
            malformed.append(cleaned)
        else:
            citations.append(cite)
    return _finish(citations, malformed)


def _parse_loose(text: str) -> CitationSet:
    citations: list[Citation] = []
    for m in _LOOSE_CITATION_RE.finditer(text):
        opener = m.group("open")
        wrapped = opener is not None and _WRAPPERS[opener] == m.group("close")
        cite = _citation_from_match(m)
        if opener is not None and not wrapped:
            cite = replace(cite, path=normalize_path(opener + m.group("path")))
        citations.append(cite)
    return _finish(citations, [])


def citation_path(entry: str) -> str:
    """Normalized file path from a single citation entry.

    Accepts a bare path, ``path:line``, or ``path:start-end`` and returns just
    the file portion, stripping any line range — so a cited ``src/a.py:10-20``
    compares equal to a plain ``src/a.py`` read. Falls back to
    :func:`normalize_path` when the entry has no recognizable citation shape.
    """
    cite = _parse_one(_clean_citation_line(entry))
    return cite.path if cite is not None else normalize_path(entry)


def _parse_one(token: str) -> Optional[Citation]:
    m = _RANGE_RE.match(token)
    if not m:
        return None
    return _citation_from_match(m)


def _citation_from_match(m: "re.Match[str]") -> Citation:
    path = normalize_path(m.group("path"))
    start = m.group("start")
    end = m.group("end")
    if start is None:
        return Citation(path=path)
    start_i = int(start)
    end_i = int(end) if end is not None else start_i
    if end_i < start_i:
        start_i, end_i = end_i, start_i
    return Citation(path=path, start=start_i, end=end_i)


def _finish(citations: list[Citation], malformed: list[str]) -> CitationSet:
    overlapping = _count_overlaps(citations)
    return CitationSet(
        citations=tuple(citations),
        malformed=tuple(malformed),
        overlapping=overlapping,
    )


def _count_overlaps(citations: Sequence[Citation]) -> int:
    """Count citations whose range overlaps an earlier citation of the same file."""
    seen: dict[str, list[tuple[int, int]]] = {}
    overlaps = 0
    for c in citations:
        if c.start is None:
            continue
        end = c.end if c.end is not None else c.start
        prior = seen.setdefault(c.path, [])
        if any(c.start <= pe and ps <= end for ps, pe in prior):
            overlaps += 1
        prior.append((c.start, end))
    return overlaps


# ── scoring ─────────────────────────────────────────────────────────────────


def score_localization(
    pred: CitationSet, reference: ReferenceLocations
) -> LocalizationScore:
    """Score predicted citations against reference locations.

    File-level metrics treat every predicted/reference path as a set element.
    Line-level metrics use interval arithmetic over *line-bounded* citations
    only; a whole-file citation scores at file granularity but contributes no
    predicted lines (citing a whole file is not localization). Compactness is
    cited lines / reference lines (``None`` when the reference has no ranges).
    """
    pred_paths = {c.path for c in pred.citations}
    ref_paths = reference.paths

    file_p, file_r, file_f1 = _prf(
        tp=len(pred_paths & ref_paths),
        pred_total=len(pred_paths),
        ref_total=len(ref_paths),
    )

    # Two keys that normalize to one path (``a.py`` and ``./a.py``) pool their
    # ranges rather than the later key replacing the earlier.
    pooled: dict[str, list[tuple[int, int]]] = {}
    for p, ranges in reference.files.items():
        if ranges:
            pooled.setdefault(normalize_path(p), []).extend(ranges)
    ref_intervals = {p: _merge(ranges) for p, ranges in pooled.items()}
    # Line metrics are only defined over files that have ranged ground truth.
    # Predicted ranges for a file referenced whole-file (or not referenced at
    # all) have no line-level truth to score against, so they must not count as
    # line-level false positives — that is a file-level concern, already scored
    # by file precision/recall above.
    ranged_files = set(ref_intervals)
    pred_intervals = {
        path: iv
        for path, iv in _intervals_by_file(pred.citations).items()
        if path in ranged_files
    }

    pred_lines = sum(_length(iv) for iv in pred_intervals.values())
    ref_lines = sum(_length(iv) for iv in ref_intervals.values())
    overlap_lines = sum(
        _overlap_length(pred_intervals[path], ref_intervals[path])
        for path in pred_intervals.keys() & ref_intervals.keys()
    )

    if ref_lines == 0:
        # No line-level ground truth (whole-file-only task): line metrics are
        # undefined rather than a vacuous perfect/zero score.
        line_p = line_r = line_f1 = None
    else:
        line_p, line_r, line_f1 = _prf(
            tp=overlap_lines, pred_total=pred_lines, ref_total=ref_lines
        )

    # Compactness compares cited lines to reference lines. It is undefined
    # (``None``) when there are no predicted lines — a whole-file-only or empty
    # prediction has nothing to measure tightness on, and must NOT score as a
    # maximally compact ``0.0`` (which would rank over-broad whole-file answers
    # above tight range citations).
    compactness = pred_lines / ref_lines if (pred_lines and ref_lines) else None

    return LocalizationScore(
        citation_validity_rate=pred.validity_rate,
        file_precision=file_p,
        file_recall=file_r,
        file_f1=file_f1,
        line_precision=line_p,
        line_recall=line_r,
        line_f1=line_f1,
        citation_compactness=compactness,
    )


def _intervals_by_file(citations: Iterable[Citation]) -> dict[str, list[tuple[int, int]]]:
    by_file: dict[str, list[tuple[int, int]]] = {}
    for c in citations:
        if c.start is None:
            continue
        end = c.end if c.end is not None else c.start
        by_file.setdefault(c.path, []).append((c.start, end))
    return {path: _merge(ranges) for path, ranges in by_file.items()}


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent inclusive integer ranges."""
    norm = sorted((min(a, b), max(a, b)) for a, b in ranges)
    merged: list[tuple[int, int]] = []
    for start, end in norm:
        if merged and start <= merged[-1][1] + 1:
            prev_s, prev_e = merged[-1]
            merged[-1] = (prev_s, max(prev_e, end))
        else:
            merged.append((start, end))
    return merged


def _length(intervals: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in intervals)


def _overlap_length(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> int:
    """Total length of the intersection of two merged interval lists."""
    total = 0
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi:
            total += hi - lo + 1
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _prf(tp: int, pred_total: int, ref_total: int) -> tuple[float, float, float]:
    """Precision/recall/F1 with degenerate cases defined.

    When there is nothing to find *and* nothing was predicted, all three are
    ``1.0`` (a perfect vacuous match). Otherwise an empty side yields ``0.0``.
    """
    if pred_total == 0 and ref_total == 0:
        return 1.0, 1.0, 1.0
    precision = tp / pred_total if pred_total else 0.0
    recall = tp / ref_total if ref_total else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return precision, recall, f1

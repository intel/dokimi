# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Candidate pairs for the repo budget's near-duplicate pass (``budget._near_duplicates``).

A pair is reported when the two word sets are equal and non-empty, or when their Jaccard overlap is at
least ``DUPLICATE_JACCARD`` and each has 3+ words. ``candidate_pairs`` yields those pairs one by one without
looking at every pair: equal sets are grouped, and the rest are found by a prefix filter (rarest words
first), each candidate checked on the spot. https://github.com/intel/dokimi/issues/995
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterator
from itertools import combinations

MIN_PARTIAL_WORDS = 3
# Only lowers a bound, so a pair at exactly the threshold is never dropped to rounding.
_EPS = 1e-9


def _prefix_length(size: int, jaccard: float) -> int:
    """Words of a ``size``-word set to index: two sets with Jaccard >= J share >= ceil(J * size) words."""
    return size - math.ceil(jaccard * size - _EPS) + 1


def _reaches(a: set[str], b: set[str], jaccard: float) -> bool:
    return len(a & b) / len(a | b) >= jaccard


def _exact_pairs(words: list[set[str]]) -> Iterator[tuple[int, int]]:
    groups: dict[frozenset[str], list[int]] = {}
    for k, w in enumerate(words):
        if w:
            groups.setdefault(frozenset(w), []).append(k)
    for group in groups.values():
        yield from combinations(group, 2)


def _partial_pairs(words: list[set[str]], jaccard: float) -> Iterator[tuple[int, int]]:
    """Pairs of unequal sets that reach ``jaccard``; equal sets are ``_exact_pairs``'s."""
    freq = Counter(w for ws in words if len(ws) >= MIN_PARTIAL_WORDS for w in ws)
    order = sorted((k for k, ws in enumerate(words) if len(ws) >= MIN_PARTIAL_WORDS), key=lambda k: (len(words[k]), k))
    index: dict[str, list[int]] = {}
    for k in order:
        size = len(words[k])
        prefix = sorted(words[k], key=lambda w: (freq[w], w))[: _prefix_length(size, jaccard)]
        near = {o for w in prefix for o in index.get(w, ())}
        # Only a pair that reaches J is yielded, so a catalog of shared boilerplate yields no false candidates.
        for o in near:
            if words[k] != words[o] and _reaches(words[k], words[o], jaccard):
                yield min(k, o), max(k, o)
        for w in prefix:
            index.setdefault(w, []).append(k)


def candidate_pairs(words: list[set[str]], jaccard: float) -> Iterator[tuple[int, int]]:
    """Index pairs (smaller first) that the rule reports, each once, streamed: none is held here."""
    yield from _exact_pairs(words)
    yield from _partial_pairs(words, jaccard)

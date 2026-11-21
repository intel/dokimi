# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Candidate pairs for the repo budget's near-duplicate pass (``budget._near_duplicates``).

A pair is reported when the two word sets are equal and non-empty, or when their Jaccard overlap is at
least ``DUPLICATE_JACCARD`` and each has 3+ words. ``candidate_pairs`` returns a superset of those
pairs without looking at every pair: equal sets are grouped, and the rest are found by a prefix filter
(rarest words first) and a size bound. https://github.com/intel/dokimi/issues/995
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

MIN_PARTIAL_WORDS = 3
# Only lowers a bound, so a pair at exactly the threshold is never dropped to rounding.
_EPS = 1e-9


def _prefix_length(size: int, jaccard: float) -> int:
    """Words of a ``size``-word set to index: two sets with Jaccard >= J share >= ceil(J * size) words."""
    return size - math.ceil(jaccard * size - _EPS) + 1


def _exact_pairs(words: list[set[str]]) -> set[tuple[int, int]]:
    groups: dict[frozenset[str], list[int]] = {}
    for k, w in enumerate(words):
        if w:
            groups.setdefault(frozenset(w), []).append(k)
    return {pair for group in groups.values() for pair in combinations(group, 2)}


def _partial_pairs(words: list[set[str]], jaccard: float) -> set[tuple[int, int]]:
    freq = Counter(w for ws in words if len(ws) >= MIN_PARTIAL_WORDS for w in ws)
    order = sorted((k for k, ws in enumerate(words) if len(ws) >= MIN_PARTIAL_WORDS), key=lambda k: (len(words[k]), k))
    index: dict[str, list[int]] = {}
    found: set[tuple[int, int]] = set()
    for k in order:
        size = len(words[k])
        prefix = sorted(words[k], key=lambda w: (freq[w], w))[: _prefix_length(size, jaccard)]
        # Earlier ids are no larger than k; a pair more than 1/J apart in size cannot reach J.
        near = {o for w in prefix for o in index.get(w, ()) if size * jaccard - _EPS <= len(words[o]) <= size}
        found.update((min(k, o), max(k, o)) for o in near)
        for w in prefix:
            index.setdefault(w, []).append(k)
    return found


def candidate_pairs(words: list[set[str]], jaccard: float) -> set[tuple[int, int]]:
    """Index pairs (smaller first) that may be reported; every reported pair is among them."""
    return _exact_pairs(words) | _partial_pairs(words, jaccard)

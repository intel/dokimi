# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Word overlap between skill descriptions: the tokens, the Jaccard similarity and the candidate pairs.

``catalog check`` compares descriptions for ``description-near-duplicate`` (a warning) and ``similar-skills-exist``
(a note). The pairs are found by a prefix filter, so only the candidate pairs are compared, not every pair.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterator

NEAR_DUPLICATE_JACCARD = 0.5
NEAR_DUPLICATE_MIN_SHARED = 3  # shared words; a one-word overlap in two short descriptions is not a near-duplicate
# Under a different name, a description overlap from this similarity up to NEAR_DUPLICATE_JACCARD is a note
# (``similar-skills-exist``): the overlap is below the warning, but the model may still see one skill as the other.
SIMILAR_JACCARD = 0.4


# Words that say nothing about what a skill is for; left out of the description overlap.
STOP_WORDS = frozenset(
    """a an the and or of to in on for with by from as at is are be been being this that
    these those it its it's you your we our use used using when with without into than then
    also any all each skill skills claude user users make makes do does not no yes can will
    should must may might if else only other such via per about over under more most very
    just""".split()
)


def _tokens(description: str) -> frozenset[str]:
    """The lowercase words of three or more letters, in any script, less the stop words."""
    words = re.findall(r"[^\W\d_]{3,}", description.lower())
    return frozenset(w for w in words if w not in STOP_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _min_overlap(size: int, jaccard: float = NEAR_DUPLICATE_JACCARD) -> int:
    """The fewest words a skill of ``size`` words shares with any pair that passes the ``jaccard`` test.

    A passing pair shares at least NEAR_DUPLICATE_MIN_SHARED words and a Jaccard similarity of at least
    ``jaccard``; its shared words are then that fraction of the union, which is at least
    the skill's own size. The epsilon only ever lowers the bound, so it stays a safe under-estimate.
    """
    return max(NEAR_DUPLICATE_MIN_SHARED, math.ceil(jaccard * size - 1e-9))


def _candidate_pairs(tokens: list[frozenset[str]], jaccard: float) -> Iterator[tuple[int, int]]:
    """The index pairs ``(i, j)``, ``i < j``, that can pass a Jaccard similarity of ``jaccard``, in index order.

    Found by a prefix filter: each skill's words are ordered rarest first across all the skills, and only its
    first ``size - _min_overlap(size, jaccard) + 1`` words are indexed. A passing pair with ``s`` shared words
    has at least ``_min_overlap`` of them, so its rarest shared word sits inside both skills' prefixes and the
    pair is a candidate. Non-candidates could not pass, so comparing only these pairs finds what comparing
    every pair finds, in the same order.
    """
    frequency = Counter(word for words in tokens for word in words)
    prefixes: list[list[str]] = []
    by_prefix_word: dict[str, list[int]] = {}
    for index, words in enumerate(tokens):
        ordered = sorted(words, key=lambda word: (frequency[word], word))
        prefix = ordered[: max(0, len(ordered) - _min_overlap(len(ordered), jaccard) + 1)]
        prefixes.append(prefix)
        for word in prefix:
            by_prefix_word.setdefault(word, []).append(index)
    for i in range(len(tokens)):
        for j in sorted({k for word in prefixes[i] for k in by_prefix_word[word] if k > i}):
            yield i, j

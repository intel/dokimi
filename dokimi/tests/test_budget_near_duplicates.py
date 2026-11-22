# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The repo budget's near-duplicate pass compares only candidate pairs and reports what the all-pairs loop did.

``_all_pairs`` is the loop ``_near_duplicates`` ran before it had a candidate filter; the new pass must
return the same list in the same order (https://github.com/intel/dokimi/issues/995).
"""

from __future__ import annotations

import random
import tracemalloc
from itertools import combinations

import pytest

from dokimi.repos import budget, skill_overlap
from dokimi.repos.budget import DUPLICATE_JACCARD, _near_duplicates


def _all_pairs(skills):
    taken = [s["name"] for s in skills]
    label = [s["path"] if taken.count(s["name"]) > 1 else s["name"] for s in skills]
    words = [set(budget._WORD.findall(s["description"].lower())) - budget._STOP for s in skills]
    out = []
    for i, j in combinations(sorted(range(len(skills)), key=lambda k: (label[k], k)), 2):
        union = words[i] | words[j]
        if not union:
            continue
        overlap = len(words[i] & words[j]) / len(union)
        is_exact = words[i] == words[j] and words[i]
        is_partial = overlap >= DUPLICATE_JACCARD and len(words[i]) >= 3 and len(words[j]) >= 3
        if is_exact or is_partial:
            out.append({"a": label[i], "b": label[j], "overlap": round(overlap, 2)})
    return out


def _skill(n, name, description):
    return {"name": name, "path": f".claude/skills/d{n}/SKILL.md", "description": description}


def _random_corpus(seed, count, vocab=12):
    rng = random.Random(seed)
    pool = [f"w{n}" for n in range(vocab)]
    skills = []
    for n in range(count):
        size = rng.choice([0, 1, 2, 3, 3, 4, 5, 6, 6, 8])
        description = " ".join(rng.sample(pool, min(size, vocab)))
        name = f"n{rng.randrange(count // 2)}"  # about half the names are shared
        skills.append(_skill(n, name, description))
    return skills


@pytest.mark.parametrize("seed", range(20))
def test_the_candidate_pass_reports_what_the_all_pairs_loop_did(seed):
    skills = _random_corpus(seed, 90)
    want = _all_pairs(skills)
    assert want  # the corpus has duplicates, so the comparison is not vacuous
    assert _near_duplicates(skills) == want


def test_tiny_exact_duplicates_are_reported_and_empty_sets_are_not():
    skills = [
        _skill(0, "one-a", "Lint"), _skill(1, "one-b", "lint"),
        _skill(2, "two-a", "run ruff"), _skill(3, "two-b", "ruff run"),
        _skill(4, "empty-a", "the of"), _skill(5, "empty-b", ""),
    ]
    assert _near_duplicates(skills) == [
        {"a": "one-a", "b": "one-b", "overlap": 1.0}, {"a": "two-a", "b": "two-b", "overlap": 1.0}]


def test_pairs_come_in_label_order_and_a_shared_name_labels_by_path():
    skills = [
        _skill(0, "zeta", "alpha beta gamma delta"),
        _skill(1, "dup", "alpha beta gamma delta"),
        _skill(2, "dup", "alpha beta gamma delta"),
        _skill(3, "beta", "alpha beta gamma delta"),
    ]
    got = _near_duplicates(skills)
    assert got == _all_pairs(skills)
    assert [(p["a"], p["b"]) for p in got][:2] == [
        (".claude/skills/d1/SKILL.md", ".claude/skills/d2/SKILL.md"), (".claude/skills/d1/SKILL.md", "beta")]


def _pair(size, other, shared):
    common = [f"c{n}" for n in range(shared)]
    return [
        _skill(0, "one", " ".join(common + [f"a{n}" for n in range(size - shared)])),
        _skill(1, "two", " ".join(common + [f"b{n}" for n in range(other - shared)])),
    ]


@pytest.mark.parametrize(
    ("size", "other", "shared", "reported"),
    [
        (3, 3, 2, True),  # 2 of 4 words: exactly 0.5
        (6, 6, 4, True),  # 4 of 8
        (3, 6, 3, True),  # sizes 2x apart, Jaccard exactly 0.5
        (4, 8, 4, True),
        (3, 7, 3, False),  # sizes more than 2x apart
        (6, 6, 3, False),  # 1/3
        (2, 4, 2, False),  # a partial overlap needs 3+ words per side
    ],
)
def test_pairs_at_the_edge_of_the_jaccard_and_size_bounds(size, other, shared, reported):
    skills = _pair(size, other, shared)
    assert _near_duplicates(skills) == _all_pairs(skills)
    assert bool(_near_duplicates(skills)) is reported


def test_shared_boilerplate_with_unique_tails_keeps_no_candidate_pair():
    from dokimi.repos.skill_overlap import candidate_pairs

    # Every prefix holds "common" and the two unique words, so the prefix filter alone pairs all 60
    # skills; each overlap is 2 of 6 words, below 0.5, and none may be kept.
    words = [{"common", "shared", f"a{n}", f"b{n}"} for n in range(60)]
    assert list(candidate_pairs(words, DUPLICATE_JACCARD)) == []


def test_dense_exact_duplicates_are_streamed_not_stored():
    from dokimi.repos.skill_overlap import candidate_pairs

    count = 1000  # 499,500 qualifying pairs; one tuple each would take about 40 MB
    words = [{"alpha", "beta", "gamma"} for _ in range(count)]
    tracemalloc.start()
    try:
        seen = sum(1 for _ in candidate_pairs(words, DUPLICATE_JACCARD))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert seen == count * (count - 1) // 2
    assert peak < 1_000_000


def test_a_pair_is_yielded_once_when_it_is_both_exact_and_partial():
    from dokimi.repos.skill_overlap import candidate_pairs

    words = [{"a", "b", "c"}, {"a", "b", "c"}, {"a", "b", "c", "d"}]
    assert sorted(candidate_pairs(words, DUPLICATE_JACCARD)) == [(0, 1), (0, 2), (1, 2)]


def test_the_pass_compares_few_of_the_pairs_of_a_catalog_like_corpus(monkeypatch):
    rng = random.Random(5)
    tail = [f"t{n}" for n in range(4000)]
    weights = [1.0 / (n + 1) for n in range(len(tail))]
    skills: list[dict[str, str]] = []
    for n in range(3000):
        if skills and rng.random() < 0.02:  # a near-copy: one word of an earlier description dropped
            words = rng.choice(skills)["description"].split()
            del words[rng.randrange(len(words))]
            description = " ".join(words)
        else:
            description = " ".join(rng.choices(tail, weights=weights, k=rng.randint(3, 22)))
        skills.append(_skill(n, f"s{n}", description))
    checks = []
    real = skill_overlap._reaches

    def counting(a, b, jaccard):
        checks.append(1)
        return real(a, b, jaccard)

    monkeypatch.setattr(skill_overlap, "_reaches", counting)
    reported = _near_duplicates(skills)
    assert reported  # the corpus holds near-copies, so the pass found something
    all_pairs = len(skills) * (len(skills) - 1) // 2
    assert len(checks) < all_pairs // 20

"""The near-duplicate pass compares only the candidate pairs its prefix filter finds (``_near_duplicates``).

The filter must never skip a pair that the all-pairs check would flag, so the findings are compared with a
brute-force pass over every pair (``_brute_force``), on seeded random descriptions, and the scale of a
5000-skill corpus is timed.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from dokimi.external import validate
from dokimi.external.catalog import SkillCatalogEntry
from dokimi.external.validate import (
    NEAR_DUPLICATE_JACCARD,
    NEAR_DUPLICATE_MIN_SHARED,
    SkillReview,
    _counterpart,
    _dir_name,
    _is_drift,
    _jaccard,
    _near_duplicates,
    _tokens,
)
from dokimi.skills import Skill

# Common and rare words, stop words, short words, and non-ASCII words, so the overlaps vary.
VOCABULARY = (
    """review code python pull request style bugs typing tests test lint format docs deploy
    migrations database query api rest client server cache logs metrics dashboard report
    security audit secrets token auth login session release changelog version tag commit branch
    merge rebase diff patch refactor cleanup dead imports types mypy ruff pytest coverage
    benchmark perf latency throughput memory leak profile trace debug fix hotfix go js ts
    проверка данные données daten schema sql notebook chart plot image render pdf slide deck
    the a an of and for with use when skill""".split()
)
DIR_NAMES = [f"skill-{n}" for n in range(40)]


def wide_vocabulary(seed: int, extra: int = 4000) -> tuple[str, ...]:
    """The topical words plus ``extra`` made-up ones: a long tail, as in a real catalog of descriptions."""
    rng = random.Random(seed)
    letters = "abcdefghijklmnoprstuvwxyz"
    made = {"".join(rng.choice(letters) for _ in range(rng.randint(4, 10))) for _ in range(extra)}
    return tuple(VOCABULARY) + tuple(sorted(made))


def _words(rng: random.Random, pool: tuple[str, ...]) -> list[str]:
    # Zipf-like weights: the first words are common, the tail is rare.
    weights = [1.0 / (rank + 1) for rank in range(len(pool))]
    return rng.choices(pool, weights=weights, k=rng.randint(1, 22))


def _corpus(
    seed: int, count: int, pool: tuple[str, ...] = VOCABULARY, near_copy: float = 0.3
) -> list[tuple[str, str, str, str, bool]]:
    """``(root, directory name, description, content digest, disable_model_invocation)`` per skill.

    ``near_copy`` is the share of descriptions that are a one-word edit of an earlier one.
    """
    rng = random.Random(seed)
    rows: list[tuple[str, str, str, str, bool]] = []
    descriptions: list[str] = []
    for _ in range(count):
        if descriptions and rng.random() < near_copy:
            # a near-copy of an earlier description: one word changed, dropped or added
            words = rng.choice(descriptions).split()
            edit = rng.random()
            if edit < 0.4 and len(words) > 1:
                words[rng.randrange(len(words))] = rng.choice(pool)
            elif edit < 0.7 and len(words) > 1:
                del words[rng.randrange(len(words))]
            else:
                words.insert(rng.randrange(len(words) + 1), rng.choice(pool))
            description = " ".join(words)
        else:
            description = " ".join(_words(rng, pool))
        descriptions.append(description)
        root = rng.choice(["checkout-a", "checkout-b"])
        dir_name = rng.choice(DIR_NAMES)
        digest = f"digest-{rng.randrange(max(1, count // 2))}"
        dmi = rng.random() < 0.05
        rows.append((root, dir_name, description, digest, dmi))
    return rows


def _loaded(rows: list[tuple[str, str, str, str, bool]]):
    """Loaded items as ``review_skills`` builds them, with virtual paths (nothing is read from disk)."""
    loaded = []
    for index, (root, dir_name, description, digest, dmi) in enumerate(rows):
        source_root = Path("/corpus") / root
        skill = Skill(
            name=f"n{index}", description=description, body="",
            path=source_root / "skills" / dir_name / "SKILL.md",
        )
        entry = SkillCatalogEntry(
            name=skill.name, status=None, path=skill.path, source_root=source_root, skill=skill
        )
        loaded.append((entry, skill, SkillReview(disable_model_invocation=dmi, content_sha256=digest)))
    return loaded


def _brute_force(loaded) -> None:
    """The all-pairs pass: the same per-pair checks as ``_near_duplicates``, with no candidate filter."""
    shown = [item for item in loaded if not item[2].disable_model_invocation]
    tokens = [_tokens(" ".join(item[1].description.split())) for item in shown]
    names = [_dir_name(item) for item in shown]
    for i, (entry, _, review) in enumerate(shown):
        for j in range(i + 1, len(shown)):
            o_entry, _, o_review = shown[j]
            if o_review.content_sha256 == review.content_sha256:
                continue
            if names[i] is not None and names[i] == names[j] and _is_drift(shown[i], shown[j]):
                continue
            shared = len(tokens[i] & tokens[j])
            similarity = _jaccard(tokens[i], tokens[j])
            if shared >= NEAR_DUPLICATE_MIN_SHARED and similarity >= NEAR_DUPLICATE_JACCARD:
                pairs = (
                    (review, _counterpart(shown[j], entry)),
                    (o_review, _counterpart(shown[i], o_entry)),
                )
                for side, other in pairs:
                    side.findings.append(
                        validate.Finding(
                            "warn",
                            "description-near-duplicate",
                            f"description overlaps {other} (word overlap {similarity:.0%}); "
                            "the model may pick either",
                        )
                    )


def _findings(loaded) -> list[list[tuple[str, str]]]:
    return [[(f.code, f.message) for f in review.findings] for _, _, review in loaded]


@pytest.mark.parametrize("seed", range(12))
def test_blocking_finds_the_same_near_duplicates_as_comparing_every_pair(seed):
    rows = _corpus(seed, count=160)
    fast, slow = _loaded(rows), _loaded(rows)
    _near_duplicates(fast)
    _brute_force(slow)
    assert _findings(fast) == _findings(slow)


def test_blocking_matches_brute_force_on_a_larger_corpus():
    rows = _corpus(1234, count=700)
    fast, slow = _loaded(rows), _loaded(rows)
    _near_duplicates(fast)
    _brute_force(slow)
    flagged = sum(1 for _, _, review in fast if review.findings)
    assert flagged > 50  # the corpus has real near-duplicates, so the comparison is not vacuous
    assert _findings(fast) == _findings(slow)


def test_pairs_at_the_thresholds_are_kept():
    # 3 shared words in a union of 6: exactly NEAR_DUPLICATE_MIN_SHARED and NEAR_DUPLICATE_JACCARD (0.5).
    shared = ["alpha", "beta", "gamma"]
    rows = [
        ("r", "one", " ".join(shared + ["delta"]), "d1", False),
        ("r", "two", " ".join(shared + ["eta", "theta"]), "d2", False),
        ("r", "three", "alpha beta", "d3", False),  # two shared words: never a near-duplicate
    ]
    fast, slow = _loaded(rows), _loaded(rows)
    _near_duplicates(fast)
    _brute_force(slow)
    assert _findings(fast) == _findings(slow)
    assert [code for code, _ in _findings(fast)[0]] == ["description-near-duplicate"]


def test_scale_of_5000_skills_is_a_few_seconds():
    # A catalog-like corpus: a long word tail and few near-copies. The dense corpus above is the
    # worst case for the prefix filter, and is kept for the equivalence checks only.
    rows = _corpus(7, count=5000, pool=wide_vocabulary(7), near_copy=0.02)
    loaded = _loaded(rows)
    started = time.perf_counter()
    _near_duplicates(loaded)
    elapsed = time.perf_counter() - started
    print(f"\n_near_duplicates on 5000 skills: {elapsed:.2f}s")
    assert elapsed < 10.0

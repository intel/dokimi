# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``similar-skills-exist``: a note on skills under different names whose descriptions overlap in the band
below ``description-near-duplicate``. Token-free: the same word-overlap check as the near-duplicate warning.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from dokimi.external.catalog import SkillCatalogEntry
from dokimi.external.validate import (
    NEAR_DUPLICATE_JACCARD,
    NEAR_DUPLICATE_MIN_SHARED,
    SIMILAR_JACCARD,
    Finding,
    SkillReview,
    _counterpart,
    _jaccard,
    _name_key,
    _similar_skills,
    _tokens,
)
from dokimi.skills import Skill
from tests.test_catalog_ignore import _run, _skill

# Description pairs with their word overlap (Jaccard similarity over the tokens), checked with _tokens.
BAND_A = "Reconcile spreadsheet invoices against bank statements for the accounts team"  # 0.333 with BAND_B
BAND_B = "Review spreadsheet invoices against purchase orders for the finance team"
EDGE_A = "Convert legacy Makefile targets into reproducible container builds"  # 0.400 with EDGE_B: the bound
EDGE_B = "Convert legacy Makefile targets into Bazel build rules for CI"
NOTE_A = "Summarise incident timelines from pager alerts and chat logs"  # 0.444: in the band
NOTE_B = "Summarise incident timelines from alerts and postmortem notes"
WARN_A = "Plan database migrations with rollback steps for PostgreSQL schemas"  # 0.556: a near-duplicate
WARN_B = "Plan database migrations with rollback steps and data backfills"


def _items(rows: list[tuple[str, str, bool]]):
    """Loaded items as ``review_skills`` builds them, with virtual paths: rows of (name, description, dmi)."""
    loaded = []
    for index, (name, description, dmi) in enumerate(rows):
        source_root = Path("/corpus") / "root"
        skill = Skill(
            name=name, description=description, body="",
            path=source_root / name / "SKILL.md",
        )
        entry = SkillCatalogEntry(name=name, status=None, path=skill.path, source_root=source_root, skill=skill)
        loaded.append((entry, skill, SkillReview(disable_model_invocation=dmi, content_sha256=f"d{index}")))
    return loaded


def _notes(loaded) -> list[list[tuple[str, str, str]]]:
    return [[(f.code, f.level, f.message) for f in review.findings] for _, _, review in loaded]


def _codes(loaded) -> list[list[str]]:
    return [[code for code, _, _ in row] for row in _notes(loaded)]


def test_the_thresholds_put_the_fixture_pairs_where_the_docs_say():
    assert SIMILAR_JACCARD == 0.4 and NEAR_DUPLICATE_JACCARD == 0.5
    assert abs(_jaccard(_tokens(BAND_A), _tokens(BAND_B)) - 1 / 3) < 1e-9
    assert _jaccard(_tokens(EDGE_A), _tokens(EDGE_B)) == 0.4
    assert SIMILAR_JACCARD <= _jaccard(_tokens(NOTE_A), _tokens(NOTE_B)) < NEAR_DUPLICATE_JACCARD
    assert _jaccard(_tokens(WARN_A), _tokens(WARN_B)) >= NEAR_DUPLICATE_JACCARD


def test_a_pair_in_the_band_gets_a_note_on_both_skills_and_no_warning():
    loaded = _items([("incident-timelines", NOTE_A, False), ("postmortem-notes", NOTE_B, False)])
    _similar_skills(loaded)

    first, second = _notes(loaded)
    assert [code for code, _, _ in first] == ["similar-skills-exist"]
    assert [code for code, _, _ in second] == ["similar-skills-exist"]
    assert first[0][1] == "note" and second[0][1] == "note"
    assert "postmortem-notes" in first[0][2] and "incident-timelines" in second[0][2]
    assert "44%" in first[0][2]


def test_a_pair_at_the_lower_bound_is_kept():
    loaded = _items([("convert-makefile", EDGE_A, False), ("bazel-rules", EDGE_B, False)])
    _similar_skills(loaded)
    assert _codes(loaded) == [["similar-skills-exist"], ["similar-skills-exist"]]


def test_a_pair_below_the_band_gets_nothing():
    loaded = _items([("invoice-review", BAND_A, False), ("invoice-check", BAND_B, False)])
    _similar_skills(loaded)
    assert _codes(loaded) == [[], []]


def test_a_pair_in_the_band_with_fewer_than_three_shared_words_gets_nothing():
    first, second = _tokens("kestrel falcon osprey"), _tokens("kestrel falcon heron egret")
    assert len(first & second) == 2 < NEAR_DUPLICATE_MIN_SHARED and _jaccard(first, second) == SIMILAR_JACCARD
    loaded = _items([
        ("bird-a", "kestrel falcon osprey", False),
        ("bird-b", "kestrel falcon heron egret", False),
        ("bird-c", "osprey heron egret lantern quartz meadow willow", False),
    ])
    _similar_skills(loaded)
    assert _codes(loaded) == [[], [], []]


def test_a_near_duplicate_is_only_the_warning_not_also_a_note():
    loaded = _items([("migration-plan", WARN_A, False), ("migration-check", WARN_B, False)])
    _similar_skills(loaded)
    assert _codes(loaded) == [[], []]


def test_the_same_name_in_another_directory_is_not_a_similar_skill():
    # The name and copy checks own same-named skills; the key compares names the way the corpus analyses do.
    assert _name_key("Incident Timelines") == _name_key("incident-timelines") == "incident-timelines"
    loaded = _items([("incident-timelines", NOTE_A, False), ("Incident Timelines", NOTE_B, False)])
    _similar_skills(loaded)
    assert _codes(loaded) == [[], []]


def test_a_skill_that_disables_model_invocation_is_not_compared():
    loaded = _items([("incident-timelines", NOTE_A, False), ("postmortem-notes", NOTE_B, True)])
    _similar_skills(loaded)
    assert _codes(loaded) == [[], []]


def _brute_similar(loaded) -> None:
    """The all-pairs pass with the same per-pair tests and no candidate filter."""
    shown = [item for item in loaded if not item[2].disable_model_invocation]
    tokens = [_tokens(" ".join(item[1].description.split())) for item in shown]
    keys = [_name_key(item[1].name) for item in shown]
    for i, (entry, _, review) in enumerate(shown):
        for j in range(i + 1, len(shown)):
            if keys[i] == keys[j]:
                continue
            shared = len(tokens[i] & tokens[j])
            similarity = _jaccard(tokens[i], tokens[j])
            if shared < NEAR_DUPLICATE_MIN_SHARED or not SIMILAR_JACCARD <= similarity < NEAR_DUPLICATE_JACCARD:
                continue
            o_entry, _, o_review = shown[j]
            review.findings.append(_finding(_counterpart(shown[j], entry), similarity))
            o_review.findings.append(_finding(_counterpart(shown[i], o_entry), similarity))


def _finding(other: str, similarity: float) -> Finding:
    return Finding(
        "note", "similar-skills-exist",
        f"description overlaps {other} (word overlap {similarity:.0%}) under another name; "
        "the two may be one skill under two names",
    )


VOCABULARY = (
    "alpha beta gamma delta epsilon zeta theta kappa lambda sigma tokens review write deploy "
    "format lint test report invoice pipeline schema queue cache image branch commit docs "
    "release notes search index"
).split()


@pytest.mark.parametrize("seed", range(8))
def test_the_candidate_search_finds_what_comparing_every_pair_finds(seed):
    rng = random.Random(seed)
    rows = []
    for index in range(120):
        words = rng.sample(VOCABULARY, rng.randint(4, 9))
        rows.append((f"skill-{index}", " ".join(words).capitalize() + ".", rng.random() < 0.05))
    fast, slow = _items(rows), _items(rows)
    _similar_skills(fast)
    _brute_similar(slow)
    assert _notes(fast) == _notes(slow)
    assert any(_codes(fast)), "the corpus should hold some pairs in the band"


def test_end_to_end_catalog_check_reports_the_note_and_keeps_the_state_of_the_skill(tmp_path, capsys):
    # Cross-skill checks run on the whole catalog; a --per-skill row reviews its skill alone.
    _skill(tmp_path / "skills", "incident-timelines", name="incident-timelines", description=NOTE_A)
    _skill(tmp_path / "skills", "postmortem-notes", name="postmortem-notes", description=NOTE_B)

    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", "--format", "json"], capsys)
    rows = {row["name"]: row for row in json.loads(out)["skills"]}

    for name in ("incident-timelines", "postmortem-notes"):
        # The fixture descriptions have no "when" clause, a warning of its own: the note does not add to the state.
        assert rows[name]["state"] == "warn"
        findings = [(f["code"], f["level"]) for f in rows[name]["findings"]]
        assert ("similar-skills-exist", "note") in findings
        assert ("description-near-duplicate", "warn") not in findings

    out, code = _run(["--root", str(tmp_path), "--kind", "skills", "--format", "json",
                      "--ignore", "similar-skills-exist"], capsys)
    rows = {row["name"]: row for row in json.loads(out)["skills"]}
    assert all("similar-skills-exist" not in [f["code"] for f in row["findings"]] for row in rows.values())
    assert code == 0

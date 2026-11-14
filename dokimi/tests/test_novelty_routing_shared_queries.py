# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Heading queries shared by sibling skills: the warning, and a lexical note that claims no finding."""

from __future__ import annotations

import json

from dokimi.cli import build_parser
from dokimi.commands.novelty_routing_report import warn_shared_queries
from dokimi.novelty.all_vs_all import all_vs_all
from dokimi.novelty.query_overlap import (
    SHARED_QUERY_WARN_SHARE,
    shared_queries_warning,
    shared_query_counts,
)
from dokimi.novelty.routing import Candidate, headings_as_queries, lexical_routing

TEMPLATE = "## Quick Start\n\n## Implementation Guide\n\n## Gotchas & Limitations\n"


def test_sibling_skills_on_one_template_give_identical_heading_queries():
    queries = headings_as_queries(TEMPLATE)
    warning = shared_queries_warning({"dpnp-a": queries, "dpnp-b": queries, "dpnp-c": queries})
    assert warning is not None
    assert "9 of 9 queries (100%)" in warning
    assert "same rank" not in warning
    assert "independent" in warning


def test_case_and_spacing_do_not_hide_a_shared_query():
    warning = shared_queries_warning({"a": ["Quick  Start"], "b": ["quick start"]})
    assert warning is not None
    assert "2 of 2" in warning


def test_trailing_punctuation_does_not_hide_a_shared_query():
    assert shared_query_counts({"a": ["Quick Start"], "b": ["Quick Start."]}) == (2, 2)


def test_word_order_does_not_hide_a_shared_query():
    assert shared_query_counts({"a": ["quick start"], "b": ["start quick"]}) == (2, 2)


def test_a_repeated_word_keeps_a_query_apart_from_the_single_word():
    assert shared_query_counts({"a": ["start start"], "b": ["start"]}) == (0, 2)


def test_queries_the_scorer_tokenizes_differently_are_not_shared():
    assert shared_query_counts({"a": ["Straße"], "b": ["STRASSE"]}) == (0, 2)


def test_the_threshold_is_half_of_the_queries_listed_for_several_skills():
    assert SHARED_QUERY_WARN_SHARE == 0.5
    at = {"a": ["shared one", "own a1"], "b": ["shared one", "own b1"]}
    below = {"a": ["shared one", "own a1", "own a2"], "b": ["shared one", "own b1", "own b2"]}
    assert shared_queries_warning(at) is not None  # 2 of 4
    assert shared_queries_warning(below) is None  # 2 of 6


def test_queries_written_per_skill_draw_no_warning():
    assert shared_queries_warning({"a": ["profile a binary"], "b": ["slice an array"]}) is None


def test_one_skill_or_no_queries_draw_no_warning():
    assert shared_queries_warning({"a": ["x y", "x y"]}) is None
    assert shared_queries_warning({"a": [], "b": []}) is None


def test_the_warning_does_not_claim_equal_ranks_the_run_does_not_give():
    cands = [Candidate("a", "alpha"), Candidate("b", "gamma")]
    queries = {"a": ["alpha"], "b": ["alpha"]}
    rates = all_vs_all(cands, queries)["lexical"]["own_rate"]
    assert rates["a"] != rates["b"]
    assert "same rank" not in shared_queries_warning(queries)


def test_queries_with_no_content_word_are_not_counted():
    queries = {"a": ["the", "and", "profile a binary"], "b": ["the", "and", "slice an array"]}
    assert shared_queries_warning(queries) is None


def test_the_warning_goes_to_stderr(capsys):
    shared = headings_as_queries(TEMPLATE)
    warn_shared_queries({"dpnp-a": shared, "dpnp-b": shared})
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("WARNING: 6 of 6 queries (100%)")


def test_the_lexical_note_does_not_present_the_family_zero_as_a_finding():
    result = lexical_routing([Candidate("a", "alpha beta"), Candidate("b", "gamma")],
                             ["alpha beta"])
    assert "0.0 for every" not in result["note"]
    assert "independently" in result["note"]


def _run_all_vs_all(tmp_path, *extra):
    root = tmp_path / "catalog"
    for name in ("a", "b"):
        skill_dir = root / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: tune {name} things\n---\n\n# B\n", encoding="utf-8")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps({"a": ["quick start"], "b": ["quick start"]}), encoding="utf-8")
    args = build_parser().parse_args(
        ["catalog", "routing", str(root),
         "--queries-by-skill", str(queries), "--out", str(tmp_path / "out.json"), *extra])
    args.func(args)


def test_all_vs_all_result_records_the_shared_query_counts():
    cands = [Candidate("a", "alpha"), Candidate("b", "beta")]
    queries = {"a": ["alpha one", "the", "own first"], "b": ["Alpha  one", "own second"]}
    assert all_vs_all(cands, queries)["shared_queries"] == {"shared": 2, "total": 4}


def test_a_saved_all_vs_all_file_carries_the_shared_query_counts(tmp_path):
    _run_all_vs_all(tmp_path)
    saved = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert saved["result"]["shared_queries"] == {"shared": 2, "total": 2}


def test_all_vs_all_warns_of_shared_queries_on_stderr(tmp_path, capsys):
    _run_all_vs_all(tmp_path)
    assert "WARNING: 2 of 2 queries (100%)" in capsys.readouterr().err


def test_estimate_only_warns_of_shared_queries_before_returning(tmp_path, capsys):
    _run_all_vs_all(tmp_path, "--estimate-only")
    assert "WARNING: 2 of 2 queries (100%)" in capsys.readouterr().err

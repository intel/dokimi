# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for dashboard aggregator and markdown renderer."""
import json
from pathlib import Path

import pytest

from dokimi.dashboard.aggregator import (
    DashboardData,
    ProductSnapshot,
    QuestionResult,
    ResultsAggregator,
)
from dokimi.dashboard.markdown_renderer import render_dashboard, save_dashboard_markdown, save_dashboard_json
from dokimi.eval.stats import paired_summary
from dokimi.records import records


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_eval_file(tmp_path: Path, name: str, evaluations: list) -> Path:
    p = tmp_path / name / "eval_results.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "library_name": name,
        "evaluated_at": "2026-02-27T00:00:00Z",
        "judge_model": "gpt-6.1-sol",
        "total_evaluations": len(evaluations),
        "evaluations": evaluations,
    }))
    return p


def _ev(q_id, question, with_score, without_score):
    delta = with_score - without_score if with_score is not None and without_score is not None else None
    return {
        "question_id": q_id,
        "question": question,
        "with_docs": {"aggregate": with_score, "correctness": with_score, "completeness": with_score} if with_score is not None else None,
        "without_docs": {"aggregate": without_score, "correctness": without_score} if without_score is not None else None,
        "delta": delta,
    }


# ── ResultsAggregator ─────────────────────────────────────────────────────────

def test_aggregator_empty_dir(tmp_path):
    agg = ResultsAggregator(tmp_path / "nonexistent")
    data = agg.aggregate()
    assert data.products == []


def test_aggregator_finds_eval_files(tmp_path):
    _make_eval_file(tmp_path, "oneTBB", [
        _ev("q1", "How to use parallel_for?", 80, 60),
        _ev("q2", "What is task_arena?", 70, 50),
    ])
    agg = ResultsAggregator(tmp_path)
    data = agg.aggregate()
    assert len(data.products) == 1
    p = data.products[0]
    assert p.product == "oneTBB"
    assert p.total_questions == 2
    assert p.avg_with_docs == 75.0
    assert p.avg_without_docs == 55.0
    assert p.avg_delta == 20.0


# Two pairs, a question scored in the context arm only, one in the baseline arm only.
_ONE_ARM_EVALS = [
    _ev("q1", "Q1?", 80, 60), _ev("q2", "Q2?", 40, 40),
    _ev("q3", "Q3?", 100, None), _ev("q4", "Q4?", None, 30),
]


def test_aggregator_averages_both_arms_over_the_complete_pairs(tmp_path):
    """The snapshot's means are `report generate`'s, which leaves one-arm questions out."""
    _make_eval_file(tmp_path, "oneTBB", _ONE_ARM_EVALS)
    p = ResultsAggregator(tmp_path).aggregate().products[0]
    s = paired_summary(records(_ONE_ARM_EVALS, kind="evaluations"))
    assert (p.avg_with_docs, p.avg_without_docs, p.avg_delta) == (60.0, 50.0, 10.0)
    assert (p.avg_with_docs, p.avg_without_docs, p.avg_delta) == (
        round(s.with_mean, 1), round(s.without_mean, 1), round(s.delta_mean, 1))
    assert p.total_questions == 4


def test_by_source_averages_over_the_pairs_and_a_group_without_one_has_no_means(tmp_path):
    """Each source row is over its own pairs; a group scored in one arm per question has none."""
    chunk = [{**_ev("c1", "C1?", 90, None), "question_source": "chunk"},
             {**_ev("c2", "C2?", None, 20), "question_source": "chunk"}]
    _make_eval_file(tmp_path, "oneTBB", _ONE_ARM_EVALS + chunk)
    p = ResultsAggregator(tmp_path).aggregate().products[0]
    assert p.by_source["persona"] == {"count": 4, "grounded": 0, "avg_with_docs": 60.0,
                                      "avg_without_docs": 50.0, "avg_delta": 10.0}
    assert p.by_source["chunk"] == {"count": 2, "grounded": 0, "avg_with_docs": None,
                                    "avg_without_docs": None, "avg_delta": None}
    assert (p.avg_with_docs, p.avg_without_docs, p.avg_delta) == (60.0, 50.0, 10.0)


def test_baseline_only_run_keeps_its_baseline_mean(tmp_path):
    """No pair because no context arm ran: the baseline mean is still the score shown."""
    _make_eval_file(tmp_path, "oneTBB", [_ev("q1", "Q1?", None, 60), _ev("q2", "Q2?", None, 40)])
    p = ResultsAggregator(tmp_path).aggregate().products[0]
    assert (p.avg_with_docs, p.avg_without_docs, p.avg_delta) == (None, 50.0, None)
    assert p.doc_score == 50.0
    assert p.status == "fair"


def test_aggregator_multiple_products(tmp_path):
    _make_eval_file(tmp_path, "oneTBB", [_ev("q1", "Q1?", 80, 60)])
    _make_eval_file(tmp_path, "oneMKL", [_ev("q1", "Q1?", 65, 45)])
    agg = ResultsAggregator(tmp_path)
    data = agg.aggregate()
    assert len(data.products) == 2
    names = {p.product for p in data.products}
    assert "oneTBB" in names
    assert "oneMKL" in names


def test_aggregator_skips_empty_eval_file(tmp_path):
    p = tmp_path / "bad" / "eval_empty.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"evaluations": []}))
    agg = ResultsAggregator(tmp_path)
    data = agg.aggregate()
    assert data.products == []


def test_aggregator_skips_unreadable_file(tmp_path):
    p = tmp_path / "x" / "eval_x.json"
    p.parent.mkdir()
    p.write_text("not json {{{")
    agg = ResultsAggregator(tmp_path)
    data = agg.aggregate()
    assert data.products == []


# ── ProductSnapshot ───────────────────────────────────────────────────────────

def test_product_status_good():
    s = ProductSnapshot("oneTBB", "onetbb", "", "m", 10, 80.0, 60.0, 20.0)
    assert s.status == "good"
    assert s.doc_score == 80.0


def test_product_status_fair():
    s = ProductSnapshot("oneTBB", "onetbb", "", "m", 10, 60.0, 40.0, 20.0)
    assert s.status == "fair"


def test_product_status_poor():
    s = ProductSnapshot("oneTBB", "onetbb", "", "m", 10, 40.0, 30.0, 10.0)
    assert s.status == "poor"


def test_product_status_no_data():
    s = ProductSnapshot("oneTBB", "onetbb", "", "m", 0, None, None, None)
    assert s.status == "no-data"
    assert s.doc_score is None


def test_sorted_by_score(tmp_path):
    _make_eval_file(tmp_path, "oneTBB", [_ev("q1", "Q?", 80, 60)])
    _make_eval_file(tmp_path, "oneMKL", [_ev("q1", "Q?", 50, 30)])
    agg = ResultsAggregator(tmp_path)
    data = agg.aggregate()
    scores = [p.doc_score for p in data.sorted_by_score]
    assert scores == sorted(scores, reverse=True)


# ── Markdown renderer ─────────────────────────────────────────────────────────

def _make_dashboard(products=None) -> DashboardData:
    if products is None:
        products = [
            ProductSnapshot(
                product="oneTBB", library_key="onetbb",
                evaluated_at="2026-02-27T00:00:00Z", judge_model="gpt-6.1-sol",
                total_questions=5, avg_with_docs=78.0, avg_without_docs=58.0, avg_delta=20.0,
                questions=[
                    QuestionResult("q1", "How to use parallel_for?", 60, 40, 20),
                    QuestionResult("q2", "What is task_arena?", 80, 60, 20),
                ],
            )
        ]
    return DashboardData(generated_at="2026-02-27T00:00:00Z", products=products)


def test_render_dashboard_contains_product():
    md = render_dashboard(_make_dashboard())
    assert "oneTBB" in md
    assert "78.0" in md


def test_render_dashboard_contains_table_headers():
    md = render_dashboard(_make_dashboard())
    assert "Context Arm" in md
    assert "Baseline" in md
    assert "Delta" in md


def test_render_dashboard_empty():
    md = render_dashboard(DashboardData(generated_at="now", products=[]))
    assert "No evaluation results" in md


def test_render_dashboard_shows_bad_questions():
    md = render_dashboard(_make_dashboard(), top_n_bad_questions=2)
    assert "parallel_for" in md
    assert "task_arena" in md


def test_render_dashboard_status_emoji():
    md = render_dashboard(_make_dashboard())
    assert "🟢" in md or "🟡" in md or "🔴" in md


def test_save_dashboard_markdown(tmp_path):
    data = _make_dashboard()
    path = tmp_path / "DASHBOARD.md"
    save_dashboard_markdown(data, path)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "oneTBB" in content


def test_save_dashboard_json(tmp_path):
    data = _make_dashboard()
    path = tmp_path / "dashboard.json"
    save_dashboard_json(data, path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["products"][0]["product"] == "oneTBB"
    assert parsed["products"][0]["avg_with_docs"] == 78.0


def test_aggregator_ignores_the_judge_cap_marker(tmp_path):
    ev = _ev("q1", "Q?", 40, 60)
    ev["with_docs"]["caps"] = {"correctness": {"judged": 95.0, "rule": "wrong_figure"}}
    ev["with_docs"]["unread_claims"] = [0, 2]
    _make_eval_file(tmp_path, "oneTBB", [ev])
    q = ResultsAggregator(tmp_path).aggregate().products[0].questions[0]
    assert q.with_docs_dimensions == {"correctness": 40, "completeness": 40}


def _miss(q_id, without_score):
    """A question the context arm never answered from the docs: no retrieval hit, no context score."""
    return {"question_id": q_id, "question": "Q?", "retrieval_hit": False, "with_docs": None,
            "without_docs": {"aggregate": without_score, "correctness": without_score}, "delta": None}


def test_a_retrieval_miss_with_a_baseline_score_counts_as_context_zero_in_the_doc_score_only(tmp_path):
    _make_eval_file(tmp_path, "oneTBB", [_ev("q1", "Q?", 80, 60), _miss("q2", 60)])
    p = ResultsAggregator(tmp_path).aggregate().products[0]
    # The miss is no judged measurement: the arms and the delta are over the one complete pair.
    assert (p.avg_with_docs, p.avg_without_docs, p.avg_delta) == (80.0, 60.0, 20.0)
    assert p.by_source["persona"]["avg_delta"] == 20.0
    # The doc health number, which the dashboard shows, still counts the miss as 0.
    assert (p.doc_health, p.doc_score) == (40.0, 40.0)
    assert p.status == "poor"


def test_an_all_miss_run_scores_its_misses_as_zero_with_no_delta(tmp_path):
    _make_eval_file(tmp_path, "oneTBB", [_miss("q1", 60), _miss("q2", 60)])
    p = ResultsAggregator(tmp_path).aggregate().products[0]
    assert (p.avg_with_docs, p.avg_without_docs, p.avg_delta) == (None, None, None)
    assert (p.doc_health, p.doc_score, p.status) == (0.0, 0.0, "poor")


@pytest.mark.parametrize("miss", [
    {"question_id": "q2", "question": "Q?", "retrieval_hit": False, "with_docs": None, "without_docs": None,
     "delta": None},
    {**_miss("q2", 60), "error": "judge failed"},
    {**_miss("q2", 60), "with_docs": {"error": "judge failed"}},
])
def test_a_miss_without_a_clean_baseline_score_leaves_the_doc_score_alone(tmp_path, miss):
    # Only a retrieval miss that the baseline answered and no error touched counts as a context zero.
    _make_eval_file(tmp_path, "oneTBB", [_ev("q1", "Q?", 80, 60), miss])
    p = ResultsAggregator(tmp_path).aggregate().products[0]
    assert (p.doc_health, p.doc_score) == (80.0, 80.0)

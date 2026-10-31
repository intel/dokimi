# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Routing: queries from description sentences, the seed help, and the estimate-only mode."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description, body="# B\n\n## A section here\n"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8")
    return f"skill:{root}"


def test_a_skill_with_no_usable_heading_is_queried_by_its_description_sentences(tmp_path, capsys):
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "grill", "Grill the user about a plan. "
                                       "Use when stress-testing a design.",
                                       body="# Grill\n\n## Steps\n"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries-from-headings", "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["queries_self_derived"] is True     # still triage, still stamped
    assert payload["queries_source"] == "description"
    assert payload["lexical"]["n_queries_given"] == 2
    assert "description" in capsys.readouterr().err


def test_description_sentences_are_queries_only_with_two_content_words():
    from dokimi.novelty.routing import description_as_queries

    assert description_as_queries(
        "Stop. Grill the user about a plan. Use when stress-testing\na design!"
    ) == ["Grill the user about a plan.", "Use when stress-testing a design!"]


def test_headings_are_preferred_to_the_description_when_both_give_queries():
    from dokimi.novelty.routing import self_derived_queries

    body = "# Title\n\n## Profile a binary\n"
    assert self_derived_queries(body, "Tune a database. Index a table.") == (
        ["Profile a binary"], "headings")
    assert self_derived_queries("# Title only\n", "Tune a database.") == (
        ["Tune a database."], "description")
    assert self_derived_queries("# Title only\n", "Stop.") == ([], "none")


def test_the_seed_help_says_what_it_fixes_and_does_not_call_the_run_reproducible(capsys):
    with pytest.raises(SystemExit):
        _parse("novelty", "routing", "--help")
    text = " ".join(capsys.readouterr().out.split())
    seed_help = text[text.rindex("--seed"):text.index("--repeats", text.rindex("--seed"))]
    assert "card order" in seed_help
    assert "reproducible" not in seed_help


def test_estimate_only_counts_the_calls_prices_them_and_sends_nothing(tmp_path, capsys,
                                                                     monkeypatch):
    import dokimi.llm_pricing as pricing

    monkeypatch.setattr(LLMClient, "text",
                        lambda self, *a, **k: pytest.fail("the estimate made a model call"))
    monkeypatch.setattr(pricing, "list_cost_usd",
                        lambda model, provider, prompt, completion: prompt * 1e-6 + completion * 2e-6)
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary", "parallelize my loops", "the a of"]),
                       encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "profile a binary with perf record"),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize loops with task groups"),
                  "--queries", str(queries), "--model", "m", "--repeats", "2",
                  "--estimate-only", "--out", str(out))
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["estimate_only"] is True
    assert estimate["n_queries"] == 3 and estimate["n_calls"] == 4  # 2 scorable x 2 repeats
    assert estimate["input_tokens"] > 4 * 50 and estimate["output_tokens"] > 0
    assert estimate["expected_usd"] == round(
        estimate["input_tokens"] * 1e-6 + estimate["output_tokens"] * 2e-6, 6)
    assert not out.exists()


def test_estimate_only_without_a_model_is_lexical_and_free(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "profile a binary with perf record"),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize loops"),
                  "--queries", str(queries), "--estimate-only")
    args.func(args)
    estimate = json.loads(capsys.readouterr().out)
    assert (estimate["n_calls"], estimate["input_tokens"], estimate["expected_usd"]) == (0, 0, 0.0)

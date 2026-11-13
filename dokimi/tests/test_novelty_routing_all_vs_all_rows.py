# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""All-vs-all routing keeps one row per query for the lexical arm, whatever ``--repeats`` is."""

from __future__ import annotations

import json

from dokimi.cli import build_parser
from dokimi.novelty.all_vs_all import all_vs_all
from dokimi.novelty.client import LLMClient
from dokimi.novelty.routing import Candidate, lexical_routing

PERF = Candidate(name="linux-perf", description="Profile a slow Linux binary with perf")
DB = Candidate(name="db-tuner", description="Tune a slow database query with an index")
HUB = Candidate(name="hub", description="slow programs and slow queries advice")
FIELD = [PERF, DB, HUB]
QUERIES = {
    "linux-perf": ["profile my binary with perf", "my slow program needs advice", "the and of"],
    "db-tuner": ["tune a database query"],
}


def test_lexical_arm_has_one_row_per_query_with_gold_pick_and_gold_rank():
    rows = all_vs_all(FIELD, QUERIES)["lexical"]["per_query"]
    assert rows == [
        {"query": "profile my binary with perf", "gold": "linux-perf", "rank": 1,
         "picked": "linux-perf"},
        {"query": "my slow program needs advice", "gold": "linux-perf", "rank": 3,
         "picked": "hub"},
        {"query": "the and of", "gold": "linux-perf", "rank": None,
         "reason": "no content words"},
        {"query": "tune a database query", "gold": "db-tuner", "rank": 1, "picked": "db-tuner"},
    ]


def test_a_row_reads_like_a_single_artifact_lexical_routing_row():
    queries = ["profile my binary with perf", "my slow program needs advice"]
    single = lexical_routing(FIELD, queries, gold=0)["per_query"]
    rows = all_vs_all(FIELD, {"linux-perf": queries})["lexical"]["per_query"]
    assert [{k: v for k, v in row.items() if k != "gold"} for row in rows] == single


def test_the_rows_rescore_to_the_own_rate_of_the_arm():
    lexical = all_vs_all(FIELD, QUERIES)["lexical"]
    scored = [r for r in lexical["per_query"] if r["gold"] == "linux-perf" and r["rank"]]
    own = sum(r["picked"] == r["gold"] for r in scored) / len(scored)
    assert own == lexical["own_rate"]["linux-perf"] == 0.5


def test_the_rows_are_there_without_repeats_and_with_a_model(tmp_path):
    catalog = tmp_path / "catalog"
    for skill in FIELD:
        folder = catalog / "skills" / skill.name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n# B\n",
            encoding="utf-8")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(QUERIES), encoding="utf-8")
    out = tmp_path / "o.json"
    args = build_parser().parse_args([
        "novelty", "routing", "--all-vs-all", "--catalog", str(catalog),
        "--queries-by-skill", str(queries), "--out", str(out)])
    args.func(args)
    result = json.loads(out.read_text(encoding="utf-8"))["result"]
    assert "model" not in result
    assert [r["picked"] for r in result["lexical"]["per_query"] if "picked" in r] == [
        "linux-perf", "hub", "db-tuner"]


def test_the_model_arm_keeps_its_rows_with_one_repeat():
    client = LLMClient(model="m", provider="p", role="selector",
                       transport=lambda prompt, system: (json.dumps({"pick": 1}), None))
    result = all_vs_all(FIELD, QUERIES, client=client, repeats=1)
    rows = result["model"]["noise"]["per_query"]
    assert sorted(row["gold"] for row in rows) == ["db-tuner", "linux-perf", "linux-perf"]
    assert len(result["lexical"]["per_query"]) == 4

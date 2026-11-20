# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""All-vs-all routing: acceptable alternative skills per query (#710, docs/skill-novelty.md)."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands.novelty_routing_report import print_attractors
from dokimi.novelty.all_vs_all import (
    all_vs_all,
    check_acceptable,
    load_queries_by_skill,
    load_queries_with_acceptable,
)
from dokimi.novelty.client import LLMClient
from dokimi.novelty.routing import Candidate, RoutingInputError
from dokimi.utils import InputError

PERF = Candidate(name="linux-perf", description="Profile a slow Linux binary with perf")
DB = Candidate(name="db-tuner", description="Tune a slow database query with an index")
HUB = Candidate(name="hub", description="slow programs and slow queries advice")
FIELD = [PERF, DB, HUB]
STRINGS = {
    "linux-perf": ["profile my binary with perf", "my slow program needs advice", "zzz qqq"],
    "db-tuner": ["tune a database query"],
}
ACCEPTABLE = {"linux-perf": {"my slow program needs advice": ["hub"], "zzz qqq": ["db-tuner"]}}


def _write(tmp_path, data, name="q.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _hub_client():
    def transport(prompt: str, system: str | None):
        lines = prompt.split("TOOLS:", 1)[1].strip().splitlines()
        return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines) if "hub" in line)}), None
    return LLMClient(model="m", role="selector", transport=transport)


def test_a_file_mixes_strings_and_objects_and_the_query_lists_read_as_before(tmp_path):
    path = _write(tmp_path, {"linux-perf": [
        "profile my binary with perf",
        {"query": "my slow program needs advice", "acceptable": ["hub", "linux-perf", "hub"]},
        {"query": "  ", "acceptable": ["hub"]},
        {"query": "zzz qqq"},
    ], "db-tuner": ["tune a database query"]})
    queries, acceptable = load_queries_with_acceptable(path)
    assert queries == {"linux-perf": ["profile my binary with perf", "my slow program needs advice",
                                      "zzz qqq"], "db-tuner": ["tune a database query"]}
    assert load_queries_by_skill(path) == queries
    # the gold named as its own alternative is dropped, a repeat is kept once, a blank query goes
    assert acceptable == {"linux-perf": {"my slow program needs advice": ["hub"]}}


def test_a_file_of_strings_gives_no_acceptable_map_and_no_new_key_in_the_result(tmp_path):
    path = _write(tmp_path, STRINGS)
    queries, acceptable = load_queries_with_acceptable(path)
    assert acceptable == {}
    plain = all_vs_all(FIELD, queries, acceptable=acceptable)
    assert plain == all_vs_all(FIELD, STRINGS)
    assert "acceptable_rate" not in plain["lexical"]
    assert all("acceptable" not in row for row in plain["lexical"]["per_query"])


@pytest.mark.parametrize("item, message", [
    ({"acceptable": ["hub"]}, "needs a string `query`"),
    ({"query": 5, "acceptable": ["hub"]}, "needs a string `query`"),
    ({"query": "slow thing", "acceptable": "hub"}, "`acceptable` must be a list"),
    ({"query": "slow thing", "acceptable": ["hub", 3]}, "`acceptable` must be a list"),
    ({"query": "slow thing", "acceptible": ["hub"]}, "unknown key 'acceptible'"),
    (7, "list of strings or query objects"),
])
def test_a_malformed_query_object_is_refused_naming_the_file_the_skill_and_the_query(
        tmp_path, item, message):
    path = _write(tmp_path, {"linux-perf": [item]})
    with pytest.raises(RoutingInputError, match=message) as raised:
        load_queries_with_acceptable(path)
    assert path in str(raised.value) and "'linux-perf'" in str(raised.value)
    if isinstance(item, dict) and item.get("query") == "slow thing":
        assert "'slow thing'" in str(raised.value)


def test_the_lexical_arm_reports_the_gold_or_acceptable_rate_beside_the_own_rate():
    plain = all_vs_all(FIELD, STRINGS)["lexical"]
    lexical = all_vs_all(FIELD, STRINGS, acceptable=ACCEPTABLE)["lexical"]
    # picks: linux-perf, hub (acceptable), (none) (db-tuner is acceptable but a non-pick never counts)
    assert lexical["own_rate"] == {"linux-perf": 0.3333, "db-tuner": 1.0}
    assert lexical["acceptable_rate"] == {"linux-perf": 0.6667, "db-tuner": 1.0}
    for key in ("confusion", "attractors", "own_rate", "n_scored", "n_unscored"):
        assert lexical[key] == plain[key]


def test_a_lexical_row_keeps_acceptable_only_for_a_query_that_has_alternatives():
    rows = all_vs_all(FIELD, STRINGS, acceptable=ACCEPTABLE)["lexical"]["per_query"]
    assert {r["query"]: r.get("acceptable") for r in rows} == {
        "profile my binary with perf": None, "my slow program needs advice": ["hub"],
        "zzz qqq": ["db-tuner"], "tune a database query": None}
    assert [("acceptable" in r) for r in rows] == [False, True, True, False]


def test_a_query_with_no_content_word_is_unscored_and_not_an_acceptable_pick():
    queries = {"linux-perf": ["the and of"], "db-tuner": ["tune a database query"]}
    lexical = all_vs_all(FIELD, queries,
                         acceptable={"linux-perf": {"the and of": ["hub"]}})["lexical"]
    assert lexical["acceptable_rate"] == {"linux-perf": None, "db-tuner": 1.0}
    assert lexical["per_query"][0]["acceptable"] == ["hub"]


def test_the_model_arm_scores_an_acceptable_pick_per_call_and_keeps_attractors():
    queries = {"linux-perf": ["profile my binary with perf", "my slow program needs advice"]}
    acceptable = {"linux-perf": {"my slow program needs advice": ["hub"]}}
    model = all_vs_all(FIELD, queries, client=_hub_client(), repeats=2, acceptable=acceptable)["model"]
    assert model["own_rate"] == {"linux-perf": 0.0}
    assert model["acceptable_rate"] == {"linux-perf": 0.5}  # 2 of 4 calls
    (attractor,) = model["attractors"]
    assert (attractor["skill"], attractor["wrongly_picked"]) == ("hub", 4)
    noise = {r["query"]: r for r in model["noise"]["per_query"]}
    assert noise["my slow program needs advice"]["acceptable"] == ["hub"]
    assert "acceptable" not in noise["profile my binary with perf"]


def test_the_model_arm_without_alternatives_has_no_acceptable_key():
    model = all_vs_all(FIELD, STRINGS, client=_hub_client())["model"]
    assert "acceptable_rate" not in model
    assert all("acceptable" not in r for r in model["noise"]["per_query"])


def test_the_gold_named_as_its_own_alternative_is_not_counted_twice_in_either_arm():
    queries = {"linux-perf": ["profile my binary with perf"], "hub": ["slow programs advice"]}
    alone = {"linux-perf": {"profile my binary with perf": ["linux-perf"]}}
    mixed = {"hub": {"slow programs advice": ["hub", "db-tuner"]}}
    plain = all_vs_all(FIELD, queries, client=_hub_client())
    result = all_vs_all(FIELD, queries, client=_hub_client(), acceptable=alone)
    assert result == plain
    both = all_vs_all(FIELD, queries, client=_hub_client(), acceptable=mixed)
    for arm in (both["lexical"], both["model"]):
        assert arm["acceptable_rate"]["hub"] == arm["own_rate"]["hub"] == 1.0
        assert arm["acceptable_picks"] == {"linux-perf": 0, "hub": 0}
    assert both["lexical"]["per_query"][1]["acceptable"] == ["db-tuner"]


def test_an_acceptable_name_outside_the_field_is_refused_naming_skill_query_and_name():
    with pytest.raises(RoutingInputError) as raised:
        all_vs_all(FIELD, STRINGS, acceptable={"linux-perf": {"zzz qqq": ["ghost"]}})
    message = str(raised.value)
    assert "`linux-perf`" in message and "'zzz qqq'" in message and "`ghost`" in message


def test_the_acceptable_names_of_a_dropped_gold_skill_are_moot():
    check_acceptable(["a", "b"], {"a": ["x y"]}, {"gone": {"x y": ["ghost"]}})
    with pytest.raises(RoutingInputError, match="`ghost`"):
        check_acceptable(["a", "b"], {"a": ["x y"]}, {"a": {"x y": ["ghost"]}})


def test_the_summary_prints_the_gold_or_acceptable_rate_next_to_the_gold_rate(capsys):
    print_attractors(all_vs_all(FIELD, STRINGS, acceptable=ACCEPTABLE))
    assert "lexical: gold 2 of 4 queries (0.500), gold or acceptable 3 of 4 (0.750)" in (
        capsys.readouterr().out)
    print_attractors(all_vs_all(FIELD, STRINGS))
    assert "gold or acceptable" not in capsys.readouterr().out


def test_the_pooled_counts_are_exact_at_a_scale_where_a_rate_rounds_to_zero(capsys):
    from collections import Counter

    from dokimi.novelty.all_vs_all import _attractor_table

    picks = {"a": Counter({"a": 1, "x": 29999}), "b": Counter({"x": 30000})}
    table = _attractor_table(picks, {"a": 0, "b": 0}, ["a", "b", "x"], [], {"b": 1, "a": 0})
    assert table["acceptable_rate"]["b"] == 0.0 and table["own_rate"]["a"] == 0.0
    print_attractors({"tied_groups": [], "lexical": table})
    assert ("lexical: gold 1 of 60000 queries (0.000), gold or acceptable 2 of 60000 (0.000)"
            in capsys.readouterr().out)


def test_the_summary_says_so_when_no_query_of_an_arm_was_scored(capsys):
    result = all_vs_all(FIELD, {"linux-perf": ["the of"]}, acceptable={"linux-perf": {"the of": ["hub"]}})
    print_attractors(result)
    assert "lexical: no queries scored" in capsys.readouterr().out


def _catalog(tmp_path, disabled=()):
    root = tmp_path / "catalog"
    for name in ("a", "b", *disabled):
        skill_dir = root / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        extra = "disable-model-invocation: true\n" if name in disabled else ""
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: tune {name} things\n{extra}---\n\n# B\n",
            encoding="utf-8")
    return root


def _run(tmp_path, queries, *extra, disabled=()):
    args = build_parser().parse_args(
        ["catalog", "routing", str(_catalog(tmp_path, disabled)),
         "--queries-by-skill", _write(tmp_path, queries), "--out", str(tmp_path / "out.json"),
         *extra])
    args.func(args)
    return json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))


def test_the_command_saves_the_acceptable_rate_and_refuses_a_name_outside_the_field(tmp_path):
    saved = _run(tmp_path, {"a": [{"query": "tune b things", "acceptable": ["b"]}]})
    assert saved["result"]["lexical"]["acceptable_rate"]["a"] is not None
    with pytest.raises(InputError, match="`ghost`"):
        _run(tmp_path, {"a": [{"query": "tune b things", "acceptable": ["ghost"]}]})
    with pytest.raises(InputError, match="`ghost`"):
        _run(tmp_path, {"a": [{"query": "tune b things", "acceptable": ["ghost"]}]},
             "--dry-run")


def test_the_command_ignores_the_alternatives_of_a_skill_the_field_leaves_out(tmp_path):
    saved = _run(tmp_path, {"a": ["tune a things"],
                            "c": [{"query": "tune c things", "acceptable": ["ghost"]}]},
                 disabled=("c",))
    assert "acceptable_rate" not in saved["result"]["lexical"]

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""All-vs-all routing: every skill in the field is the gold in turn."""

from __future__ import annotations

import functools
import json

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient
from dokimi.novelty.all_vs_all import all_vs_all, load_queries_by_skill
from dokimi.novelty.routing import Candidate, RoutingInputError

PERF = Candidate(name="linux-perf",
                 description="Profile a Linux binary with perf: record, report, annotate")
TBB = Candidate(name="onetbb-quickstart",
                description="Parallelize C++ loops with oneTBB task groups")
MKL = Candidate(name="mkl-advisor",
                description="Choose oneMKL BLAS routines for dense linear algebra")


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _catalog_skill(root, name, description):
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")


def _write_queries(tmp_path, by_skill):
    path = tmp_path / "queries_by_skill.json"
    path.write_text(json.dumps(by_skill), encoding="utf-8")
    return path


# hub's card shares "slow" with the others' queries, so it takes them. Lexically it picks
# linux-perf's two queries and nothing of mkl-advisor's; the model arm (below) picks it for all.
HUB = Candidate(name="hub", description="slow programs and slow queries advice")
ALL_FIELD = [PERF, HUB, MKL]
ALL_QUERIES = {
    "linux-perf": ["my slow program needs advice", "profile a slow program"],
    "mkl-advisor": ["choose blas routines for dense linear algebra", "zzz unrelated words"],
}


def test_all_vs_all_names_the_skill_that_takes_the_queries_of_others():
    confusion = all_vs_all(ALL_FIELD, ALL_QUERIES)["lexical"]["confusion"]
    assert confusion["linux-perf"] == {"hub": 2}


def test_an_attractor_row_says_how_many_sibling_queries_it_took_and_from_whom():
    result = all_vs_all(ALL_FIELD, ALL_QUERIES)
    assert result["n_skills"] == 3 and result["n_queries"] == 4
    (row,) = result["lexical"]["attractors"]
    assert row == {"skill": "hub", "wrongly_picked": 2, "sibling_queries": 4, "rate": 0.5,
                   "from": ["linux-perf"]}


def test_a_query_no_card_shares_a_word_with_is_none_not_the_first_card():
    # linux-perf is first in the field, so the lexical tie-break would name it here.
    lexical = all_vs_all(ALL_FIELD, ALL_QUERIES)["lexical"]
    assert lexical["confusion"]["mkl-advisor"] == {"mkl-advisor": 1, "(none)": 1}
    assert lexical["own_rate"]["mkl-advisor"] == 0.5


def test_the_model_arm_is_reported_only_when_a_client_is_given():
    assert "model" not in all_vs_all(ALL_FIELD, ALL_QUERIES)


def test_the_model_arm_attractor_is_the_skill_the_selector_always_picks():
    def transport(prompt: str, system: str | None):
        lines = prompt.split("TOOLS:", 1)[1].strip().splitlines()
        return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines)
                                        if "hub" in line)}), None

    client = LLMClient(model="m", role="selector", transport=transport)
    model = all_vs_all(ALL_FIELD, ALL_QUERIES, client=client, seed=7)["model"]
    assert model["seed"] == 7
    (row,) = model["attractors"]
    assert (row["skill"], row["wrongly_picked"], row["sibling_queries"]) == ("hub", 4, 4)
    assert row["from"] == ["linux-perf", "mkl-advisor"]


@pytest.mark.parametrize("name", ["model-a", "model-b"])
def test_the_model_arm_names_the_selector_model_that_made_it(name):
    client = LLMClient(model=name, role="selector",
                       transport=lambda prompt, system: (json.dumps({"pick": 1}), None))
    assert all_vs_all(ALL_FIELD, ALL_QUERIES, client=client)["model"]["model"] == name


@pytest.mark.parametrize("field, queries, message", [
    ([PERF], {"linux-perf": ["q"]}, "at least two skills"),
    ([PERF, PERF], {"linux-perf": ["q"]}, "more than once"),
    ([PERF, TBB], {"mkl-advisor": ["q"]}, "not in the field: `mkl-advisor`"),
    ([PERF, TBB], {}, "no skill in the field has queries"),
])
def test_all_vs_all_refuses_a_field_where_it_would_not_measure_selection(field, queries,
                                                                         message):
    with pytest.raises(RoutingInputError, match=message):
        all_vs_all(field, queries)


def test_a_queries_file_names_skills_by_object_and_drops_blank_requests(tmp_path):
    path = _write_queries(tmp_path, {"linux-perf": ["  ", "profile it"], "db": [" "]})
    assert load_queries_by_skill(str(path)) == {"linux-perf": ["profile it"]}


@pytest.mark.parametrize("payload", ['["a list"]', '{"linux-perf": "one string"}',
                                     '{"linux-perf": [1]}'])
def test_a_queries_file_that_is_not_an_object_of_string_lists_is_refused(tmp_path, payload):
    path = tmp_path / "q.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(RoutingInputError):
        load_queries_by_skill(str(path))


def _recursion_on_load(monkeypatch):
    """Make json.loads hit the depth limit: whether a deep file does depends on the Python."""
    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(json, "loads", too_deep)


def test_a_queries_file_nested_too_deeply_is_refused_by_name(tmp_path, monkeypatch):
    path = tmp_path / "q.json"
    path.write_text('{"linux-perf": []}', encoding="utf-8")
    _recursion_on_load(monkeypatch)
    with pytest.raises(RoutingInputError, match="nested too deeply"):
        load_queries_by_skill(str(path))


def test_a_queries_file_nested_too_deeply_exits_with_a_message(tmp_path, monkeypatch):
    path = tmp_path / "q.json"
    path.write_text('{"linux-perf": []}', encoding="utf-8")
    _recursion_on_load(monkeypatch)
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf record and report")
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(path), "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit, match="nested too deeply") as exc:
        args.func(args)
    assert "RecursionError" not in str(exc.value)


def test_all_vs_all_writes_the_matrix_and_prints_the_attractors(tmp_path, capsys):
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf record and report")
    _catalog_skill(root, "db", "tune a slow database query with an index")
    _catalog_skill(root, "hub", "general advice for slow programs and slow queries")
    queries = _write_queries(tmp_path, {
        "perf": ["my slow program needs advice", "profile a slow program"],
        "db": ["my database query is slow", "add an index to speed up a query"],
    })
    out = tmp_path / "all_vs_all.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mode"] == "all_vs_all" and payload["n_candidates"] == 3
    assert payload["result"]["lexical"]["confusion"]["db"]["db"] == 2
    assert payload["result"]["lexical"]["confusion"]["perf"] == {"perf": 1, "hub": 1}
    stdout = capsys.readouterr().out
    assert "all-vs-all: 3 skills in the field, 4 queries" in stdout
    assert "lexical: hub picked for 1 of 4 sibling queries (from perf)" in stdout


def test_all_vs_all_with_model_names_its_model_calls_on_stderr(tmp_path, capsys, monkeypatch):
    # One selector call per query: 2 + 2 queries, so 4 calls, each over the 3 cards.
    import dokimi.commands.novelty as novelty_cmd

    def transport(prompt: str, system: str | None):
        return json.dumps({"pick": 1}), None

    monkeypatch.setattr(
        novelty_cmd,
        "_client",
        lambda model, provider, role, args: LLMClient(
            model=model, provider=provider, role=role, transport=transport
        ),
    )
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf record and report")
    _catalog_skill(root, "db", "tune a slow database query with an index")
    _catalog_skill(root, "hub", "general advice for slow programs and slow queries")
    queries = _write_queries(
        tmp_path,
        {
            "perf": ["my slow program needs advice", "profile a slow program"],
            "db": ["my database query is slow", "add an index to speed up a query"],
        },
    )
    args = _parse(
        "novelty",
        "routing",
        "--all-vs-all",
        "--catalog",
        str(root),
        "--queries-by-skill",
        str(queries),
        "--out",
        str(tmp_path / "o.json"),
        "--model",
        "m",
    )
    args.func(args)
    assert (
        "all-vs-all: --model makes 4 model calls, one per query, over 3 cards each"
        in capsys.readouterr().err
    )


def test_all_vs_all_without_model_says_nothing_about_model_calls(tmp_path, capsys):
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf record and report")
    _catalog_skill(root, "db", "tune a slow database query with an index")
    queries = _write_queries(tmp_path, {"perf": ["profile a slow program"]})
    args = _parse(
        "novelty",
        "routing",
        "--all-vs-all",
        "--catalog",
        str(root),
        "--queries-by-skill",
        str(queries),
        "--out",
        str(tmp_path / "o.json"),
    )
    args.func(args)
    assert "model calls" not in capsys.readouterr().err


def test_all_vs_all_takes_no_artifact(tmp_path):
    queries = _write_queries(tmp_path, {"linux-perf": ["q"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--artifact", "skill:/x",
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit, match="takes no --artifact"):
        args.func(args)


def test_a_single_routing_run_still_needs_an_artifact(tmp_path):
    args = _parse("novelty", "routing", "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit, match="--all-vs-all for the whole field"):
        args.func(args)


def _two_identical_cards(order):
    cards = {"a": Candidate(name="a", description="format json output"),
             "b": Candidate(name="b", description="format json output")}
    return [cards[n] for n in order]


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_two_identical_cards_tie_and_neither_is_scored_a_win(order):
    result = all_vs_all(_two_identical_cards(order),
                        {"a": ["format json"], "b": ["format json"]})["lexical"]
    assert result["own_rate"] == {"a": 0.0, "b": 0.0}
    assert result["confusion"] == {"a": {"b": 1}, "b": {"a": 1}}


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_two_identical_cards_are_one_tied_group_and_no_attractor(order):
    result = all_vs_all(_two_identical_cards(order),
                        {"a": ["format json"], "b": ["format json"]})
    assert result["tied_groups"] == [list(order)]
    assert result["lexical"]["attractors"] == []


def test_an_attractor_outside_the_tied_group_is_still_reported():
    field = [Candidate(name="a", description="format json output"),
             Candidate(name="b", description="format json output"),
             Candidate(name="c", description="profile binary")]
    result = all_vs_all(field, {"a": ["profile"], "c": ["profile"]})
    assert result["tied_groups"] == [["a", "b"]]
    assert result["lexical"]["attractors"] == [{
        "skill": "c", "wrongly_picked": 1, "sibling_queries": 1, "rate": 1.0, "from": ["a"],
    }]


def test_a_tied_group_member_is_a_real_attractor_for_an_outside_query_only():
    # A query naming jsonfmt breaks the tie, so jsonfmt is picked for c's query: an attractor.
    # jsonpretty's queries land on jsonfmt, its group-mate, so they are neither an attraction
    # nor sibling queries: jsonfmt's sibling count is c's one query, not c's plus jsonpretty's two.
    field = [Candidate(name="jsonfmt", description="format json output"),
             Candidate(name="jsonpretty", description="format json output"),
             Candidate(name="c", description="profile binary")]
    result = all_vs_all(field, {"c": ["jsonfmt format"],
                                "jsonpretty": ["format json", "format json"]})
    assert result["lexical"]["attractors"] == [{
        "skill": "jsonfmt", "wrongly_picked": 1, "sibling_queries": 1, "rate": 1.0,
        "from": ["c"],
    }]


def test_two_empty_descriptions_are_not_a_tied_group():
    field = [Candidate(name="a", description=""), Candidate(name="b", description="  ")]
    assert all_vs_all(field, {"a": ["alpha"]})["tied_groups"] == []


def test_the_model_arm_does_not_count_a_pick_of_the_group_mate():
    def transport(prompt: str, system: str | None):
        lines = prompt.split("TOOLS:", 1)[1].strip().splitlines()
        return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines)
                                        if "jsonfmt" in line)}), None

    field = [Candidate(name="jsonfmt", description="format json output"),
             Candidate(name="jsonpretty", description="format json output"),
             Candidate(name="c", description="profile binary")]
    client = LLMClient(model="m", role="selector", transport=transport)
    model = all_vs_all(field, {"c": ["profile"], "jsonpretty": ["format json"]},
                       client=client)["model"]
    assert [(row["skill"], row["from"]) for row in model["attractors"]] == [("jsonfmt", ["c"])]


def test_a_three_way_tie_is_not_a_perfect_pick():
    field = [Candidate(name=f"s{i}", description="common words") for i in range(3)]
    lexical = all_vs_all(field, {"s0": ["common"]})["lexical"]
    assert lexical["own_rate"] == {"s0": 0.0}


def test_attractors_are_ranked_by_count_and_rated_over_the_other_skills_queries():
    # hub takes 2 of perf's 2 queries and 1 of db's 3; sink takes 1 of perf's.
    field = [Candidate(name="perf", description="profile binary"),
             Candidate(name="db", description="index query"),
             Candidate(name="hub", description="slow advice"),
             Candidate(name="sink", description="gpu offload")]
    queries = {"perf": ["slow one", "slow two", "gpu thing"],
               "db": ["slow query", "index query", "index query again"]}
    lexical = all_vs_all(field, queries)["lexical"]
    hub, sink = lexical["attractors"][:2]
    assert hub == {"skill": "hub", "wrongly_picked": 3, "sibling_queries": 6, "rate": 0.5,
                   "from": ["perf", "db"]}
    assert sink["skill"] == "sink" and sink["wrongly_picked"] == 1


def test_an_attractor_with_queries_of_its_own_is_rated_over_the_other_skills_queries_only():
    # perf takes db's one query; perf's own three queries are not sibling queries of perf.
    field = [Candidate(name="perf", description="profile binary slow"),
             Candidate(name="db", description="index query")]
    queries = {"perf": ["profile binary", "profile run", "binary size"], "db": ["slow"]}
    assert all_vs_all(field, queries)["lexical"]["attractors"] == [
        {"skill": "perf", "wrongly_picked": 1, "sibling_queries": 1, "rate": 1.0,
         "from": ["db"]}]


def _positional_client(pick_for):
    def transport(prompt: str, system: str | None):
        picked = pick_for(prompt)
        return json.dumps({"pick": picked}), None
    return LLMClient(model="m", role="selector", transport=transport)


def test_a_selector_that_abstains_is_none_and_counted_in_the_model_arm():
    model = all_vs_all(ALL_FIELD, ALL_QUERIES, client=_positional_client(lambda p: 0))["model"]
    assert model["confusion"] == {"linux-perf": {"(none)": 2}, "mkl-advisor": {"(none)": 2}}
    assert model["n_abstained"] == 4 and model["n_failed"] == 0
    assert model["attractors"] == []


def test_the_model_arm_seeds_each_gold_apart_and_hands_model_routing_the_gold(monkeypatch):
    import dokimi.novelty.all_vs_all as all_vs_all_module

    seen = []
    real = all_vs_all_module.model_routing

    def spy(candidates, queries, **kwargs):
        seen.append((kwargs["gold"], kwargs["seed"]))
        return real(candidates, queries, **kwargs)

    monkeypatch.setattr(all_vs_all_module, "model_routing", spy)
    all_vs_all(ALL_FIELD, ALL_QUERIES, client=_positional_client(lambda p: 1), seed=10)
    assert seen == [(0, 10), (2, 12)]


def test_the_model_arm_follows_each_call_not_one_fixed_card():
    calls = iter([1, 2, 3, 1])
    model = all_vs_all(ALL_FIELD, ALL_QUERIES,
                       client=_positional_client(lambda p: next(calls)), seed=3)["model"]
    assert sum(sum(row.values()) for row in model["confusion"].values()) == 4
    assert len({name for row in model["confusion"].values() for name in row}) > 1


def test_a_transport_that_raises_is_counted_not_reported_as_a_clean_zero():
    def transport(prompt: str, system: str | None):
        raise RuntimeError("boom")

    client = LLMClient(model="m", role="selector", transport=transport)
    model = all_vs_all(ALL_FIELD, ALL_QUERIES, client=client)["model"]
    assert model["n_failed"] == 4 and model["n_abstained"] == 0
    assert "boom" in model["first_error"]


def test_a_transport_that_raises_with_no_message_is_still_counted_as_failed():
    def transport(prompt: str, system: str | None):
        raise RuntimeError()

    client = LLMClient(model="m", role="selector", transport=transport)
    model = all_vs_all(ALL_FIELD, ALL_QUERIES, client=client)["model"]
    assert model["n_failed"] == 4 and model["n_abstained"] == 0
    assert model["first_error"]


def test_all_vs_all_exits_nonzero_when_every_model_call_fails(tmp_path, capsys, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    def transport(prompt: str, system: str | None):
        raise RuntimeError("boom")

    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role, transport=transport))
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf record and report")
    _catalog_skill(root, "db", "tune a slow database query with an index")
    queries = _write_queries(tmp_path, {"perf": ["profile a slow program"],
                                        "db": ["add an index"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"),
                  "--model", "m")
    with pytest.raises(SystemExit, match="2 of 2 model call.s. failed"):
        args.func(args)
    captured = capsys.readouterr()
    assert "OK Saved" not in captured.out
    assert "2 model call(s) failed" in captured.err and "boom" in captured.err


def test_all_vs_all_stops_at_the_max_usd_cap_the_docs_point_to(tmp_path, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd
    from dokimi.novelty.client import SpendCap
    from dokimi.metrics.usage import UsageRecord

    calls: list[str] = []

    def transport(prompt: str, system: str | None = None):
        calls.append(prompt)
        return "none", UsageRecord(cost_usd=0.6)

    cap = SpendCap(limit_usd=1.0)
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role, transport=transport, cap=cap))
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf record and report")
    _catalog_skill(root, "db", "tune a slow database query with an index")
    queries = _write_queries(tmp_path, {"perf": ["profile a slow program", "find hot functions"],
                                        "db": ["add an index"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"),
                  "--model", "m", "--max-usd", "1")
    with pytest.raises(SystemExit, match=r"\$1\.20 of \$1\.00"):
        args.func(args)
    assert len(calls) == 2  # the third request was refused before it reached the provider


@pytest.mark.parametrize("make", ["missing", "not_utf8"])
def test_an_unreadable_queries_file_is_a_message_not_a_traceback(tmp_path, make):
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary")
    _catalog_skill(root, "db", "tune a slow database query")
    queries = tmp_path / "q.json"
    if make == "not_utf8":
        queries.write_bytes(b"\xff\xfe\x00bad")
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit, match="cannot read the queries"):
        args.func(args)


def _cat(root, names_descriptions):
    for name, description in names_descriptions.items():
        _catalog_skill(root, name, description)


def test_a_partial_model_failure_exits_non_zero_and_leaves_failed_queries_out(
        tmp_path, capsys, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    answered = []

    def transport(prompt: str, system: str | None):
        if answered:
            raise RuntimeError("rate limited")
        answered.append(1)
        return '{"pick": 1}', None

    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role, transport=transport))
    root = tmp_path / "catalog"
    _cat(root, {"perf": "profile a slow binary with perf", "db": "tune a slow database query"})
    queries = _write_queries(tmp_path, {"db": ["add an index"], "perf": ["profile it", "perf it"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out), "--model", "m")
    with pytest.raises(SystemExit, match="2 of 3 model call"):
        args.func(args)
    assert "OK Saved" not in capsys.readouterr().out
    model = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]
    assert model["n_failed"] == 2
    # perf's two queries both failed: no rate, not a measured 0.0.
    assert model["own_rate"]["perf"] is None and model["n_scored"]["perf"] == 0
    assert model["n_unscored"] == {"perf": 2}


def test_a_query_with_no_content_word_is_not_scored_as_a_miss():
    field = [Candidate("alpha", "convert a json file to yaml"),
             Candidate("beta", "tune a slow database query")]
    result = all_vs_all(field, {"alpha": ["convert my json file to yaml",
                                          "json to yaml please", "the of and"]})
    lexical = result["lexical"]
    assert lexical["own_rate"]["alpha"] == 1.0
    assert lexical["n_scored"] == {"alpha": 2} and lexical["n_unscored"] == {"alpha": 1}


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_a_tie_among_other_cards_is_a_tie_in_any_field_order(order):
    cards = [Candidate("fft", "fast transforms"), Candidate("linalg", "fast transforms"),
             Candidate("random", "fast transforms"),
             Candidate("gold", "something unrelated entirely")]
    field = [cards[i] for i in order] + [cards[3]]
    lexical = all_vs_all(field, {"gold": ["fast transforms"]})["lexical"]
    assert lexical["confusion"]["gold"] == {"(tie)": 1}
    assert lexical["attractors"] == []


def test_the_saved_payload_records_the_field_rule(tmp_path):
    root = tmp_path / "catalog"
    _cat(root, {"perf": "profile a slow binary", "db": "tune a slow database query"})
    hidden = root / "skills" / "ghost"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text(
        "---\nname: ghost\ndescription: x\ndisable-model-invocation: true\n---\n\n# B\n",
        encoding="utf-8")
    queries = _write_queries(tmp_path, {"perf": ["profile a binary"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out))
    args.func(args)
    field = json.loads(out.read_text(encoding="utf-8"))["field"]
    assert field["include_disabled"] is False
    assert sorted(field["competitors"]) == ["db", "perf"]
    assert len(field["left_out"]) == 1 and "ghost" in field["left_out"][0]


def test_a_skill_named_twice_in_the_queries_file_is_refused(tmp_path):
    path = tmp_path / "q.json"
    path.write_text('{"a": ["profile"], "a": ["slow"]}', encoding="utf-8")
    with pytest.raises(RoutingInputError, match="skill 'a' appears more than once"):
        load_queries_by_skill(str(path))


def test_the_not_in_the_field_error_names_the_disable_flag_and_its_override():
    with pytest.raises(RoutingInputError, match="disable-model-invocation.*--include-disabled"):
        all_vs_all([PERF, MKL], {"ghost": ["x y"]})


@pytest.mark.parametrize("flag", [["--queries", "q.txt"], ["--queries-from-headings"]])
def test_all_vs_all_refuses_the_single_artifact_query_flags(tmp_path, flag):
    queries = _write_queries(tmp_path, {"perf": ["profile"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(tmp_path),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"), *flag)
    with pytest.raises(SystemExit, match="takes --queries-by-skill, not --queries"):
        args.func(args)


def test_all_vs_all_refuses_repeats_it_would_not_make(tmp_path, monkeypatch):
    # all_vs_all makes one call per query, so --repeats 3 would be dropped without a word.
    import dokimi.commands.novelty as novelty_cmd

    calls = []
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role,
        transport=lambda prompt, system: calls.append(prompt) or (json.dumps({"pick": 1}), None)))
    root = tmp_path / "catalog"
    _cat(root, {"perf": "profile a slow binary", "db": "tune a slow database query"})
    queries = _write_queries(tmp_path, {"perf": ["profile my slow binary"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"),
                  "--model", "m", "--repeats", "3")
    with pytest.raises(SystemExit, match="takes no --repeats"):
        args.func(args)
    assert calls == [] and not (tmp_path / "o.json").exists()


def test_a_single_artifact_routing_run_records_the_field_rule_too(tmp_path):
    root = tmp_path / "catalog"
    _cat(root, {"perf": "profile a slow binary", "db": "tune a slow database query"})
    queries = tmp_path / "q.txt"
    queries.write_text("profile a binary\n", encoding="utf-8")
    out = tmp_path / "r.json"
    args = _parse("novelty", "routing", "--artifact", f"skill:{root / 'skills' / 'perf'}",
                  "--catalog", str(root), "--include-disabled", "--queries", str(queries),
                  "--out", str(out))
    args.func(args)
    field = json.loads(out.read_text(encoding="utf-8"))["field"]
    assert field == {"include_disabled": True, "competitors": ["db"], "left_out": []}


def test_console_says_how_many_tied_and_unscored_queries_are_not_counted_as_picks(
        tmp_path, capsys):
    # A tie and a query with no content word are the two cases where no pick is named. The
    # summary must not read "no skill was picked" without saying what was left out beside it.
    root = tmp_path / "catalog"
    _catalog_skill(root, "fft", "fast transforms")
    _catalog_skill(root, "linalg", "fast transforms")
    _catalog_skill(root, "gold", "something unrelated entirely")
    queries = _write_queries(tmp_path, {"gold": ["fast transforms", "the of and"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    args.func(args)
    stdout = capsys.readouterr().out
    assert "lexical: no skill was picked for another skill's query" in stdout
    assert "lexical: 1 tied and 1 unscored queries are not counted as picks" in stdout


def test_console_warns_when_the_lexical_arm_cannot_score_any_query_of_a_skill(tmp_path, capsys):
    root = tmp_path / "catalog"
    _catalog_skill(root, "fft", "fast transforms")
    _catalog_skill(root, "gold", "something unrelated entirely")
    queries = _write_queries(tmp_path, {"gold": ["the of and", "a an the"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    args.func(args)
    err = capsys.readouterr().err
    assert "the lexical arm cannot score any query of gold" in err
    assert "no content word" in err


def test_two_contentless_queries_of_one_skill_are_two_unscored_and_no_rate():
    field = [Candidate("alpha", "convert a json file to yaml"),
             Candidate("beta", "tune a slow database query")]
    lexical = all_vs_all(field, {"alpha": ["the of and", "a an the"]})["lexical"]
    assert lexical["n_unscored"] == {"alpha": 2}
    assert lexical["n_scored"] == {"alpha": 0}
    assert lexical["own_rate"]["alpha"] is None


def _run_console(tmp_path, capsys, skills, queries_by_skill):
    root = tmp_path / "catalog"
    for name, description in skills.items():
        _catalog_skill(root, name, description)
    queries = _write_queries(tmp_path, queries_by_skill)
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    args.func(args)
    return capsys.readouterr().out


def test_console_counts_unscored_queries_when_nothing_tied(tmp_path, capsys):
    stdout = _run_console(tmp_path, capsys,
                          {"fft": "fast transforms", "gold": "something unrelated entirely"},
                          {"gold": ["something unrelated", "the of and"]})
    assert "lexical: 0 tied and 1 unscored queries are not counted as picks" in stdout


def test_console_counts_tied_queries_when_nothing_is_unscored(tmp_path, capsys):
    stdout = _run_console(tmp_path, capsys,
                          {"fft": "fast transforms", "linalg": "fast transforms",
                           "gold": "something unrelated entirely"},
                          {"gold": ["fast transforms", "transforms fast"]})
    assert "lexical: 2 tied and 0 unscored queries are not counted as picks" in stdout


def test_console_names_a_tied_group_once_and_not_as_attractors(tmp_path, capsys):
    stdout = _run_console(tmp_path, capsys,
                          {"fft": "fast transforms", "linalg": "fast transforms",
                           "gold": "something unrelated entirely"},
                          {"fft": ["fast transforms"], "gold": ["something unrelated"]})
    assert ("tied: fft, linalg have the same description; one group, not attractors of each "
            "other") in stdout
    assert "sibling queries" not in stdout


def test_console_sums_unscored_queries_over_every_skill(tmp_path, capsys):
    stdout = _run_console(tmp_path, capsys,
                          {"fft": "fast transforms", "gold": "something unrelated entirely"},
                          {"gold": ["the of and", "a an the", "something unrelated"],
                           "fft": ["the and", "fast transforms"]})
    assert "lexical: 0 tied and 3 unscored queries are not counted as picks" in stdout


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_exactly_two_other_cards_tied_at_the_top_are_a_tie(order):
    cards = [Candidate("fft", "fast transforms"), Candidate("linalg", "fast transforms")]
    field = [cards[i] for i in order] + [Candidate("gold", "something unrelated entirely")]
    lexical = all_vs_all(field, {"gold": ["fast transforms"]})["lexical"]
    assert lexical["confusion"]["gold"] == {"(tie)": 1}
    assert lexical["attractors"] == []


def test_the_model_arm_does_not_send_a_query_with_no_content_word():
    prompts: list[str] = []

    def transport(prompt: str, system: str | None):
        prompts.append(prompt)
        return '{"pick": 1}', None

    field = [Candidate("alpha", "convert a json file to yaml"),
             Candidate("beta", "tune a slow database query")]
    client = LLMClient(model="m", provider="p", role="selector", transport=transport)
    result = all_vs_all(field, {"alpha": ["the of and", "convert json"],
                                "beta": ["a an the"]}, client=client)
    model, lexical = result["model"], result["lexical"]
    assert len(prompts) == 1
    assert model["n_scored"] == lexical["n_scored"] == {"alpha": 1, "beta": 0}
    assert model["n_unscored"] == lexical["n_unscored"] == {"alpha": 1, "beta": 1}
    assert model["own_rate"]["beta"] is None and model["n_failed"] == 0


def test_a_failed_call_adds_to_the_contentless_queries_of_the_same_skill():
    def transport(prompt: str, system: str | None):
        raise RuntimeError("boom")

    field = [Candidate("alpha", "convert a json file to yaml"),
             Candidate("beta", "tune a slow database query")]
    client = LLMClient(model="m", provider="p", role="selector", transport=transport)
    model = all_vs_all(field, {"alpha": ["the of and", "convert json"]}, client=client)["model"]
    assert model["n_unscored"] == {"alpha": 2}
    assert model["n_scored"] == {"alpha": 0}


def test_the_failed_call_count_is_over_the_calls_sent_not_every_query(
        tmp_path, monkeypatch, capsys):
    import dokimi.commands.novelty as novelty_cmd

    def transport(prompt: str, system: str | None):
        raise RuntimeError("boom")

    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: LLMClient(
        model=model, provider=provider, role=role, transport=transport))
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "db", "tune a slow database query")
    queries = _write_queries(tmp_path, {"db": ["add an index", "the of and"]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"),
                  "--model", "m")
    with pytest.raises(SystemExit, match="^1 of 1 model call"):
        args.func(args)
    assert "makes 1 model calls" in capsys.readouterr().err


def _word_client(model, provider, role, args, swap=None):
    """A selector that picks the card holding the request's word, wherever it is shuffled.

    With ``swap``, it picks the card holding ``swap[word]`` instead.
    """
    def transport(prompt: str, system: str | None):
        request, cards = prompt.split("REQUEST:", 1)[1].split("TOOLS:", 1)
        word = (swap or {}).get(request.strip(), request.strip())
        lines = cards.strip().splitlines()
        return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines)
                                        if word in line)}), None
    return LLMClient(model=model, provider=provider, role=role, transport=transport)


def test_the_seed_on_the_command_line_is_the_seed_the_saved_model_arm_records(
        tmp_path, monkeypatch):
    import dokimi.commands.novelty as novelty_cmd

    monkeypatch.setattr(novelty_cmd, "_client", _word_client)
    root = tmp_path / "catalog"
    _cat(root, {"perf": "profile a slow binary", "db": "tune a slow database query"})
    queries = _write_queries(tmp_path, {"perf": ["profile"], "db": ["database"]})
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out), "--model", "m",
                  "--seed", "5")
    args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]
    assert (model["model"], model["seed"]) == ("m", 5)


def test_console_prints_the_top_five_attractors_of_each_arm_by_count(
        tmp_path, capsys, monkeypatch):
    # Lexically, each of six skills takes the gold queries that name its word: alpha one, ...
    # foxtrot six. The selector sends each word to the mirror skill's card instead (alpha's
    # to foxtrot), so the model arm counts the other way round and leaves out foxtrot.
    import dokimi.commands.novelty as novelty_cmd

    words = {"alpha": "anvil", "bravo": "barley", "charlie": "cobalt", "delta": "dune",
             "echo": "ember", "foxtrot": "fjord"}
    mirror = dict(zip(words.values(), reversed(words.values()), strict=True))
    monkeypatch.setattr(novelty_cmd, "_client", functools.partial(_word_client, swap=mirror))
    root = tmp_path / "catalog"
    _cat(root, {**words, "gold": "something unrelated entirely"})
    queries = _write_queries(tmp_path, {"gold": [word for count, word in
                                                 enumerate(words.values(), 1)
                                                 for _ in range(count)]})
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"),
                  "--model", "m")
    args.func(args)
    lines = [line for line in capsys.readouterr().out.splitlines() if "sibling" in line]
    top_five = {"lexical": [("foxtrot", 6), ("echo", 5), ("delta", 4), ("charlie", 3),
                            ("bravo", 2)],
                "model": [("alpha", 6), ("bravo", 5), ("charlie", 4), ("delta", 3),
                          ("echo", 2)]}
    assert lines == [f"{arm}: {name} picked for {count} of 21 sibling queries (from gold)"
                     for arm, rows in top_five.items() for name, count in rows]


def test_attractors_tied_on_count_and_golds_tied_in_from_are_in_name_order():
    # zeta comes first in the field and mu first in the queries: name order has to win.
    field = [Candidate("zeta", "zebra stripes"), Candidate("alpha", "apple orchard"),
             Candidate("mu", "something unrelated entirely"),
             Candidate("beta", "another unrelated thing")]
    queries = {"mu": ["apple", "orchard", "zebra"], "beta": ["zebra"], "zeta": ["stripes"]}
    assert all_vs_all(field, queries)["lexical"]["attractors"] == [
        {"skill": "alpha", "wrongly_picked": 2, "sibling_queries": 5, "rate": 0.4,
         "from": ["mu"]},
        {"skill": "zeta", "wrongly_picked": 2, "sibling_queries": 4, "rate": 0.5,
         "from": ["beta", "mu"]}]

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""All-vs-all routing: every skill in the field is the gold in turn."""

from __future__ import annotations

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

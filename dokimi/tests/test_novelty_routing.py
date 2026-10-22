# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Selection against competitors: the lexical floor, the model arm, and the self-scope guard."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient
from dokimi.novelty.routing import (
    Candidate,
    RoutingInputError,
    catalog_competitors,
    check_field,
    headings_as_queries,
    lexical_routing,
    load_queries,
    model_routing,
)

PERF = Candidate(name="linux-perf",
                 description="Profile a Linux binary with perf: record, report, annotate")
TBB = Candidate(name="onetbb-quickstart",
                description="Parallelize C++ loops with oneTBB task groups")
MKL = Candidate(name="mkl-advisor",
                description="Choose oneMKL BLAS routines for dense linear algebra")
# A competitor phrased the way a user would phrase the problem: this is what beats a
# correctly-scoped description in practice.
XPU = Candidate(name="xpu-port",
                description="My binary is slow on the GPU: find where the cycles go")


def test_a_query_that_names_the_artifact_s_own_terms_ranks_it_first():
    result = lexical_routing([PERF, TBB, MKL], ["record a perf profile and annotate it"])
    # Three candidates cannot produce a meaningful recall@3 -- every rank is <= 3.
    assert (result["recall_at_1"], result["recall_at_3"], result["mrr"]) == (1.0, None, 1.0)
    assert result["per_query"][0]["picked"] == "linux-perf"
    assert result["n_candidates"] == 3


def test_a_competitor_phrased_in_the_user_s_words_wins():
    # The failure the measurement exists for: the right skill loses on vocabulary, not on
    # scope. Measured on the real corpus, linux-perf lost all five independently written
    # queries, three of them to an xpu-* skill (R@1 0.0, R@3 0.8 against 32 competitors).
    result = lexical_routing([PERF, TBB, MKL, XPU],
                             ["my binary is slow, where do the cycles go"])
    assert result["recall_at_1"] == 0.0
    assert result["per_query"][0]["picked"] == "xpu-port"


def test_the_gold_rank_is_reported_per_query_not_only_as_a_rate():
    result = lexical_routing(
        [PERF, TBB, MKL],
        ["parallelize my C++ loops", "record a perf profile"],
    )
    ranks = [row["rank"] for row in result["per_query"]]
    assert ranks == [3, 1] or ranks == [2, 1]      # beaten by oneTBB on its own query
    assert result["recall_at_1"] == 0.5


def test_a_query_with_no_content_words_is_dropped_with_its_reason():
    result = lexical_routing([PERF, TBB], ["the and of"])
    assert result["n_queries"] == 0
    assert result["recall_at_1"] is None
    assert result["per_query"][0]["reason"] == "no content words"


def test_ties_do_not_depend_on_candidate_order():
    same = [Candidate(name=f"s{i}", description="identical description text")
            for i in range(4)]
    first = lexical_routing(same, ["identical description text"], gold=0)
    last = lexical_routing(same, ["identical description text"], gold=3)
    # A tie must not be scored as a win for whichever card happened to be first: the CLI
    # always puts the artifact under test at index 0.
    assert first["recall_at_1"] == 0.0 and last["recall_at_1"] == 0.0
    assert first["per_query"][0]["rank"] == last["per_query"][0]["rank"] == 4


def test_a_query_that_matches_no_card_is_not_a_hit():
    result = lexical_routing([PERF, TBB, MKL], ["deploy kubernetes yaml manifests"])
    assert result["per_query"][0]["rank"] == 3
    assert result["recall_at_1"] == 0.0


def test_a_position_only_strategy_scores_about_one_over_n():
    """A pick-the-first-card model must not score on position.

    A rotation does not give this: with q queries over n candidates it puts the gold card
    at position 1 every n-th query, so the rate is set by how q and n line up. A seeded
    permutation does, and stays reproducible.
    """
    def transport(prompt: str, system: str | None):
        return json.dumps({"pick": 1}), None

    client = LLMClient(model="selector-m", role="selector", transport=transport)
    cards = [PERF, TBB, MKL, XPU, Candidate(name="e", description="five")]
    result = model_routing(cards, [f"q{i}" for i in range(200)], client=client)
    assert 0.10 <= result["selection_rate"] <= 0.30          # 1/5, with sampling slack
    assert result["card_order"] == "seeded random permutation per query"
    assert "upper bound on harness behaviour" in result["note"]

    # The gold card is shown at every position, not just the first and last ones.
    positions = {row["gold_shown_at"] for row in result["per_query"]}
    assert positions == {1, 2, 3, 4, 5}


def test_the_permutation_is_reproducible_and_recorded():
    client = LLMClient(model="m", role="selector",
                       transport=lambda p, s: (json.dumps({"pick": 1}), None))
    first = model_routing([PERF, TBB, MKL], ["q1", "q2"], client=client, seed=7)
    again = model_routing([PERF, TBB, MKL], ["q1", "q2"], client=client, seed=7)
    other = model_routing([PERF, TBB, MKL], ["q1", "q2"], client=client, seed=8)
    orders = [row["order"] for row in first["per_query"]]
    assert orders == [row["order"] for row in again["per_query"]]
    assert orders != [row["order"] for row in other["per_query"]]
    assert set(orders[0]) == {PERF.name, TBB.name, MKL.name}


def test_an_abstention_counts_against_selection_but_not_as_a_wrong_pick():
    client = LLMClient(model="m", role="selector",
                       transport=lambda p, s: (json.dumps({"pick": 0}), None))
    result = model_routing([PERF, TBB], ["q1", "q2"], client=client)
    assert result["n_abstained"] == 2
    # Not picking anything is the routing failure, so the headline rate is 0.0; the
    # among-answered rate is undefined and reported separately.
    assert (result["selection_rate"], result["selection_rate_among_answered"]) == (0.0, None)


def test_a_model_that_abstains_nine_times_out_of_ten_does_not_score_one():
    calls = {"n": 0}

    def transport(prompt: str, system: str | None):
        calls["n"] += 1
        if calls["n"] == 1:
            gold_position = prompt.split("TOOLS:", 1)[1].strip().splitlines()
            pick = next(i + 1 for i, line in enumerate(gold_position)
                        if PERF.name in line)
            return json.dumps({"pick": pick}), None
        return json.dumps({"pick": 0}), None

    client = LLMClient(model="m", role="selector", transport=transport)
    result = model_routing([PERF, TBB, MKL, XPU], [f"q{i}" for i in range(10)],
                           client=client)
    assert result["selection_rate"] == 0.1
    assert result["selection_rate_among_answered"] == 1.0
    assert result["n_abstained"] == 9


def test_one_failed_query_does_not_lose_the_others():
    calls = {"n": 0}

    def transport(prompt: str, system: str | None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider refused")
        return json.dumps({"pick": 1}), None

    client = LLMClient(model="m", role="selector", transport=transport)
    result = model_routing([PERF, TBB], ["q1", "q2"], client=client)
    assert result["n_failed"] == 1
    assert result["selection_rate"] is not None


def test_headings_are_offered_only_as_self_derived_queries():
    body = "# Title\n\n## Recording a profile\n\n## Reading the report\n\n### x\n"
    assert headings_as_queries(body) == ["Recording a profile", "Reading the report"]


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description, body="# B\n\n## A section here\n"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8")
    return f"skill:{root}"


def test_routing_against_a_single_candidate_is_refused(tmp_path):
    # Selecting from one candidate measures tool use rather than this artifact winning.
    args = _parse("novelty", "routing", "--artifact", _skill(tmp_path, "a", "d"),
                  "--queries", "x")
    with pytest.raises(SystemExit, match="needs competitors"):
        args.func(args)


def test_the_catalog_supplies_every_sibling_but_the_artifact(tmp_path, capsys):
    catalog = tmp_path / "skills"
    catalog.mkdir()
    gold = _skill(catalog, "perf", "profile a slow binary with perf")
    _skill(catalog, "xpu", "profile a slow GPU kernel")
    _skill(catalog, "db", "tune a slow database query")
    extra = _skill(tmp_path, "gdb", "debug a crashing binary")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow, where is the time going"]),
                       encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", gold, "--catalog", str(catalog),
                  "--competitors", extra, "--queries", str(queries), "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n_candidates"] == 4                # perf + xpu, db from the catalog + gdb
    assert "--catalog" in capsys.readouterr().err


def test_a_competitor_that_is_also_in_the_catalog_is_counted_once(tmp_path):
    # The CLI excludes the --competitors names from the catalog. Otherwise xpu would be
    # a candidate twice and the run would fail with "more than once".
    catalog = tmp_path / "skills"
    catalog.mkdir()
    gold = _skill(catalog, "perf", "profile a slow binary with perf")
    xpu = _skill(catalog, "xpu", "profile a slow GPU kernel")
    _skill(catalog, "db", "tune a slow database query")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow, where is the time going"]),
                       encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", gold, "--catalog", str(catalog),
                  "--competitors", xpu, "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 3  # perf, xpu once, db


def test_a_catalog_that_is_not_a_directory_is_refused(tmp_path):
    args = _parse("novelty", "routing", "--artifact", _skill(tmp_path, "a", "d"),
                  "--catalog", str(tmp_path / "nope"), "--queries-from-headings")
    with pytest.raises(SystemExit, match="--catalog is not a directory"):
        args.func(args)


def test_routing_without_a_query_source_is_refused(tmp_path):
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"))
    with pytest.raises(SystemExit, match="--queries"):
        args.func(args)


def test_self_derived_queries_are_stamped_on_the_result(tmp_path, capsys):
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries-from-headings", "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["queries_self_derived"] is True
    assert "model" not in payload                      # no --model, no spend
    captured = capsys.readouterr()
    assert "its own relevance" in captured.err
    assert "triage only" in captured.out


def test_external_queries_run_the_lexical_floor_without_a_model(tmp_path):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["where do my cycles go", "profile a binary"]),
                       encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors",
                  _skill(tmp_path, "b", "Where do my cycles go in a database query"),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["queries_self_derived"] is False
    assert payload["lexical"]["n_queries"] == 2
    assert payload["lexical"]["recall_at_1"] == 0.5


@pytest.mark.parametrize("content", [
    {"queries": "zbd info missing"}, {"queries": ["ok", 3]}, ["ok", None], {"queries": 7},
])
def test_queries_value_must_be_a_list_of_strings(tmp_path, content):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(content), encoding="utf-8")
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries))
    with pytest.raises(SystemExit, match="list of strings"):
        args.func(args)


def test_duplicate_competitors_are_refused_or_deduped(tmp_path):
    queries = tmp_path / "q.txt"
    queries.write_text("profile a binary\n", encoding="utf-8")
    competitor = _skill(tmp_path, "b", "tune a database")
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", competitor, competitor, "--queries", str(queries))
    with pytest.raises(SystemExit, match="more than once"):
        args.func(args)


def test_a_description_that_ends_a_sentence_still_matches_its_own_words():
    # The tokenizer kept the period, so "deadlock." could never match "deadlock" and part
    # of the measured BM25 floor was an artifact of punctuation.
    with_period = Candidate(name="lock-advisor", description="Resolve a deadlock.")
    other = Candidate(name="db-tuner", description="Tune database indexes for throughput")
    third = Candidate(name="io-tuner", description="Tune file system readahead")
    fourth = Candidate(name="net-tuner", description="Tune socket buffers")
    result = lexical_routing([with_period, other, third, fourth],
                             ["my program hit a deadlock"])
    assert result["recall_at_1"] == 1.0


def test_a_hyphenated_name_can_be_matched_by_one_of_its_words():
    sibling = Candidate(name="dpnp-array-creation",
                        description="Create dpnp arrays from host data")
    others = [Candidate(name=f"x{i}", description="Something else entirely")
              for i in range(3)]
    result = lexical_routing([sibling, *others], ["how do I create an array"])
    assert result["recall_at_1"] == 1.0


def test_the_query_accounting_says_how_many_were_scored():
    result = lexical_routing([PERF, TBB], ["record a perf profile", "a an the", "of to in"])
    assert (result["n_queries_given"], result["n_queries_scored"]) == (3, 1)


def test_an_out_of_range_gold_is_refused_rather_than_indexed():
    client = LLMClient(model="m", role="selector",
                       transport=lambda p, s: (json.dumps({"pick": 1}), None))
    for call in (lambda: lexical_routing([PERF, TBB], ["q"], gold=7),
                 lambda: lexical_routing([PERF, TBB], ["q"], gold=-1),
                 lambda: model_routing([PERF, TBB], ["q"], client=client, gold=5)):
        with pytest.raises(ValueError, match="outside"):
            call()


def test_the_artifact_cannot_compete_with_itself(tmp_path):
    ref = _skill(tmp_path, "perf", "Profile a binary")
    args = _parse("novelty", "routing", "--artifact", ref, "--competitors", ref,
                  "--queries", "x")
    with pytest.raises(SystemExit, match="against itself"):
        args.func(args)


def test_the_self_derived_stamp_travels_with_each_arm(tmp_path):
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries-from-headings", "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    # A consumer that reads payload["lexical"] alone must still see the caveat.
    assert payload["lexical"]["queries_self_derived"] is True


def test_dotted_identifier_matches_its_parts():
    cards = [Candidate(name="fast-la", description="Speeds up numpy.linalg calls"),
             Candidate(name="other", description="plotting charts")]
    result = lexical_routing(cards, ["numpy linalg slow"])
    assert result["recall_at_1"] == 1.0


@pytest.mark.parametrize("reply", [
    "I think the best one is the third.", '{"pick": null}', '{"pick": "none"}',
    '{"pick": [2]}', '{"pick": 2.9}', '{"pick": true}',
])
def test_an_unusable_reply_is_a_miss_not_a_dropped_query(reply):
    calls = {"n": 0}

    def transport(prompt: str, system: str | None):
        calls["n"] += 1
        if calls["n"] == 1:
            lines = prompt.split("TOOLS:", 1)[1].strip().splitlines()
            return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines)
                                            if PERF.name in line)}), None
        return reply, None

    client = LLMClient(model="m", role="selector", transport=transport)
    result = model_routing([PERF, TBB, MKL, XPU], [f"q{i}" for i in range(10)],
                           client=client)
    assert result["selection_rate"] == 0.1
    assert result["n_failed"] == 0


def test_a_transport_error_is_still_excluded_from_the_rate():
    def transport(prompt: str, system: str | None):
        raise RuntimeError("provider down")

    client = LLMClient(model="m", role="selector", transport=transport)
    result = model_routing([PERF, TBB], ["q1"], client=client)
    assert (result["n_failed"], result["selection_rate"]) == (1, None)


def _run_queries(tmp_path, data: bytes):
    queries = tmp_path / "q.txt"
    queries.write_bytes(data)
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    return json.loads(out.read_text(encoding="utf-8"))["lexical"]


def test_bom_prefixed_json_queries_are_parsed_as_json(tmp_path):
    lexical = _run_queries(tmp_path, b'\xef\xbb\xbf["profile a binary", "perf record"]')
    assert lexical["n_queries_given"] == 2


@pytest.mark.parametrize("data", [b"[urgent] profile a binary\nperf record\n",
                                  b"profile a binary\n"])
def test_line_files_are_lines_even_when_they_look_like_json(tmp_path, data):
    lexical = _run_queries(tmp_path, data)
    assert lexical["n_queries_given"] == len(data.splitlines())


def test_a_field_where_selection_is_no_measurement_is_refused():
    check_field(PERF, [TBB, MKL])
    for competitors, message in (([], "needs competitors"), ([TBB, PERF], "against itself"),
                                 ([TBB, MKL, TBB], "`onetbb-quickstart` more than once")):
        with pytest.raises(RoutingInputError, match=message):
            check_field(PERF, competitors)


@pytest.mark.parametrize("content, queries", [
    (json.dumps(["a b", "  ", "c d"]), ["a b", "c d"]),
    (json.dumps({"queries": ["a b"]}), ["a b"]),
    ("[urgent] build fails\n\nslow run\n", ["[urgent] build fails", "slow run"]),
])
def test_a_query_file_is_a_list_a_queries_key_or_one_per_line(tmp_path, content, queries):
    path = tmp_path / "q"
    path.write_text(content, encoding="utf-8")
    assert load_queries(str(path)) == queries


def test_a_query_file_that_is_not_strings_is_refused_but_a_missing_one_is_not_masked(
        tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"queries": "a b"}), encoding="utf-8")
    with pytest.raises(RoutingInputError, match="list of strings"):
        load_queries(str(path))
    with pytest.raises(FileNotFoundError):
        load_queries(str(tmp_path / "missing.json"))


def test_the_catalog_leaves_out_the_names_it_is_told_to(tmp_path):
    _skill(tmp_path, "perf", "profile a slow binary with perf")
    _skill(tmp_path, "xpu", "profile a slow GPU kernel")
    assert [c.name for c in catalog_competitors(str(tmp_path), {"perf"})] == ["xpu"]
    with pytest.raises(ValueError, match="not a directory"):
        catalog_competitors(str(tmp_path / "nope"), set())


def test_catalog_competitors_with_name_in_competitors_excludes_it_from_catalog(tmp_path):
    # When --competitors specifies a skill that is also in the --catalog directory,
    # the exclusion set must include it so catalog_competitors does not add it again.
    # This mutation test: if the exclusion set drops the --competitors names,
    # the same skill appears twice and check_field fails with "more than once" error.
    _skill(tmp_path, "perf", "profile a slow binary with perf")
    _skill(tmp_path, "xpu", "profile a slow GPU kernel")
    _skill(tmp_path, "cache", "profile cache misses")

    # The exclusion set should include both gold and named competitors.
    # In the CLI, this is: {gold.name, *(c.name for c in competitors)}
    excluded = {"perf"}  # perf is a named competitor and also in the catalog
    catalog = catalog_competitors(str(tmp_path), excluded)

    # perf should not appear twice; the catalog must exclude it.
    names = [c.name for c in catalog]
    assert names.count("perf") == 0, "perf should be excluded from catalog"
    assert set(names) == {"xpu", "cache"}, f"Expected xpu and cache, got {names}"



def _answer_as(name: str):
    """A transport reply that picks the card called ``name``, wherever it is shown."""
    def reply(prompt: str) -> str:
        lines = prompt.split("TOOLS:", 1)[1].strip().splitlines()
        return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines)
                                        if line.split(" ", 1)[1].startswith(name))})
    return reply


@pytest.mark.parametrize("reply", [
    "I think the best one is the third.", '{"pick": 0}', '{"pick": 999}',
])
def test_a_declined_reply_is_recorded_verbatim(reply):
    client = LLMClient(model="m", role="selector", transport=lambda p, s: (reply, None))
    row = model_routing([PERF, TBB], ["q1"], client=client)["per_query"][0]
    assert row["abstained"] is True
    assert row["raw_reply"] == reply


def test_a_very_long_declined_reply_is_cut_at_the_recording_limit():
    client = LLMClient(model="m", role="selector",
                       transport=lambda p, s: ("x" * 5000, None))
    row = model_routing([PERF, TBB], ["q1"], client=client)["per_query"][0]
    assert len(row["raw_reply"]) == 2000


def test_a_long_declined_reply_keeps_its_head_not_its_tail():
    reply = "a" * 2000 + "b" * 3000
    client = LLMClient(model="m", role="selector", transport=lambda p, s: (reply, None))
    row = model_routing([PERF, TBB], ["q1"], client=client)["per_query"][0]
    assert row["raw_reply"] == reply[:2000]


def test_repeats_below_one_are_refused():
    client = LLMClient(model="m", role="selector",
                       transport=lambda p, s: (json.dumps({"pick": 1}), None))
    with pytest.raises(ValueError, match="at least 1"):
        model_routing([PERF, TBB], ["q1"], client=client, repeats=0)


def test_one_repeat_has_no_noise_block():
    client = LLMClient(model="m", role="selector",
                       transport=lambda p, s: (json.dumps({"pick": 1}), None))
    result = model_routing([PERF, TBB], ["q1", "q2"], client=client)
    assert (result["repeats"], result["noise"]) == (1, None)
    assert [row["repeat"] for row in result["per_query"]] == [0, 0]


def test_noise_reports_each_query_s_agreement_and_the_spread_between_repeats():
    # Query 1 is answered PERF, PERF, TBB; query 2 is PERF three times. Calls run query by
    # query, repeat by repeat, so this script is the order the model is asked in.
    script = iter([PERF.name, PERF.name, TBB.name, PERF.name, PERF.name, PERF.name])

    def transport(prompt: str, system: str | None):
        return _answer_as(next(script))(prompt), None

    client = LLMClient(model="m", role="selector", transport=transport)
    result = model_routing([PERF, TBB, MKL], ["q1", "q2"], client=client, repeats=3)
    noise = result["noise"]
    assert result["repeats"] == 3
    assert [q["modal_pick"] for q in noise["per_query"]] == [PERF.name, PERF.name]
    assert noise["per_query"][0]["agreement"] == pytest.approx(2 / 3, abs=1e-4)
    assert noise["per_query"][1]["agreement"] == 1.0
    assert noise["per_query"][0]["n_correct"] == 2
    assert noise["unstable_queries"] == 1
    # Repeat 2 gets query 1 wrong: the rate goes 1.0, 1.0, 0.5, so the half range is 0.25.
    assert noise["selection_rate_by_repeat"] == [1.0, 1.0, 0.5]
    assert noise["half_range"] == 0.25
    assert [row["repeat"] for row in result["per_query"]] == [0, 1, 2, 0, 1, 2]


def test_a_query_that_failed_on_every_repeat_has_no_agreement_to_report():
    def transport(prompt: str, system: str | None):
        raise RuntimeError("provider down")

    client = LLMClient(model="m", role="selector", transport=transport)
    noise = model_routing([PERF, TBB], ["q1"], client=client, repeats=2)["noise"]
    assert noise == {
        "mean_agreement": None, "unstable_queries": 0,
        "selection_rate_by_repeat": [None, None], "half_range": None,
        "n_queries_compared": 0,
        "per_query": [{"query": "q1", "modal_pick": None, "agreement": None, "picks": [],
                       "n_correct": 0}],
    }


def _completions(monkeypatch, tmp_path, replies: list[str]) -> list[list[dict]]:
    """Route the real default transport into scripted completions, with the cache on."""
    import litellm

    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(tmp_path / "responses"))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    asked: list[list[dict]] = []

    def completion(model, messages, api_key=None, **kw):
        reply = replies[min(len(asked), len(replies) - 1)]
        asked.append(messages)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr(litellm, "completion", completion)
    return asked


def test_with_the_response_cache_on_each_repeat_is_asked_again(monkeypatch, tmp_path):
    # Identical prompts: without a draw index the cache would replay the first reply and
    # every repeat would agree by construction, reporting a noise floor of zero.
    asked = _completions(monkeypatch, tmp_path,
                         [json.dumps({"pick": 1}), json.dumps({"pick": 2}),
                          json.dumps({"pick": 3})])
    client = LLMClient(model="m", provider="openai", role="selector")
    result = model_routing([PERF, TBB, MKL], ["q1"], client=client, repeats=3)
    assert result["n_failed"] == 0          # a refused draw would read as zero calls
    assert len(asked) == 3
    assert [row["picked_at"] for row in result["per_query"]] == [1, 2, 3]


def test_a_repeat_run_through_the_cli_writes_the_noise_block(tmp_path, monkeypatch, capsys):
    import dokimi.commands.novelty as novelty

    def answer(prompt: str) -> str:
        return _answer_as("perf")(prompt)

    monkeypatch.setattr(
        novelty, "_client",
        lambda model, provider, role, args: LLMClient(
            model=model, role=role, transport=lambda p, s: (answer(p), None)),
    )
    artifact = _skill(tmp_path, "perf", "record and annotate a perf profile")
    competitor = _skill(tmp_path, "tbb", "parallelize C++ loops with task groups")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["record a perf profile", "profile my binary"]),
                       encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", artifact, "--competitors", competitor,
                  "--queries", str(queries), "--model", "m", "--repeats", "2",
                  "--out", str(out))
    args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["model"]
    assert model["repeats"] == 2
    assert model["noise"]["selection_rate_by_repeat"] == [1.0, 1.0]
    assert model["noise"]["half_range"] == 0.0
    assert "noise    2 repeats per query" in capsys.readouterr().out


def test_repeats_without_a_model_are_refused_on_the_cli(tmp_path):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--repeats", "3")
    with pytest.raises(SystemExit, match="needs --model"):
        args.func(args)


def test_a_declining_model_is_named_on_the_cli_with_where_its_replies_are(
        tmp_path, monkeypatch, capsys):
    import dokimi.commands.novelty as novelty

    monkeypatch.setattr(
        novelty, "_client",
        lambda model, provider, role, args: LLMClient(
            model=model, role=role,
            transport=lambda p, s: ("I would rather not choose.", None)),
    )
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["record a perf profile"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "perf", "record and annotate a perf profile"),
                  "--competitors", _skill(tmp_path, "tbb", "parallelize C++ loops"),
                  "--queries", str(queries), "--model", "m", "--out", str(out))
    args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["model"]
    assert model["per_query"][0]["raw_reply"] == "I would rather not choose."
    assert "1 abstained or unparseable" in capsys.readouterr().out


class _Boom(Exception):
    pass


def _scripted(script):
    """A selector whose n-th call answers a card name, abstains ("-"), or fails ("!")."""
    calls = iter(script)

    def transport(prompt: str, system: str | None):
        step = next(calls)
        if step == "!":
            raise _Boom("provider down")
        if step == "-":
            return json.dumps({"pick": 0}), None
        return _answer_as(step)(prompt), None

    return LLMClient(model="m", role="selector", transport=transport)


def test_a_failed_repeat_is_not_reported_as_sampling_noise():
    # q1 is wrong in both repeats. q2 fails in repeat 0 and is right in repeat 1. Per-repeat
    # rates over each repeat's own answered rows would be [0.0, 0.5]: a half range of 0.25
    # from an error, not from the model.
    client = _scripted([TBB.name, TBB.name, "!", PERF.name])
    noise = model_routing([PERF, TBB, MKL], ["q1", "q2"], client=client, repeats=2)["noise"]
    assert noise["selection_rate_by_repeat"] == [0.0, 0.0]
    assert noise["half_range"] == 0.0
    assert noise["n_queries_compared"] == 1


def test_noise_counts_an_abstention_as_a_pick_and_an_error_as_no_answer():
    # One query, three repeats: PERF, abstained, failed. Agreement is over the two answered
    # repeats (1/2), not the three calls (1/3), and the abstention has its own label.
    client = _scripted([PERF.name, "-", "!"])
    noise = model_routing([PERF, TBB], ["q1"], client=client, repeats=3)["noise"]
    (query,) = noise["per_query"]
    assert query["picks"] == [PERF.name, "(abstained)"]
    assert query["agreement"] == 0.5
    assert noise["mean_agreement"] == 0.5 and noise["unstable_queries"] == 1
    assert noise["n_queries_compared"] == 0
    assert noise["selection_rate_by_repeat"] == [None, None, None]
    assert noise["half_range"] is None


def test_an_abstention_can_be_the_modal_pick():
    client = _scripted(["-", "-", PERF.name])
    (query,) = model_routing([PERF, TBB], ["q1"], client=client, repeats=3)["noise"][
        "per_query"]
    assert query["modal_pick"] == "(abstained)"
    assert query["agreement"] == pytest.approx(2 / 3, abs=1e-4)


def test_a_single_usable_repeat_has_no_half_range():
    from dokimi.novelty.routing import _noise

    noise = _noise([[{"query": "q", "repeat": 0, "picked": PERF.name, "correct": True}]])
    assert noise["selection_rate_by_repeat"] == [1.0]
    assert noise["half_range"] is None

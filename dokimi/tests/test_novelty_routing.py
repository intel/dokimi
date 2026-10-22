# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Selection against competitors: the lexical floor, the model arm, and the self-scope guard."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient
from dokimi.novelty.routing import (
    Candidate,
    RoutingInputError,
    all_vs_all,
    catalog_competitors,
    check_field,
    disables_model_invocation,
    headings_as_queries,
    lexical_routing,
    load_queries,
    load_queries_by_skill,
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



def _catalog_skill(root, name, description):
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")


def test_the_catalog_is_the_skills_yaml_manifest_when_there_is_one(tmp_path):
    # A manifest decides which skills the catalog has: a SKILL.md it does not list is not a
    # sibling, and a listed skill with no directory is reported rather than aborting the run.
    root = tmp_path / "catalog"
    _catalog_skill(root, "xpu", "profile a slow GPU kernel")
    _catalog_skill(root, "db", "tune a slow database query")
    _catalog_skill(root, "unlisted", "profile a slow thing")
    (root / "skills.yaml").write_text("- name: xpu\n- name: db\n- name: ghost\n",
                                      encoding="utf-8")
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(root), {"perf"}, notes)]
    assert names == ["xpu", "db"]
    assert len(notes) == 1 and "ghost" in notes[0]


def test_a_non_skill_file_among_competitors_is_skipped_with_a_warning(tmp_path, capsys):
    # A shell glob over a skills directory passes the helper scripts and an empty directory
    # too; that used to abort the whole run on the first .mjs file.
    helper = tmp_path / "activation-rule.mjs"
    helper.write_text("export {}\n", encoding="utf-8")
    empty = tmp_path / "generated"
    empty.mkdir()
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors", f"skill:{helper}", f"skill:{empty}",
                  _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 2
    err = capsys.readouterr().err
    assert "skipped competitor" in err and "activation-rule.mjs is not a SKILL.md" in err
    assert "the directory has no SKILL.md" in err


def test_a_non_skill_artifact_is_still_refused(tmp_path):
    helper = tmp_path / "activation-rule.mjs"
    helper.write_text("export {}\n", encoding="utf-8")
    args = _parse("novelty", "routing", "--artifact", f"skill:{helper}",
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", "x")
    with pytest.raises(SystemExit, match="cannot load an artifact"):
        args.func(args)


def _model_hidden_skill(root, name, description):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "disable-model-invocation: true\n---\n\n# B\n", encoding="utf-8")
    return f"skill:{skill_dir}"


def test_a_disable_model_invocation_skill_is_not_a_competitor_unless_asked(tmp_path, capsys):
    # ultracave and megacave set the flag: no harness lists them, so a card for them scores a
    # choice the model cannot make.
    catalog = tmp_path / "skills"
    catalog.mkdir()
    gold = _skill(catalog, "perf", "profile a slow binary with perf")
    _skill(catalog, "db", "tune a slow database query")
    _model_hidden_skill(catalog, "ultra", "compress output hard")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", gold, "--catalog", str(catalog),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 2
    assert "left out catalog skill 'ultra'" in capsys.readouterr().err

    kept = tmp_path / "kept.json"
    args = _parse("novelty", "routing", "--artifact", gold, "--catalog", str(catalog),
                  "--queries", str(queries), "--out", str(kept), "--include-disabled")
    args.func(args)
    assert json.loads(kept.read_text(encoding="utf-8"))["n_candidates"] == 3


def test_an_explicit_disabled_competitor_is_skipped_with_a_warning(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    hidden = _model_hidden_skill(tmp_path, "ultra", "compress output hard")
    args = _parse("novelty", "routing", "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", hidden, _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(tmp_path / "r.json"))
    args.func(args)
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["n_candidates"] == 2
    assert f"skipped competitor {hidden}: disable-model-invocation" in capsys.readouterr().err


def test_include_disabled_keeps_a_disabled_skill_named_as_a_competitor(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    hidden = _model_hidden_skill(tmp_path, "ultra", "compress output hard")
    out = tmp_path / "r.json"
    args = _parse("novelty", "routing", "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", hidden, _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(out), "--include-disabled")
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 3
    assert "skipped competitor" not in capsys.readouterr().err


def test_a_disabled_artifact_is_still_routed_but_warned_about(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    artifact = _model_hidden_skill(tmp_path, "ultra", "compress output hard")
    args = _parse("novelty", "routing", "--artifact", artifact,
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(tmp_path / "r.json"))
    args.func(args)
    assert "the model cannot select it" in capsys.readouterr().err


@pytest.mark.parametrize("line, hidden", [
    ("disable-model-invocation: true", True), ("disable-model-invocation: false", False),
    ("disable-model-invocation: yes", True), ("description: x", False),
    # Unquoted yes/on parse to True; the quoted and numeric spellings reach the string branch.
    ("disable-model-invocation: 'yes'", True), ("disable-model-invocation: 'on'", True),
    ("disable-model-invocation: 1", True), ("disable-model-invocation: 0", False),
    ("disable-model-invocation: 'TRUE'", True), ("disable-model-invocation: 'no'", False),
])
def test_the_disable_flag_is_read_the_way_the_repo_budget_reads_it(tmp_path, line, hidden):
    path = tmp_path / "SKILL.md"
    path.write_text(f"---\nname: x\ndescription: x\n{line}\n---\n\nbody\n", encoding="utf-8")
    assert disables_model_invocation(path) is hidden


def test_a_mirror_of_a_catalog_skill_is_counted_once(tmp_path, capsys):
    # A plugin copy of the skills/ tree: byte-identical SKILL.md, same name. It used to make
    # the field name db twice and refuse the run.
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "db", "tune a slow database query")
    mirror = root / "plugins" / "x" / "skills" / "db"
    mirror.mkdir(parents=True)
    (mirror / "SKILL.md").write_bytes((root / "skills" / "db" / "SKILL.md").read_bytes())
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", f"skill:{root / 'skills' / 'perf'}",
                  "--catalog", str(root), "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 2
    err = capsys.readouterr().err
    assert "identical to" in err and "skills" in err


def test_the_mirror_kept_is_the_copy_nearest_the_catalog_root(tmp_path):
    root = tmp_path / "catalog"
    _catalog_skill(root, "db", "tune a slow database query")
    deep = root / "plugins" / "x" / "skills" / "db"
    deep.mkdir(parents=True)
    (deep / "SKILL.md").write_bytes((root / "skills" / "db" / "SKILL.md").read_bytes())
    notes: list[str] = []
    assert [c.name for c in catalog_competitors(str(root), set(), notes)] == ["db"]
    assert "plugins" in notes[0] and "identical to" in notes[0]
    # The note names the copy that was kept, not only the one left out.
    assert notes[0].endswith(f"identical to {root / 'skills' / 'db' / 'SKILL.md'}")


def test_mirrors_at_one_depth_keep_the_path_that_sorts_first(tmp_path):
    root = tmp_path / "catalog"
    for branch in ("b", "a"):
        copy = root / branch / "skills" / "db"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(
            "---\nname: db\ndescription: tune a slow database query\n---\n\n# B\n",
            encoding="utf-8")
    notes: list[str] = []
    assert [c.name for c in catalog_competitors(str(root), set(), notes)] == ["db"]
    assert notes[0].endswith(f"identical to {root / 'a' / 'skills' / 'db' / 'SKILL.md'}")
    assert f"at {root / 'b' / 'skills' / 'db' / 'SKILL.md'}" in notes[0]


def test_two_catalog_skills_with_one_name_and_different_content_are_refused_by_name(
    tmp_path, capsys
):
    # Not "pass each competitor once": the catalog holds two different cards under one name,
    # and the refusal names both copies rather than the duplicate-competitor check.
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "db", "tune a slow database query")
    fork = root / "plugins" / "x" / "skills" / "db"
    fork.mkdir(parents=True)
    (fork / "SKILL.md").write_text(
        "---\nname: db\ndescription: a different card\n---\n\n# B\n", encoding="utf-8"
    )
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse(
        "novelty",
        "routing",
        "--artifact",
        f"skill:{root / 'skills' / 'perf'}",
        "--catalog",
        str(root),
        "--queries",
        str(queries),
        "--out",
        str(out),
    )
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    message = str(refused.value)
    assert message.startswith("catalog has db at ") and "dedupe or rename" in message
    assert str(root / "skills" / "db" / "SKILL.md") in message
    assert str(fork / "SKILL.md") in message
    assert "more than once" not in message and not out.exists()


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
    # hub's own queries are not sibling queries of hub: only the other golds count.
    assert all(row["skill"] != "perf" or row["sibling_queries"] == 3
               for row in lexical["attractors"])


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
    import dokimi.novelty.routing as routing

    seen = []
    real = routing.model_routing

    def spy(candidates, queries, **kwargs):
        seen.append((kwargs["gold"], kwargs["seed"]))
        return real(candidates, queries, **kwargs)

    monkeypatch.setattr(routing, "model_routing", spy)
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
    with pytest.raises(SystemExit, match="every model call failed"):
        args.func(args)
    captured = capsys.readouterr()
    assert "OK Saved" not in captured.out
    assert "2 model call(s) failed" in captured.err and "boom" in captured.err


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

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`catalog routing-compare`: two saved all-vs-all runs, query by query (#710)."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.all_vs_all import all_vs_all
from dokimi.novelty.client import LLMClient
from dokimi.novelty.routing import Candidate, RoutingInputError
from dokimi.novelty.routing_compare import compare_runs, load_run
from dokimi.utils import InputError


def _row(query, pick, gold="g", **extra):
    if pick is None:
        return {"query": query, "gold": gold, "rank": None, "reason": "no content words", **extra}
    return {"query": query, "gold": gold, "rank": 1, "picked": pick, "scores": {}, **extra}


def _model_row(query, picks, gold="g", **extra):
    return {"query": query, "gold": gold, "picks": picks,
            "modal_pick": picks[0] if picks else None, **extra}


def _save(tmp_path, name, rows=None, model_rows=None, mode="all_vs_all"):
    result: dict = {}
    if rows is not None:
        result["lexical"] = {"per_query": rows}
    if model_rows is not None:
        result["model"] = {"model": "m1", "noise": {"per_query": model_rows}}
    path = tmp_path / name
    path.write_text(json.dumps({"mode": mode, "result": result}), encoding="utf-8")
    return str(path)


def _compare(tmp_path, base_rows, other_rows, arm="lexical"):
    key = "rows" if arm == "lexical" else "model_rows"
    base = _save(tmp_path, "base.json", **{key: base_rows})
    other = _save(tmp_path, "other.json", **{key: other_rows})
    return compare_runs(load_run(base, arm), load_run(other, arm), arm)


def _ten(base_only, other_only):
    """10 queries: ``base_only`` right in BASE only, ``other_only`` in OTHER only, rest both."""
    base, other = [], []
    for i in range(10):
        q = f"query {i}"
        wrong_in_other = i < base_only
        wrong_in_base = base_only <= i < base_only + other_only
        base.append(_row(q, "x" if wrong_in_base else "g"))
        other.append(_row(q, "x" if wrong_in_other else "g"))
    return base, other


@pytest.mark.parametrize("base_only, other_only, p", [(5, 0, 0.0625), (6, 0, 0.03125),
                                                       (3, 2, 1.0), (0, 0, 1.0)])
def test_the_sign_test_gives_the_known_exact_p_over_the_discordant_pairs(
        tmp_path, base_only, other_only, p):
    result = _compare(tmp_path, *_ten(base_only, other_only))
    assert result["n_paired"] == 10
    assert result["discordant"] == {"only_base": base_only, "only_other": other_only,
                                    "n_discordant": base_only + other_only}
    assert result["p_value"] == p


def test_each_run_counts_what_it_got_right_and_the_changed_picks_are_in_a_stable_order(tmp_path):
    base = [_row("b q", "g", gold="z"), _row("a q", "x", gold="a"), _row("same", "y")]
    other = [_row("b q", "y", gold="z"), _row("a q", "a", gold="a"), _row("same", "y")]
    result = _compare(tmp_path, base, other)
    assert result["n_correct"] == {"base": 0, "other": 1}
    assert result["changed"] == [{"gold": "a", "query": "a q", "base": "x", "other": "a"},
                                 {"gold": "z", "query": "b q", "base": "g", "other": "y"}]


def test_a_query_in_one_run_only_is_counted_listed_and_left_out_of_every_figure(tmp_path):
    base = [_row("both", "g"), _row("only base", "g"), _row("only base 2", "x")]
    other = [_row("both", "x"), _row("only other", "g")]
    result = _compare(tmp_path, base, other)
    assert result["n_paired"] == 1
    assert result["discordant"]["only_base"] == 1
    assert result["only_in_base"] == {"count": 2, "examples": [
        {"gold": "g", "query": "only base"}, {"gold": "g", "query": "only base 2"}]}
    assert result["only_in_other"]["count"] == 1


def test_the_same_query_under_two_golds_is_two_pairs(tmp_path):
    base = [_row("shared", "a", gold="a"), _row("shared", "a", gold="b")]
    other = [_row("shared", "a", gold="a"), _row("shared", "b", gold="b")]
    result = _compare(tmp_path, base, other)
    assert result["n_paired"] == 2 and result["n_correct"] == {"base": 1, "other": 2}


def test_a_request_listed_twice_for_one_gold_pairs_in_order(tmp_path):
    base = [_row("dup", "g"), _row("dup", "x")]
    other = [_row("dup", "x"), _row("dup", "g")]
    result = _compare(tmp_path, base, other)
    assert result["n_paired"] == 2
    assert result["discordant"] == {"only_base": 1, "only_other": 1, "n_discordant": 2}


def test_a_row_with_no_content_word_in_either_run_is_left_out_and_counted(tmp_path):
    base = [_row("the and", None), _row("real", "g"), _row("odd", "g")]
    other = [_row("the and", None), _row("real", "g"), _row("odd", None)]
    result = _compare(tmp_path, base, other)
    assert result["n_paired"] == 1
    assert result["unscored"] == {"no_content_word": 2}


def test_the_model_arm_uses_the_unique_modal_pick_and_leaves_a_tie_unscored(tmp_path):
    base = [_model_row("q1", ["g", "g", "x"]), _model_row("q2", ["g", "x"]),
            _model_row("q3", ["x", "x"]), _model_row("q4", [])]
    other = [_model_row("q1", ["x", "x", "g"]), _model_row("q2", ["g"]),
             _model_row("q3", ["g", "g"]), _model_row("q4", ["g"])]
    result = _compare(tmp_path, base, other, arm="model")
    assert result["n_paired"] == 2
    assert result["n_correct"] == {"base": 1, "other": 1}
    assert result["discordant"] == {"only_base": 1, "only_other": 1, "n_discordant": 2}
    assert result["unscored"] == {"no_answered_call": 1, "no_unique_modal_pick": 1}


def test_the_model_arm_is_refused_when_either_run_has_none(tmp_path):
    base = _save(tmp_path, "base.json", rows=[_row("q", "g")], model_rows=[_model_row("q", ["g"])])
    other = _save(tmp_path, "other.json", rows=[_row("q", "g")])
    with pytest.raises(RoutingInputError, match="other.json: has no model arm"):
        load_run(other, "model")
    assert load_run(base, "model")["model"] == "m1"


def test_alternatives_both_runs_name_alike_count_and_the_report_says_so(tmp_path):
    base = [_row("q1", "h", acceptable=["h"]), _row("q2", "x", acceptable=["h"])]
    other = [_row("q1", "g", acceptable=["h"]), _row("q2", "h", acceptable=["h"])]
    result = _compare(tmp_path, base, other)
    assert result["scoring"] == "gold_or_acceptable" and result["acceptable_pairs"] == 2
    assert result["n_correct"] == {"base": 1, "other": 2}
    assert result["n_correct_gold"] == {"base": 0, "other": 1}
    assert result["acceptable_mismatch"] == 0


def test_alternatives_in_one_run_only_or_different_ones_score_on_the_gold_alone(tmp_path, capsys):
    from dokimi.commands.routing_compare import print_comparison

    base = [_row("q1", "h", acceptable=["h"]), _row("q2", "h", acceptable=["h"])]
    other = [_row("q1", "g"), _row("q2", "h", acceptable=["h", "k"])]
    result = _compare(tmp_path, base, other)
    assert result["scoring"] == "gold" and result["acceptable_mismatch"] == 2
    assert result["n_correct"] == {"base": 0, "other": 1}
    print_comparison(result)
    assert "2 paired queries have acceptable alternatives in one run only" in capsys.readouterr().out


@pytest.mark.parametrize("text, message", [
    ("{not json", "base.json: not JSON"),
    ('["a list"]', "base.json: not an all-vs-all routing result"),
    ('{"mode": "single"}', "base.json: not an all-vs-all routing result"),
    ('{"mode": "all_vs_all", "result": {"lexical": {"attractors": []}}}',
     "base.json: no `lexical.per_query` rows: saved before"),
    ('{"mode": "all_vs_all", "result": {"lexical": {"per_query": [{"query": 1}]}}}',
     "base.json: a per_query row"),
])
def test_a_file_that_is_not_a_saved_all_vs_all_run_is_refused_naming_file_and_cause(
        tmp_path, text, message):
    path = tmp_path / "base.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RoutingInputError, match=message):
        load_run(str(path), "lexical")


def test_a_missing_file_is_refused_by_name(tmp_path):
    with pytest.raises(RoutingInputError, match="nope.json: cannot read"):
        load_run(str(tmp_path / "nope.json"), "lexical")


def _cli(tmp_path, *argv):
    args = build_parser().parse_args(["catalog", "routing-compare", *argv])
    args.func(args)


def test_the_command_refuses_two_runs_with_no_pair_in_common(tmp_path):
    base = _save(tmp_path, "base.json", rows=[_row("one", "g")])
    other = _save(tmp_path, "other.json", rows=[_row("two", "g")])
    with pytest.raises(InputError, match="share no .gold, query. pair"):
        _cli(tmp_path, base, other)


def test_the_command_turns_a_bad_file_into_an_input_error(tmp_path):
    base = _save(tmp_path, "base.json", rows=[_row("one", "g")], mode="single")
    with pytest.raises(InputError, match="base.json: not an all-vs-all"):
        _cli(tmp_path, base, base)


FIELD = [Candidate("linux-perf", "Profile a slow Linux binary with perf"),
         Candidate("db-tuner", "Tune a slow database query with an index"),
         Candidate("hub", "slow programs and slow queries advice")]
QUERIES = {"linux-perf": ["profile my binary with perf", "my slow program needs advice",
                          "profile a slow program"],
           "db-tuner": ["tune a database query"]}


def _stub(pick_name):
    def transport(prompt: str, system: str | None):
        lines = prompt.split("TOOLS:", 1)[1].strip().splitlines()
        return json.dumps({"pick": next(i + 1 for i, line in enumerate(lines)
                                        if pick_name in line)}), None
    return LLMClient(model="stub", role="selector", transport=transport)


def _write_real(tmp_path, name, field, client=None, acceptable=None):
    path = tmp_path / name
    path.write_text(json.dumps({"mode": "all_vs_all", "result": all_vs_all(
        field, QUERIES, client=client, acceptable=acceptable)}), encoding="utf-8")
    return str(path)


def test_two_runs_the_real_all_vs_all_wrote_compare_end_to_end(tmp_path, capsys):
    better = [Candidate("linux-perf", "My slow program needs advice: profile a slow Linux binary "
                                      "with perf"), FIELD[1], Candidate("hub", "unrelated words")]
    base = _write_real(tmp_path, "base.json", FIELD, client=_stub("hub"))
    other = _write_real(tmp_path, "other.json", better, client=_stub("linux-perf"))
    out = tmp_path / "cmp" / "out.json"
    _cli(tmp_path, base, other, "--out", str(out))
    printed = capsys.readouterr().out
    assert "4 paired queries scored on gold" in printed
    assert "BASE  3 of 4 right" in printed and "OTHER 4 of 4 right" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["mode"] == "routing_compare" and saved["arm"] == "lexical"
    assert saved["discordant"] == {"only_base": 0, "only_other": 1, "n_discordant": 1}
    assert saved["p_value"] == 1.0
    _cli(tmp_path, base, other, "--arm", "model", "--out", str(out))
    model = json.loads(out.read_text(encoding="utf-8"))
    assert model["arm"] == "model" and model["n_correct"] == {"base": 0, "other": 3}
    assert model["discordant"]["only_other"] == 3
    assert model["p_value"] == 0.25


def test_the_command_refuses_the_model_arm_for_a_run_made_without_a_model(tmp_path):
    base = _write_real(tmp_path, "base.json", FIELD)
    with pytest.raises(InputError, match="base.json: has no model arm"):
        _cli(tmp_path, base, base, "--arm", "model")


def test_the_command_scores_alternatives_both_real_runs_carry(tmp_path, capsys):
    acceptable = {"linux-perf": {"my slow program needs advice": ["hub"]}}
    base = _write_real(tmp_path, "base.json", FIELD, acceptable=acceptable)
    other = _write_real(tmp_path, "other.json", FIELD, acceptable=acceptable)
    _cli(tmp_path, base, other)
    printed = capsys.readouterr().out
    assert "scored on gold or acceptable" in printed
    assert "p = 1" in printed

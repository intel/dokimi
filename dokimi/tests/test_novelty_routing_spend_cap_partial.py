# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty routing --max-usd`: a run stopped by the cap keeps what it paid for."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.client import SpendCapReached


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")
    return f"skill:{root}"


def _provider(monkeypatch, usage):
    """Every selector call answers card 1 and reports ``usage``; returns the call log."""
    from dokimi import llm

    calls: list[str] = []

    def stub(prompt, model, **_kwargs):
        calls.append(prompt)
        return '{"pick": 1}', usage

    monkeypatch.setattr(llm, "llm_call_with_usage", stub)
    return calls


def _single(tmp_path, queries, *extra):
    q = tmp_path / "q.json"
    q.write_text(json.dumps(queries), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(q), "--model", "m", "--out", str(out), *extra)
    return args, out


def _again(tmp_path, out, name, *extra):
    """A second run of ``_single`` writing to the same ``out``."""
    sub = tmp_path / name
    sub.mkdir()
    args, _ = _single(sub, QUERIES, *extra)
    args.out = str(out)
    return args


QUERIES = ["profile a binary", "tune a slow query", "record a perf profile"]


def test_a_single_run_stopped_by_the_cap_writes_what_it_scored(tmp_path, monkeypatch, capsys):
    calls = _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, [*QUERIES, "annotate the hot loop"], "--max-usd", "1")
    with pytest.raises(SystemExit, match=r"\$1\.20 of \$1\.00") as stop:
        args.func(args)
    assert str(out) in str(stop.value)
    assert len(calls) == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["partial"]["reason"] == "spend_cap"
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (2, 4)
    assert payload["model"]["n_calls"] == 2 and len(payload["model"]["per_query"]) == 2
    assert payload["cost"]["llm_calls"] == 2
    assert "lexical" in payload
    shown = capsys.readouterr().out
    assert "cost     2 calls" in shown and "$1.2000" in shown
    assert "OK Saved" not in shown


def test_a_stopped_repeat_run_keeps_the_noise_of_the_queries_it_finished(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.4))
    args, out = _single(tmp_path, QUERIES, "--repeats", "2", "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (3, 6)
    assert len(payload["model"]["per_query"]) == 3          # the half-asked query is kept
    # ... but only the query asked in full enters the repeat noise.
    assert [row["query"] for row in payload["model"]["noise"]["per_query"]] == [QUERIES[0]]


def test_a_call_with_no_price_also_leaves_the_partial_result(tmp_path, monkeypatch):
    _provider(monkeypatch, None)
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["partial"]["reason"] == "spend_cap" and payload["partial"]["calls_done"] == 1
    # The reply came back and was paid for, so it is scored, not lost; no further call is made.
    row = payload["model"]["per_query"][0]
    assert "error" not in row and row["picked"] is not None
    assert len(payload["model"]["per_query"]) == 1


def test_a_run_the_cap_did_not_stop_has_no_partial_marker(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)
    assert "partial" not in json.loads(out.read_text(encoding="utf-8"))


def _catalog(tmp_path):
    for name, description in {"perf": "profile a slow binary with perf record and report",
                              "db": "tune a slow database query with an index"}.items():
        d = tmp_path / "catalog" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")
    q = tmp_path / "queries.json"
    q.write_text(json.dumps({"perf": ["profile a slow program", "find hot functions"],
                             "db": ["add an index"]}), encoding="utf-8")
    return tmp_path / "catalog", q


def test_an_all_vs_all_run_stopped_by_the_cap_scores_the_queries_it_finished(
        tmp_path, monkeypatch, capsys):
    calls = _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    root, queries = _catalog(tmp_path)
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out),
                  "--model", "m", "--max-usd", "1")
    with pytest.raises(SystemExit, match=r"\$1\.20 of \$1\.00") as stop:
        args.func(args)
    assert str(out) in str(stop.value)
    assert len(calls) == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["partial"]["reason"] == "spend_cap"
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (2, 3)
    model = payload["result"]["model"]
    assert sum(model["n_scored"].values()) == 2          # the two answered calls are scored
    assert sum(model["n_unscored"].values()) == 1        # the refused one is not a miss
    assert payload["cost"]["llm_calls"] == 2
    shown = capsys.readouterr().out
    assert "cost     2 calls" in shown and "OK Saved" not in shown
    # The summary of a finished run, marked partial: the field line, the attractors, the model line.
    assert "all-vs-all: 2 skills in the field, 3 queries" in shown
    assert "model    partial: 2 of 3 calls, 2 answered" in shown
    assert "WARNING: only 2 model calls answered" in shown


def test_an_all_vs_all_run_stopped_early_leaves_the_skills_it_never_reached_unscored(
        tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    root, queries = _catalog(tmp_path)
    extra = root / "skills" / "net"        # a third skill, so one is skipped, not just refused
    extra.mkdir(parents=True)
    (extra / "SKILL.md").write_text(
        "---\nname: net\ndescription: debug slow network sockets\n---\n\n# B\n", encoding="utf-8")
    by_skill = {**json.loads(queries.read_text()), "net": ["trace a socket", "dump packets"]}
    queries.write_text(json.dumps(by_skill), encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out),
                  "--model", "m", "--max-usd", "0.5")
    # The first call is allowed (nothing spent), then 0.6 >= 0.5 refuses the rest.
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]
    assert sum(model["n_scored"].values()) == 1
    unreached = [skill for skill, n in model["n_scored"].items() if n == 0]
    assert len(unreached) == 2             # the skill whose call was refused, and the one skipped
    for skill in unreached:
        assert model["own_rate"][skill] is None          # not a measured 0.0
        assert model["n_unscored"][skill] == len(by_skill[skill])


def test_a_stopped_run_is_marked_partial_and_a_finished_one_is_not(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "partial"
    sub = tmp_path / "all"
    sub.mkdir()
    root, queries = _catalog(sub)
    all_out = sub / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(all_out),
                  "--model", "m", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    assert json.loads(all_out.read_text(encoding="utf-8"))["status"] == "partial"
    finished = tmp_path / "fin"
    finished.mkdir()
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(finished, QUERIES, "--max-usd", "5")
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "ok"


def test_the_model_line_of_a_partial_run_says_how_many_calls_it_rests_on(
        tmp_path, monkeypatch, capsys):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    queries = [f"profile binary number {i}" for i in range(20)]
    args, _out = _single(tmp_path, queries, "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    lines = capsys.readouterr().out.splitlines()
    model = next(line for line in lines if line.startswith("model "))
    assert "partial: 1 of 20 calls" in model
    # One answered call is not a rate: the warning the lexical arm gets applies here too.
    assert any(line.startswith("WARNING: only 1 model call answered") for line in lines)


def test_a_partial_run_does_not_overwrite_a_finished_result(tmp_path, monkeypatch, capsys):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)
    finished = out.read_text(encoding="utf-8")
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    again = _again(tmp_path, out, "second", "--max-usd", "0.5")
    with pytest.raises(SystemExit) as stop:
        again.func(again)
    side = out.with_suffix(".partial.json")
    assert out.read_text(encoding="utf-8") == finished       # the finished result stays
    assert json.loads(side.read_text(encoding="utf-8"))["status"] == "partial"
    assert str(side) in str(stop.value)
    # A second stopped run replaces the partial file, not the finished one.
    again2 = _again(tmp_path, out, "third", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        again2.func(again2)
    assert out.read_text(encoding="utf-8") == finished


def test_a_partial_run_writes_over_an_earlier_partial_one(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    again = _again(tmp_path, out, "second", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        again.func(again)
    assert not out.with_suffix(".partial.json").exists()


def test_a_partial_run_skips_a_sidecar_that_holds_a_finished_result(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)                                          # a finished routing.json
    side = out.with_suffix(".partial.json")
    sidecar = _again(tmp_path, side, "sidecar", "--max-usd", "5")
    sidecar.func(sidecar)                                    # a finished routing.partial.json
    finished, finished_side = out.read_text(encoding="utf-8"), side.read_text(encoding="utf-8")
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    again = _again(tmp_path, out, "third", "--max-usd", "0.5")
    with pytest.raises(SystemExit) as stop:
        again.func(again)
    third = out.with_name("routing.partial2.json")
    assert out.read_text(encoding="utf-8") == finished        # the finished result stays
    assert side.read_text(encoding="utf-8") == finished_side  # and so does the finished sidecar
    assert json.loads(third.read_text(encoding="utf-8"))["status"] == "partial"
    assert str(third) in str(stop.value)


def test_a_single_run_capped_before_any_query_finished_has_an_empty_noise_block(tmp_path, monkeypatch):
    # noise is always an object (main's contract): with no query finished it names the repeats
    # asked for and measures nothing.
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--repeats", "3", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    noise = json.loads(out.read_text(encoding="utf-8"))["model"]["noise"]
    assert (noise["n_repeats"], noise["n_queries_compared"], noise["mean_agreement"]) == (3, 0, None)


def test_a_command_that_writes_no_partial_still_says_nothing_was_written():
    from dokimi.commands.novelty import _stops_at_cap

    def command(args):
        raise SpendCapReached("spend cap reached: $1.20 of $1.00 after 2 calls (next call: m)")

    with pytest.raises(SystemExit, match="nothing was written"):
        _stops_at_cap(command)(None)


def test_a_partial_run_skips_a_sidecar_it_cannot_read(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)                                          # a finished routing.json
    side = out.with_suffix(".partial.json")
    side.write_text("not json", encoding="utf-8")            # a sidecar this cannot read is no partial result
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    again = _again(tmp_path, out, "third", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        again.func(again)
    assert side.read_text(encoding="utf-8") == "not json"    # it is left as it is
    third = out.with_name("routing.partial2.json")
    assert json.loads(third.read_text(encoding="utf-8"))["status"] == "partial"


def test_a_partial_model_line_with_five_answered_calls_has_no_warning(capsys):
    from dokimi.commands.novelty_routing_report import print_all_vs_all_model

    print_all_vs_all_model({"partial": {"calls_done": 6, "calls_planned": 9},
                            "n_scored": {"a": 3, "b": 2}}, None)
    out = capsys.readouterr().out
    assert "partial: 6 of 9 calls, 5 answered" in out
    assert "WARNING" not in out


def test_the_blind_skill_warning_needs_a_lexical_arm(capsys):
    from dokimi.commands.novelty_routing_report import warn_blind_skills

    warn_blind_skills({}, {"a": ["q"]})
    assert capsys.readouterr().err == ""


def test_an_all_vs_all_repeat_run_stopped_mid_query_pins_its_counts(tmp_path, monkeypatch):
    # Skills db (1 query), net (2), perf (2); two repeats; 0.4 per call and a 1.00 cap. db runs
    # both its repeats, net's first query gets one of its two, and perf is never reached.
    _provider(monkeypatch, UsageRecord(cost_usd=0.4))
    root, queries = _catalog(tmp_path)
    extra = root / "skills" / "net"
    extra.mkdir(parents=True)
    (extra / "SKILL.md").write_text(
        "---\nname: net\ndescription: debug slow network sockets\n---\n\n# B\n", encoding="utf-8")
    by_skill = {**json.loads(queries.read_text()), "net": ["trace a socket", "dump packets"]}
    queries.write_text(json.dumps(by_skill), encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out),
                  "--model", "m", "--repeats", "2", "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (3, 10)
    model = payload["result"]["model"]
    assert model["n_unscored"]["net"] == 3             # 4 planned, 1 made: the refused repeats
    assert model["n_unscored"]["perf"] == 4            # never reached: 2 queries x 2 repeats
    noise = model["noise"]
    assert noise["n_queries_compared"] == 1            # only the query asked in full (db's)
    assert [row["query"] for row in noise["per_query"]] == ["add an index"]


def test_a_last_call_with_no_price_that_ends_the_run_leaves_no_partial_marker(tmp_path, monkeypatch):
    # One query, one call, no price: the run is complete, so it is not partial and does not exit.
    calls = _provider(monkeypatch, None)
    args, out = _single(tmp_path, QUERIES[:1], "--max-usd", "5")
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "partial" not in payload and payload["status"] == "ok"
    assert len(calls) == 1 and payload["model"]["n_calls"] == 1


def test_an_all_vs_all_last_call_with_no_price_leaves_no_partial_marker(tmp_path, monkeypatch):
    # Two skills, one query for db: the last planned call has no price, so the run is complete.
    calls = _provider(monkeypatch, None)
    root, queries = _catalog(tmp_path)
    queries.write_text(json.dumps({"db": ["add an index"]}), encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out),
                  "--model", "m", "--max-usd", "5")
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "partial" not in payload and payload.get("status") != "partial"
    assert len(calls) == 1


def test_an_unpriced_call_stops_the_later_repeats_and_skills_too(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, None)
    root, queries = _catalog(tmp_path)
    out = tmp_path / "o.json"
    args = _parse("novelty", "routing", "--all-vs-all", "--catalog", str(root),
                  "--queries-by-skill", str(queries), "--out", str(out),
                  "--model", "m", "--repeats", "3", "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    # The first unpriced reply stops the run: no repeat of that query and no later skill is asked.
    assert len(calls) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (1, 9)


def test_an_unpriced_call_stops_the_repeats_of_a_single_artifact_run(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, None)
    args, out = _single(tmp_path, QUERIES[:2], "--repeats", "3", "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    assert len(calls) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (1, 6)


def test_a_partial_single_run_rates_its_selections_over_the_calls_it_made(tmp_path, monkeypatch):
    # Three queries, the cap stops after two calls: the rate is over the two asked, not all three.
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["model"]
    hits = sum(1 for row in model["per_query"] if row.get("correct"))
    assert model["n_calls"] == 2
    assert model["selection_rate"] == round(hits / 2, 4)

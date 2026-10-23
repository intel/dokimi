# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tool-preference probe: item design, outcome classification, the summary's arithmetic, the CLI."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi import tool_preference as tp
from dokimi.artifacts import validate_artifact

GROUPS = Path(__file__).resolve().parent / "fixtures" / "tool_preference" / "groups.json"


@pytest.fixture
def doc():
    return tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8")))


def _by_id(items):
    return {i["id"]: i for i in items}


def _desc(item, role):
    name = item["twin_names"][role]
    return next(t["function"]["description"] for t in item["tools"] if t["function"]["name"] == name)


# ── groups document ───────────────────────────────────────────────────────
def test_invalid_groups_are_refused_before_anything_runs(doc):
    bad = copy.deepcopy(doc)
    del bad["groups"][0]["twins"]["B"]
    with pytest.raises(tp.GroupsError, match="B"):
        tp.load_groups(bad)
    dup = copy.deepcopy(doc)
    dup["groups"][1]["id"] = "G01"
    with pytest.raises(tp.GroupsError, match="duplicate ids"):
        tp.load_groups(dup)
    clash = copy.deepcopy(doc)
    clash["groups"][0]["distractors"][0]["name"] = "search_docs"
    with pytest.raises(tp.GroupsError, match="unique within a group"):
        tp.load_groups(clash)


# ── item design ───────────────────────────────────────────────────────────
def test_cells_are_crossed_and_missing_fields_skip_their_factor(doc):
    items = tp.build_items(doc)
    per_group = {g: sum(i["group_id"] == g for i in items) for g in ("G01", "G02", "N01")}
    # G01: 2 baselines + 5 factors x (A,B) x (first,last). G02: no vendor names, no exclusions,
    # only A has a detail. N01 (control): 2 baselines + 2 per factor it can express.
    assert per_group == {"G01": 22, "G02": 12, "N01": 6}
    assert {i["kind"] for i in items if i["group_id"] == "N01"} == {"control"}


def test_twins_sit_at_the_ends_and_only_the_treated_one_changes(doc):
    items = _by_id(tp.build_items(doc))
    base = items["G01-baseline-Afirst"]
    names = [t["function"]["name"] for t in base["tools"]]
    assert names == ["search_docs", "read_file", "send_email", "find_documentation"]
    assert [t["function"]["name"] for t in items["G01-baseline-Bfirst"]["tools"]][0] == "find_documentation"

    last = items["G01-assertive-A-last"]
    assert last["first_role"] == "B" and last["tools"][-1]["function"]["name"] == "search_docs"
    assert _desc(last, "A").endswith(tp.DEFAULT_CUES["assertive"])
    assert _desc(last, "B") == doc["groups"][0]["twins"]["B"]["description"]

    vend = items["G01-vendor_name-B-first"]
    assert vend["twin_names"] == {"A": "search_docs", "B": "docs_finder_pro"}
    assert "Example: search_docs" in _desc(items["G01-long_desc-A-first"], "A")
    assert _desc(items["G01-exclusion-B-last"], "B").endswith("searching source code.")


def test_cues_and_system_prompt_can_be_overridden(doc):
    doc["cues"] = {"assertive": "Use me."}
    doc["system"] = ""
    item = _by_id(tp.build_items(doc, ["assertive"]))["G01-assertive-A-first"]
    assert _desc(item, "A").endswith("Use me.")
    assert [m["role"] for m in item["messages"]] == ["user"]
    # the artifact stamps what was sent: the override, and the default for the cue left alone
    assert tp.settings(doc) == ("", {"assertive": "Use me.", "social_proof": tp.DEFAULT_CUES["social_proof"]})


# ── outcomes ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("called,outcome", [
    (["search_docs"], "A"),
    (["find_documentation", "read_file"], "B"),
    (["find_documentation", "search_docs"], "both"),
    (["read_file"], "distractor"),
    (["read_file", "functions.x"], "distractor"),
    (["functions.x"], "unmatched"),
    ([""], "unmatched"),
    (["search_docs", "functions.x"], "A"),
    ([], "none"),
])
def test_classify(doc, called, outcome):
    item = _by_id(tp.build_items(doc))["G01-baseline-Afirst"]
    assert tp.classify(item, called)["outcome"] == outcome


def test_vendor_names_map_back_to_their_role(doc):
    item = _by_id(tp.build_items(doc))["G01-vendor_name-A-first"]
    assert tp.classify(item, ["acme_docs_search"])["outcome"] == "A"
    # the base name is not on offer in a vendor_name item
    assert tp.classify(item, ["search_docs"])["outcome"] == "unmatched"


def test_unmatched_names_are_their_own_rate_and_count_as_over_calling(doc):
    items = tp.build_items(doc, ["assertive"])
    s = tp.summarize(items, tp.run_probe(items, _fake_model(lambda i: ["functions.x"])))
    assert s["pair_outcomes"]["unmatched"] == 1.0 and s["pair_outcomes"]["distractor"] == 0.0
    assert s["control_over_call"] == {"assertive": 1.0, "baseline": 1.0}


# ── summary ───────────────────────────────────────────────────────────────
def _fake_model(policy):
    """A caller that answers with policy(item) -> list of tool names."""
    return lambda item: {"called": policy(item), "text": "", "usage": {"prompt_tokens": 10,
                                                                       "completion_tokens": 2,
                                                                       "cost_usd": 0.001}}


def test_summary_reads_factor_effect_against_same_tool_same_position(doc):
    items = tp.build_items(doc, ["assertive"])

    def policy(item):
        if item["kind"] == "control":
            return []
        if item["factor"] == "assertive":
            return [item["twin_names"][item["treated_role"]]]       # always the treated twin
        return [item["twin_names"][item["first_role"]]]             # baseline: first-listed twin

    s = tp.summarize(items, tp.run_probe(items, _fake_model(policy), reps=2, concurrency=2))
    # treated first: 1 - 1 (baseline already picks the first) = 0; treated last: 1 - 0 = 1
    assert s["factor_effects"]["assertive"]["mean"] == 0.5
    assert s["factor_effects"]["assertive"]["n_groups"] == 2
    assert s["first_twin_share_baseline"]["mean"] == 1.0
    assert s["control_over_call"] == {"assertive": 0.0, "baseline": 0.0}
    assert s["same_choice_rate"] == 1.0 and s["modal_share"] == 1.0


def test_both_twins_count_half_and_controls_measure_over_calling(doc):
    items = tp.build_items(doc, ["social_proof"])

    def policy(item):
        if item["kind"] == "control":
            return [item["twin_names"]["A"]] if item["factor"] == "social_proof" else []
        return list(item["twin_names"].values())

    s = tp.summarize(items, tp.run_probe(items, _fake_model(policy)))
    assert s["first_twin_share_baseline"]["mean"] == 0.5
    assert s["factor_effects"]["social_proof"]["mean"] == 0.0
    assert s["pair_outcomes"]["both_twins"] == 1.0
    assert s["control_over_call"] == {"baseline": 0.0, "social_proof": 1.0}
    assert s["same_choice_rate"] is None          # one rep: nothing to compare


def test_a_failed_call_is_recorded_and_left_out(doc):
    items = tp.build_items(doc, ["assertive"])

    def flaky(item):
        if item["id"] == "G01-baseline-Afirst":
            raise RuntimeError("throttled")
        return {"called": [item["twin_names"]["A"]]}

    results = tp.run_probe(items, flaky, reps=1)
    bad = [r for r in results if r["error"]]
    assert len(bad) == 1 and bad[0]["error"] == "RuntimeError: throttled" and bad[0]["choice"] is None
    s = tp.summarize(items, results)
    assert s["n_errors"] == 1 and s["n_calls"] == len(items)


def test_payload_is_a_valid_artifact(doc):
    items = tp.build_items(doc)
    results = tp.run_probe(items, _fake_model(lambda i: [i["twin_names"]["B"]]), reps=2)
    data = tp.payload(doc, items, results, model="m", provider="openai", reps=2, factors=list(tp.FACTORS),
                      max_tokens=512)
    data["schema_version"] = "tool_preference.v1"
    validate_artifact("tool_preference", data)
    assert data["max_tokens"] == 512
    assert data["system"] == tp.DEFAULT_SYSTEM and data["cues"] == tp.DEFAULT_CUES
    assert {i["group_id"]: i["domain"] for i in data["items"]} == {"G01": "generic", "G02": None, "N01": None}
    assert data["usage"]["n_calls"] == 2 * len(items)
    assert data["usage"]["cost_usd"] == pytest.approx(0.002 * len(items))


# ── CLI ───────────────────────────────────────────────────────────────────
class _Fn:
    def __init__(self, name):
        self.name = name


class _Msg:
    def __init__(self, names):
        self.content = "" if names else "No tool needed."
        self.tool_calls = [type("TC", (), {"function": _Fn(n)})() for n in names]


class _Resp:
    def __init__(self, names):
        self.choices = [type("C", (), {"message": _Msg(names)})()]
        self.usage = type("U", (), {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})()


def test_cli_runs_and_writes_the_artifact(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    def main(argv):
        args = build_parser().parse_args(argv)
        args.func(args)

    sent = []

    def fake_chat(messages, model, provider="openai", tools=None, tool_choice=None, **kw):
        sent.append((tool_choice, len(tools)))
        return _Resp([] if "GiB" in messages[-1]["content"] else [tools[-1]["function"]["name"]])

    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    out = tmp_path / "pref.json"
    main(["tools", "preference", "--groups", str(GROUPS), "--model", "m", "--reps", "2",
          "--factors", "assertive,long_desc", "--out-json", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("tool_preference", data)
    assert data["reps"] == 2 and data["factors"] == ["assertive", "long_desc"]
    assert data["max_tokens"] == 2048
    assert len(data["results"]) == 2 * len(data["items"]) == len(sent)
    assert {c for c, _ in sent} == {"auto"}
    # the fake always takes the last-listed tool: the first-listed twin never wins
    assert data["summary"]["first_twin_share_baseline"]["mean"] == 0.0
    printed = capsys.readouterr().out
    assert "assertive" in printed and "-> " in printed


def test_cli_dry_run_calls_nothing_and_unknown_factor_exits(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    def main(argv):
        args = build_parser().parse_args(argv)
        args.func(args)

    monkeypatch.setattr(llm, "chat_completion", lambda *a, **k: pytest.fail("called the model"))
    items_out = tmp_path / "items.json"
    main(["tools", "preference", "--groups", str(GROUPS), "--dry-run", "--items-out", str(items_out)])
    assert "3 groups -> 40 items (34 pair, 6 control) x 3 reps = 120 calls" in capsys.readouterr().out
    assert len(json.loads(items_out.read_text(encoding="utf-8"))) == 40
    with pytest.raises(SystemExit, match="unknown"):
        main(["tools", "preference", "--groups", str(GROUPS), "--factors", "loudness", "--dry-run"])
    with pytest.raises(SystemExit, match="--model is required"):
        main(["tools", "preference", "--groups", str(GROUPS)])


def test_cli_exits_nonzero_when_every_call_failed(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    def boom(*a, **k):
        raise RuntimeError("auth failed")

    monkeypatch.setattr(llm, "chat_completion", boom)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "all 16 call(s) failed" in capsys.readouterr().err
    data = json.loads(out.read_text(encoding="utf-8"))  # the evidence is kept
    validate_artifact("tool_preference", data)
    assert data["summary"]["n_errors"] == data["summary"]["n_calls"] == 16


def test_repeated_factors_are_not_expanded_twice(doc, monkeypatch, tmp_path, capsys):
    from dokimi.cli import build_parser

    once = tp.build_items(doc, ["assertive"])
    twice = tp.build_items(doc, ["assertive", "assertive"])
    assert [i["id"] for i in twice] == [i["id"] for i in once]
    out = tmp_path / "items.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--dry-run",
                                      "--factors", "assertive,assertive", "--items-out", str(out)])
    args.func(args)
    assert f"{len(once)} items" in capsys.readouterr().out
    assert len(json.loads(out.read_text(encoding="utf-8"))) == len(once)


def test_reply_cut_before_any_tool_call_is_an_error_not_none(monkeypatch):
    import dokimi.llm as llm
    from dokimi.commands.tools import _first_turn_caller

    def cut(*a, **k):
        r = _Resp([])
        r.choices[0].finish_reason = "length"
        return r

    monkeypatch.setattr(llm, "chat_completion", cut)
    items = tp.build_items(tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8"))), ["assertive"])
    results = tp.run_probe(items, _first_turn_caller("m", "openai", 16), reps=1)
    assert {r["choice"] for r in results} == {None}
    assert all("max_tokens=16" in r["error"] for r in results)
    assert tp.summarize(items, results)["n_errors"] == len(items)


def test_reply_cut_after_a_tool_call_is_scored_and_flagged(monkeypatch):
    import dokimi.llm as llm
    from dokimi.commands.tools import _first_turn_caller

    def cut(messages, model, provider="openai", tools=None, tool_choice=None, **kw):
        r = _Resp([tools[0]["function"]["name"]])
        r.choices[0].finish_reason = "length"
        return r

    monkeypatch.setattr(llm, "chat_completion", cut)
    items = tp.build_items(tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8"))), ["assertive"])
    results = tp.run_probe(items, _first_turn_caller("m", "openai", 16), reps=1)
    assert all(r["error"] is None and r["truncated"] for r in results)
    s = tp.summarize(items, results)
    assert s["n_errors"] == 0 and s["n_truncated"] == len(items)
    assert s["first_twin_share_baseline"]["mean"] == 1.0


def test_cli_caller_parses_tool_calls_and_forwards_max_tokens(monkeypatch):
    import dokimi.llm as llm
    from dokimi.commands.tools import _first_turn_caller

    seen = []

    def fake(messages, model, provider="openai", tools=None, tool_choice=None, **kw):
        seen.append(kw.get("max_tokens"))
        names = [t["function"]["name"] for t in tools]
        return _Resp([names[0], names[-1]])

    monkeypatch.setattr(llm, "chat_completion", fake)
    items = tp.build_items(tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8"))), ["assertive"])
    pair = next(i for i in items if i["kind"] == "pair")
    out = _first_turn_caller("m", "openai", 77)(pair)
    assert out["called"] == [pair["tools"][0]["function"]["name"], pair["tools"][-1]["function"]["name"]]
    assert tp.classify(pair, out["called"])["outcome"] == "both"
    assert out["truncated"] is False
    assert seen == [77]


def test_cli_caller_keeps_an_empty_tool_name(monkeypatch):
    import dokimi.llm as llm
    from dokimi.commands.tools import _first_turn_caller

    monkeypatch.setattr(llm, "chat_completion", lambda *a, **k: _Resp([""]))
    items = tp.build_items(tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8"))), ["assertive"])
    out = _first_turn_caller("m", "openai", 77)(items[0])
    assert out["called"] == [""]
    assert tp.classify(items[0], out["called"])["outcome"] == "unmatched"


def test_cli_caller_attaches_usage_to_exception_from_response_parsing(monkeypatch):
    import dokimi.llm as llm
    from dokimi.commands.tools import _first_turn_caller

    def fail_after_response(*a, **k):
        # Return a response with empty choices: this will fail during parsing
        r = _Resp([])
        r.choices = []  # no choices, will fail when accessing choices[0]
        return r

    monkeypatch.setattr(llm, "chat_completion", fail_after_response)
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: 0.01)
    items = tp.build_items(tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8"))), ["assertive"])
    # This should fail with usage attached to the exception
    caller = _first_turn_caller("m", "openai", 77)
    with pytest.raises(IndexError) as info:
        caller(items[0])
    usage = getattr(info.value, "usage", None)
    assert isinstance(usage, dict) and usage["cost_usd"] == 0.01


# ── cost cap ──────────────────────────────────────────────────────────────
def test_cost_cap_stops_new_calls_and_leaves_them_out(doc):
    items = tp.build_items(doc, ["assertive"])
    calls = []

    def priced(item):
        calls.append(item["id"])
        return {"called": [item["twin_names"]["A"]], "usage": {"cost_usd": 0.001}}

    # one at a time: the 4th call brings the spend to the cap, so the 5th is never started
    results = tp.run_probe(items, priced, reps=1, concurrency=1, max_cost_usd=0.0035)
    assert len(calls) == 4 and [r["item_id"] for r in results[:4]] == calls
    assert all(r["skipped"] and r["choice"] is None and r["error"] is None for r in results[4:])
    s = tp.summarize(items, results)
    assert s["n_skipped"] == len(items) - 4 and s["n_errors"] == 0
    # the 4 run calls are G01's two baselines and two assertive items: one group reached
    assert s["first_twin_share_baseline"]["n_groups"] == 1


def test_cost_cap_with_concurrency_stays_within_bound(doc):
    items = tp.build_items(doc, ["assertive"])
    gate = threading.Barrier(4)
    ran = []

    def priced(item):
        ran.append(item["id"])
        if len(ran) <= 4:
            gate.wait(timeout=5)  # four calls in flight together
        return {"called": [item["twin_names"]["A"]], "usage": {"cost_usd": 0.001}}

    results = tp.run_probe(items, priced, reps=1, concurrency=4, max_cost_usd=0.0035)
    # the crossing call plus at most concurrency - 1 = 3 others in flight
    assert sum(not r["skipped"] for r in results) == len(ran)
    assert 4 <= len(ran) <= 7
    assert not any(r.get("error") for r in results)


def test_an_unpriced_call_blinds_the_cap_so_the_run_stops(doc):
    items = tp.build_items(doc, ["assertive"])
    results = tp.run_probe(items, lambda i: {"called": [], "usage": {"cost_usd": None}},
                           reps=1, concurrency=1, max_cost_usd=100.0)
    assert [r["skipped"] for r in results] == [False] + [True] * (len(items) - 1)


def test_no_cap_runs_unpriced_calls_to_the_end(doc):
    items = tp.build_items(doc, ["assertive"])
    results = tp.run_probe(items, lambda i: {"called": [], "usage": {"cost_usd": None}}, reps=1, concurrency=1)
    assert not any(r["skipped"] for r in results)


def test_a_failed_but_billed_call_counts_toward_the_cap(doc):
    items = tp.build_items(doc, ["assertive"])
    calls = []

    def cut(item):
        calls.append(item["id"])
        exc = RuntimeError("cut")
        exc.usage = {"cost_usd": 0.5}  # type: ignore[attr-defined]
        raise exc

    results = tp.run_probe(items, cut, reps=1, concurrency=1, max_cost_usd=0.75)
    assert len(calls) == 2
    assert [r["usage"] for r in results[:2]] == [{"cost_usd": 0.5}] * 2
    assert all(r["skipped"] for r in results[2:])


def test_classify_raises_after_billed_call_counts_toward_cap(doc, monkeypatch):
    items = tp.build_items(doc, ["assertive"])
    def call_then_classify_fails(item):
        return {"called": [item["tools"][0]["function"]["name"]], "usage": {"cost_usd": 0.01}, "text": ""}

    original_classify = tp.classify
    call_count = [0]
    def failing_classify(item, called):
        call_count[0] += 1
        if call_count[0] == 2:
            # Second call raises
            raise ValueError("classify error")
        return original_classify(item, called)

    monkeypatch.setattr(tp, "classify", failing_classify)
    results = tp.run_probe(items, call_then_classify_fails, reps=1, concurrency=1, max_cost_usd=0.02)
    # First call: success, costs 0.01, skipped=False
    assert results[0]["skipped"] is False and results[0]["error"] is None
    assert results[0]["usage"]["cost_usd"] == 0.01
    # Second call: classify raises, but usage is still captured from the call
    assert results[1]["skipped"] is False and results[1]["error"] is not None
    assert results[1]["usage"]["cost_usd"] == 0.01
    # Total spent: 0.02, so remaining calls should be skipped
    assert all(r["skipped"] for r in results[2:])


@pytest.mark.parametrize("reply", [
    {"called": []},                                   # no usage at all
    {"called": [], "usage": {}},                      # usage without a cost
    {"called": [], "usage": "n/a"},                   # usage that is not a dict
    {"called": [], "usage": {"cost_usd": "0.01"}},    # a cost that is not a number
    {"called": [], "usage": {"cost_usd": float("nan")}},
    {"called": [], "usage": {"cost_usd": True}},
])
def test_a_reply_without_a_usable_price_blinds_the_cap(doc, reply):
    items = tp.build_items(doc, ["assertive"])
    # a cap no valid price would reach: only a blind cap stops the run after one call
    results = tp.run_probe(items, lambda i: reply, reps=1, concurrency=1, max_cost_usd=100.0)
    assert [r["skipped"] for r in results] == [False] + [True] * (len(items) - 1)
    assert results[0]["error"] is None


def test_a_billed_failure_without_a_usable_price_blinds_the_cap(doc):
    items = tp.build_items(doc, ["assertive"])

    def cut(item):
        exc = RuntimeError("cut")
        exc.usage = {"cost_usd": "0.01"}  # type: ignore[attr-defined]
        raise exc

    results = tp.run_probe(items, cut, reps=1, concurrency=1, max_cost_usd=100.0)
    assert [r["skipped"] for r in results] == [False] + [True] * (len(items) - 1)


def test_a_failure_with_no_usage_is_not_billed_and_does_not_blind_the_cap(doc):
    items = tp.build_items(doc, ["assertive"])

    def refused(item):
        raise ConnectionError("refused before any reply")

    results = tp.run_probe(items, refused, reps=1, concurrency=1, max_cost_usd=0.01)
    assert not any(r["skipped"] for r in results)
    assert all(r["error"] for r in results)


class _HoldOneDecision:
    """A lock that holds the first thread to reach its second job back for a moment.

    Every job takes the lock twice, to start and to count its cost, so a thread's third
    entry is the start of its second job. A later job must not run while it waits.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._flag = threading.Lock()
        self._local = threading.local()
        self._held = False

    def __enter__(self):
        self._local.n = getattr(self._local, "n", 0) + 1
        with self._flag:
            hold = self._local.n == 3 and not self._held
            self._held = self._held or hold
        if hold:
            time.sleep(0.3)
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)


def test_the_calls_that_ran_are_a_prefix_when_a_worker_is_slow_to_start(doc, monkeypatch):
    items = tp.build_items(doc, ["assertive"])
    monkeypatch.setattr(tp, "threading", SimpleNamespace(Lock=_HoldOneDecision))
    results = tp.run_probe(items, lambda i: {"called": [], "usage": {"cost_usd": 0.01}},
                           reps=1, concurrency=2, max_cost_usd=0.05)
    ran = "".join("." if r["skipped"] else "R" for r in results)
    assert ran == ran.count("R") * "R" + ran.count(".") * ".", ran
    assert 5 <= ran.count("R") <= 6


def test_the_overshoot_stays_within_concurrency_minus_one(doc):
    items = tp.build_items(doc, ["assertive"])
    gate = threading.Barrier(4)
    ran = []

    def priced(item):
        ran.append(item["id"])
        if len(ran) <= 4:
            gate.wait(timeout=5)  # four calls in flight together
        return {"called": [], "usage": {"cost_usd": 0.001}}

    tp.run_probe(items, priced, reps=1, concurrency=4, max_cost_usd=0.0035)
    # the 4th call crosses the cap; at most the 3 others in flight run past it
    assert 4 <= len(ran) <= 4 + 3


def test_cli_cost_cap_keeps_the_partial_artifact_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: 0.01)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "0.02", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "--max-cost-usd 0.02 reached; 14 of 16 call(s) not run" in capsys.readouterr().err
    data = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("tool_preference", data)
    assert data["max_cost_usd"] == 0.02 and data["summary"]["n_skipped"] == 14


def test_cli_cut_reply_is_billed_and_stops_the_run(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    def cut(*a, **k):
        r = _Resp([])
        r.choices[0].finish_reason = "length"
        return r

    monkeypatch.setattr(llm, "chat_completion", cut)
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: 0.01)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "0.02", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "reached; 14 of 16 call(s) not run" in capsys.readouterr().err
    data = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("tool_preference", data)
    assert data["usage"]["cost_usd"] == 0.02 and data["summary"]["n_errors"] == 2


def test_cli_unpriced_model_stops_and_says_why(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: None)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "5", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "no price, so --max-cost-usd cannot be enforced; 15 of 16 call(s) not run" in err
    assert "reached" not in err


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf"])
def test_cli_cost_cap_must_be_positive(bad):
    from dokimi.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--max-cost-usd", bad])


def test_cli_no_warning_when_skipped_by_cost_cap(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    # With cap reached (skipped calls), no warning should be printed
    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: 0.01)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "0.02", "--out-json", str(out)])
    with pytest.raises(SystemExit):
        args.func(args)
    err = capsys.readouterr().err
    assert "reached" in err and "Warning:" not in err


def test_cli_no_warning_when_unpriced_skips_calls(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    # Unpriced model with cap set: first call blinds cap, rest skip, no warning
    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: None)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "100", "--out-json", str(out)])
    with pytest.raises(SystemExit):
        args.func(args)
    err = capsys.readouterr().err
    assert "cannot be enforced" in err and "Warning:" not in err


def test_cli_warning_when_last_call_unpriced_no_skipped(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    # Only the last call is unpriced; all 16 run, no skipped, warning should appear
    call_count = [0]
    def chat_with_last_unpriced(messages, model, tools=None, **k):
        call_count[0] += 1
        return _Resp([tools[0]["function"]["name"]])

    def cost_except_last(*a, **k):
        if call_count[0] == 16:
            return None  # last call is unpriced
        return 0.0001  # all others are priced

    monkeypatch.setattr(llm, "chat_completion", chat_with_last_unpriced)
    monkeypatch.setattr(llm, "_safe_completion_cost", cost_except_last)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "100", "--out-json", str(out)])
    args.func(args)
    err = capsys.readouterr().err
    assert "Warning: 1 call(s) had no price" in err


def test_an_unpriced_cut_reply_stops_the_run(doc):
    items = tp.build_items(doc, ["assertive"])
    def cut_no_price(item):
        exc = RuntimeError("cut at max_tokens")
        exc.usage = {"cost_usd": None}  # type: ignore[attr-defined]
        raise exc
    results = tp.run_probe(items, cut_no_price, reps=1, concurrency=1, max_cost_usd=100.0)
    assert results[0]["skipped"] is False and results[0]["error"] is not None
    assert results[0]["usage"] == {"cost_usd": None}
    assert all(r["skipped"] for r in results[1:])


def test_non_dict_exc_usage_does_not_crash_payload(doc):
    items = tp.build_items(doc, ["assertive"])
    def bad_usage(item):
        exc = RuntimeError("error")
        exc.usage = "n/a"  # type: ignore[attr-defined]
        raise exc
    results = tp.run_probe(items, bad_usage, reps=1, concurrency=1)
    assert results[0]["skipped"] is False and results[0]["error"] is not None
    assert results[0]["usage"] is None  # non-dict usage converted to None
    payload_doc = tp.payload(doc, items, results, model="test", provider="test", reps=1, factors=[], max_tokens=2048)
    assert payload_doc["usage"]["n_calls"] == 0  # no valid dict usage


def test_payload_counts_only_dict_usages_with_a_number_as_priced(doc):
    items = tp.build_items(doc, ["assertive"])
    base = {"item_id": items[0]["id"], "rep": 0, "choice": None, "text_preview": "", "truncated": False,
            "skipped": False, "error": "RuntimeError: cut"}
    results = [{**base, "usage": "n/a"}, {**base, "usage": {"cost_usd": "0.01"}},
               {**base, "usage": {"cost_usd": 0.02}}]
    usage = tp.payload(doc, items, results, model="test", provider="test", reps=1, factors=[],
                       max_tokens=2048)["usage"]
    assert usage["n_calls"] == 2
    assert usage["cost_known_calls"] == 1 and usage["cost_usd"] == 0.02


def test_non_dict_success_usage_does_not_crash_payload(doc):
    items = tp.build_items(doc, ["assertive"])
    def bad_success_usage(item):
        return {"called": [item["tools"][0]["function"]["name"]], "usage": "n/a"}  # type: ignore[dict-item]
    results = tp.run_probe(items, bad_success_usage, reps=1, concurrency=1)
    assert results[0]["skipped"] is False and results[0]["error"] is None
    assert results[0]["usage"] is None  # non-dict usage converted to None
    payload_doc = tp.payload(doc, items, results, model="test", provider="test", reps=1, factors=[], max_tokens=2048)
    assert payload_doc["usage"]["n_calls"] == 0  # no valid dict usage


def test_cli_no_unpriced_warning_without_a_cap(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: None)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--out-json", str(out)])
    args.func(args)
    err = capsys.readouterr().err
    assert "max-cost-usd" not in err
    assert json.loads(out.read_text(encoding="utf-8"))["usage"]["cost_known_calls"] == 0


def test_cli_skipping_exactly_one_call(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: 0.01)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "0.15", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "1 of 16 call(s) not run" in err


def test_stopping_message_in_cap_reached_case(monkeypatch, tmp_path, capsys):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    monkeypatch.setattr(llm, "chat_completion",
                        lambda messages, model, tools=None, **k: _Resp([tools[0]["function"]["name"]]))
    monkeypatch.setattr(llm, "_safe_completion_cost", lambda *a, **k: 0.01)
    out = tmp_path / "pref.json"
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--concurrency", "1",
                                      "--max-cost-usd", "5", "--out-json", str(out)])
    args.func(args)
    captured = capsys.readouterr()
    assert "stopping at $5" in captured.out
    assert "Warning:" not in captured.err


def test_max_cost_usd_zero_skips_all(doc):
    items = tp.build_items(doc, ["assertive"])
    results = tp.run_probe(items, lambda i: {"called": [], "usage": {"cost_usd": 0.0001}},
                           reps=1, concurrency=1, max_cost_usd=0.0)
    assert all(r["skipped"] for r in results)


def test_dry_run_names_the_groups_a_factor_is_skipped_for(capsys):
    from dokimi.cli import build_parser

    expected = {"vendor_name": ["G02", "N01"], "long_desc": ["G02", "N01"], "exclusion": ["G02", "N01"]}
    doc = tp.load_groups(json.loads(GROUPS.read_text(encoding="utf-8")))
    assert tp.skipped_factors(doc) == expected
    assert tp.skipped_factors(doc, ["assertive", "long_desc"]) == {"long_desc": ["G02", "N01"]}
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--dry-run",
                                      "--factors", "assertive,exclusion"])
    args.func(args)
    out = capsys.readouterr().out
    assert "exclusion: skipped for 2 of 3 groups (G02, N01)" in out
    assert "assertive: skipped" not in out

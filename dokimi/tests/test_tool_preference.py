# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tool-preference probe: item design, outcome classification, the summary's arithmetic, the CLI."""

from __future__ import annotations

import copy
import json
from pathlib import Path

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


# ── outcomes ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("called,outcome", [
    (["search_docs"], "A"),
    (["find_documentation", "read_file"], "B"),
    (["find_documentation", "search_docs"], "both"),
    (["read_file"], "distractor"),
    ([], "none"),
])
def test_classify(doc, called, outcome):
    item = _by_id(tp.build_items(doc))["G01-baseline-Afirst"]
    assert tp.classify(item, called)["outcome"] == outcome


def test_vendor_names_map_back_to_their_role(doc):
    item = _by_id(tp.build_items(doc))["G01-vendor_name-A-first"]
    assert tp.classify(item, ["acme_docs_search"])["outcome"] == "A"


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
    data = tp.payload(doc, items, results, model="m", provider="openai", reps=2, factors=list(tp.FACTORS))
    data["schema_version"] = "tool_preference.v1"
    validate_artifact("tool_preference", data)
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

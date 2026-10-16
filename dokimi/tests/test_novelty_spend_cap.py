# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`--max-usd`: one dollar cap across a novelty run's role clients, enforced per call."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.client import LLMClient, SpendCap, SpendCapReached


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _priced(cost):
    calls = []

    def transport(prompt, system=None):
        calls.append(prompt)
        return "ok", UsageRecord(cost_usd=cost)

    return transport, calls


def test_the_cap_refuses_the_call_after_the_limit_is_spent():
    transport, calls = _priced(0.6)
    client = LLMClient(model="m", transport=transport, cap=SpendCap(limit_usd=1.0))
    client.text("a")
    client.text("b")            # 0.6 spent, under the limit: allowed, and goes over it
    with pytest.raises(SpendCapReached, match=r"\$1\.20 of \$1\.00 after 2 calls"):
        client.text("c")
    assert calls == ["a", "b"]  # the refused call never reached the provider


def test_a_call_without_a_price_stops_a_capped_run():
    transport, _ = _priced(None)
    client = LLMClient(model="m", transport=transport, cap=SpendCap(limit_usd=5.0))
    with pytest.raises(SpendCapReached, match="no price is known for m"):
        client.text("a")


def test_an_uncapped_client_ignores_missing_prices():
    transport, _ = _priced(None)
    assert LLMClient(model="m", transport=transport).text("a") == "ok"


def test_every_role_shares_one_cap():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--max-usd", "2")
    caps = {id(client.cap) for client in novelty_cmd._clients(args)}
    assert len(caps) == 1
    assert novelty_cmd._clients(args)[0].cap.limit_usd == 2.0


def test_without_the_flag_there_is_no_cap():
    args = _parse("novelty", "analyze", "--artifact", "skill:x")
    assert all(client.cap is None for client in novelty_cmd._clients(args))


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_a_cap_that_could_never_trip_is_refused(value):
    with pytest.raises(SystemExit):
        _parse("novelty", "analyze", "--artifact", "skill:x", "--max-usd", value)


@pytest.mark.parametrize("sub", ["analyze", "atoms", "probe", "shorten", "routing"])
def test_every_command_that_calls_a_model_takes_the_flag(sub):
    extra = {"probe": ["--atoms", "a.json"], "shorten": ["--atoms", "a.json"],
             "routing": ["--competitors", "skill:y"]}.get(sub, [])
    args = _parse("novelty", sub, "--artifact", "skill:x", *extra, "--max-usd", "3")
    assert args.max_usd == 3.0


def test_the_cap_gets_through_a_stage_that_absorbs_failed_calls(tmp_path, monkeypatch):
    """The faithfulness check treats a failed call as a missing verdict; the cap must not be one."""
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nDo not enable the legacy "
                     "driver.\n", encoding="utf-8")
    rows = [{"statement": "The legacy driver must not be enabled.",
             "question": "Should the legacy driver be enabled?", "answer": "No.",
             "kind": "contract", "tags": ["negative_constraint"], "span_ids": ["s0002"]}]
    calls = []

    def transport(prompt, system=None):
        calls.append(prompt)
        reply = (json.dumps(rows) if "Extract the checkable knowledge" in prompt
                 else json.dumps({"verdict": "supported"}))
        return reply, UsageRecord(cost_usd=1.0)

    real_client = novelty_cmd._client
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: replace(
        real_client(model, provider, role, args), transport=transport))
    out = tmp_path / "atoms.json"
    args = _parse("novelty", "atoms", "--artifact", f"skill:{skill}", "--out", str(out),
                  "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    assert len(calls) == 1      # extraction ran; the faithfulness call was refused
    assert not out.exists()


def test_the_estimate_points_at_the_cap(tmp_path, capsys):
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nText.\n", encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{skill}", "--estimate-only")
    args.func(args)
    assert "--max-usd" in capsys.readouterr().err


def test_a_reply_replayed_from_the_response_cache_counts_nothing():
    """A stopped run resumed under the same cap must get past where it stopped."""
    def transport(prompt, system=None):
        return "ok", UsageRecord(cost_usd=1.0, replayed=True)

    client = LLMClient(model="m", transport=transport, cap=SpendCap(limit_usd=0.5))
    for _ in range(3):
        client.text("a")
    assert client.cap.spent_usd == 0.0 and client.cap.calls == 3


def test_a_replayed_reply_needs_no_price():
    def transport(prompt, system=None):
        return "ok", UsageRecord(cost_usd=None, replayed=True)

    assert LLMClient(model="m", transport=transport, cap=SpendCap(limit_usd=1.0)).text("a") == "ok"


def test_the_response_cache_marks_what_it_replays(tmp_path, monkeypatch):
    from dokimi import caching

    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(tmp_path))
    caching.store_response("k", "text", UsageRecord(cost_usd=0.25))
    text, usage = caching.load_response("k")
    assert (text, usage.cost_usd, usage.replayed) == ("text", 0.25, True)   # cost kept as stored
    caching.store_response("k2", "text", usage)                               # a re-store stays loadable
    assert caching.load_response("k2")[1].replayed is True

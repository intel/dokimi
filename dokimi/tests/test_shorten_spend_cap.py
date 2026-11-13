# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`skill shorten` at the `--max-usd` cap: no file is written, and the message says what a rerun reuses."""

from __future__ import annotations

from dataclasses import replace

import pytest

from dokimi.cli import build_parser
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty import cost as novelty_cost
from dokimi.novelty.atoms import Atom, save_atoms
from dokimi.novelty.spans import segment

_VERDICT = '{"verdict": "retained", "why": "stated"}'


def _setup(tmp_path):
    """An artifact and an atoms file for 30 spans, each atom only partly overlapping its span."""
    rows = [f"Configure module{i} with parameter{i} always." for i in range(30)]
    artifact = tmp_path / "rows.md"
    artifact.write_text("# H\n\n" + "\n\n".join(rows) + "\n", encoding="utf-8")
    spans = segment(artifact.read_text(encoding="utf-8"))
    atoms = [Atom(statement=f"Configure module{i} with parameter{i} never requires restarting "
                            f"service{i}", question="q", answer="a", span_ids=(span.id,))
             for i, span in enumerate(s for s in spans if s.kind != "heading")]
    save_atoms(atoms, spans, tmp_path / "atoms.json", extractor="e", cap=None)
    return artifact


def _run(tmp_path, monkeypatch, *flags, prices=1.0):
    prompts = []

    def transport(prompt, system=None):
        prompts.append(prompt)
        price = prices.pop(0) if isinstance(prices, list) else prices  # a list: one price per call
        return _VERDICT, UsageRecord(cost_usd=price)

    real_client = novelty_cost.client
    monkeypatch.setattr(novelty_cost, "client", lambda model, provider, role, args: replace(
        real_client(model, provider, role, args), transport=transport))
    args = build_parser().parse_args([
        "skill", "shorten", "--artifact", f"text:{tmp_path / 'rows.md'}",
        "--atoms", str(tmp_path / "atoms.json"), "--model", "o", "--out", str(tmp_path / "short.md"),
        *flags])
    try:
        args.func(args)
    except SystemExit as stop:
        return prompts, str(stop)
    return prompts, None


def test_a_spent_cap_writes_nothing_and_says_how_to_keep_the_verdicts(tmp_path, monkeypatch):
    _setup(tmp_path)
    prompts, stop = _run(tmp_path, monkeypatch, "--max-usd", "0.05", "--cache-dir",
                         str(tmp_path / "cache"), prices=1.0)
    # The first adjudication is paid for ($1 against $0.05); the second is refused.
    assert len(prompts) == 1
    assert stop is not None and "spend cap reached" in stop
    assert "nothing was written (no candidate table and no --out file)" in stop
    assert f"the retention verdicts adjudicated before the stop (1) are stored in {tmp_path / 'cache'}" in stop
    assert "a higher --max-usd and the same --cache-dir" in stop
    assert not (tmp_path / "short.md").exists()


def test_without_a_cache_dir_the_message_says_the_verdicts_were_lost(tmp_path, monkeypatch):
    _setup(tmp_path)
    prompts, stop = _run(tmp_path, monkeypatch, "--max-usd", "0.05")
    assert len(prompts) == 1
    assert stop is not None and "nothing was written" in stop
    assert "the retention verdicts adjudicated in this run (1) were lost; --cache-dir <dir> would keep" in stop
    assert "stored in" not in stop


def test_a_stop_before_any_verdict_does_not_promise_a_replay(tmp_path, monkeypatch):
    _setup(tmp_path)
    real_client = novelty_cost.client

    def spent(model, provider, role, args):
        client = real_client(model, provider, role, args)
        client.cap.spent_usd = client.cap.limit_usd
        return replace(client, transport=lambda prompt, system=None: pytest.fail("called"))

    monkeypatch.setattr(novelty_cost, "client", spent)
    args = build_parser().parse_args([
        "skill", "shorten", "--artifact", f"text:{tmp_path / 'rows.md'}",
        "--atoms", str(tmp_path / "atoms.json"), "--model", "o", "--out", str(tmp_path / "short.md"),
        "--max-usd", "0.05", "--cache-dir", str(tmp_path / "cache")])
    with pytest.raises(SystemExit) as stop:
        args.func(args)
    assert "spend cap reached: $0.05 of $0.05 after 0 calls" in str(stop.value)
    assert f"no retention verdict is stored in {tmp_path / 'cache'} from this run: none had been adjudicated yet" \
        in str(stop.value)
    assert "replay" not in str(stop.value)


def test_a_cap_spent_before_any_call_without_a_cache_dir_does_not_claim_lost_verdicts(tmp_path, monkeypatch):
    _setup(tmp_path)
    real_client = novelty_cost.client

    def spent(model, provider, role, args):
        client = real_client(model, provider, role, args)
        client.cap.spent_usd = client.cap.limit_usd
        return replace(client, transport=lambda prompt, system=None: pytest.fail("called"))

    monkeypatch.setattr(novelty_cost, "client", spent)
    args = build_parser().parse_args([
        "skill", "shorten", "--artifact", f"text:{tmp_path / 'rows.md'}",
        "--atoms", str(tmp_path / "atoms.json"), "--model", "o", "--max-usd", "0.05"])
    with pytest.raises(SystemExit) as stop:
        args.func(args)
    assert "no retention call had been paid for yet" in str(stop.value)
    assert "lost" not in str(stop.value)


def test_an_unpriced_stop_says_its_paid_verdict_was_not_stored(tmp_path, monkeypatch):
    _setup(tmp_path)
    prompts, stop = _run(tmp_path, monkeypatch, "--max-usd", "5", "--cache-dir",
                         str(tmp_path / "cache"), prices=None)
    assert len(prompts) == 1
    assert stop is not None and "no price is known" in stop
    assert f"no retention verdict is stored in {tmp_path / 'cache'} from this run: the one call made " \
           "was paid for and its verdict was not stored" in stop
    assert "no verdict" not in stop and "replay" not in stop
    assert not list((tmp_path / "cache").glob("*.json"))


def test_an_unpriced_stop_without_a_cache_dir_does_not_say_zero_were_lost(tmp_path, monkeypatch):
    _setup(tmp_path)
    prompts, stop = _run(tmp_path, monkeypatch, "--max-usd", "5", prices=None)
    assert len(prompts) == 1
    assert stop is not None and "the one retention call made was paid for and its verdict was not stored" in stop
    assert "(0)" not in stop


def test_an_unpriced_call_after_priced_ones_counts_only_the_stored_verdicts(tmp_path, monkeypatch):
    _setup(tmp_path)
    cache = tmp_path / "cache"
    prompts, stop = _run(tmp_path, monkeypatch, "--max-usd", "5", "--cache-dir", str(cache),
                         prices=[0.01, 0.01, None])
    assert len(prompts) == 3
    assert stop is not None and f"verdicts adjudicated before the stop (2) are stored in {cache}" in stop
    assert "rerun with no --max-usd and the same --cache-dir" in stop
    assert "the call with no price was paid for and its verdict was not stored" in stop
    assert len(list(cache.glob("*.json"))) == 2


def test_a_rerun_with_a_higher_cap_and_the_same_cache_dir_replays_the_paid_verdicts(tmp_path, monkeypatch):
    _setup(tmp_path)
    cache = str(tmp_path / "cache")
    first, stop = _run(tmp_path, monkeypatch, "--max-usd", "3", "--cache-dir", cache)
    assert stop is not None and len(first) == 3
    second, stop = _run(tmp_path, monkeypatch, "--max-usd", "1000", "--cache-dir", cache)
    assert stop is None and second
    assert not set(first) & set(second)
    assert (tmp_path / "short.md").exists()

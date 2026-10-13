# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A failed or truncated model reply must not read as a verdict.

Each test pins a place where a lost reply used to become a default value: an atom judged
*not needed* (so *inert*), a conflict check that silently saw nothing, a retention
adjudication cached as *absent*, a reply cut at the output ceiling with no trace.
"""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace

from dokimi.novelty.atoms import Atom
from dokimi.novelty.batching import ask_in_batches
from dokimi.novelty.classify import (
    ClassifyReport,
    apply_conflicts,
    categorize,
    detect_conflicts,
    judge_value,
)
from dokimi.novelty.client import LLMClient
from dokimi.novelty.retention import ABSENT, RetentionOracle


def _atoms(n: int) -> list[Atom]:
    return [Atom(statement=f"Setting {i} defaults to {i * 10}.", question="q", answer="a",
                 span_ids=(f"s{i:04d}",)) for i in range(1, n + 1)]


def _numbers(prompt: str) -> list[int]:
    return [int(m) for m in re.findall(r"^(\d+)\. ", prompt, flags=re.M)]


def test_ask_in_batches_splits_an_unparseable_reply_and_keeps_order():
    def truncating(prompt, system):
        rows = _numbers(prompt)
        reply = json.dumps(rows)
        return (reply[:-1] if len(rows) > 2 else reply), None

    items = list(range(7))
    client = LLMClient(model="x", transport=truncating)
    result = ask_in_batches(
        client, items, size=7, parse=json.loads,
        prompt=lambda chunk: "\n".join(f"{i + 1}. item" for i in range(len(chunk))),
    )
    assert [i for chunk, _ in result.answered for i in chunk] == items
    assert result.failed == [] and result.retried_batches > 0


def test_ask_in_batches_does_not_split_a_transport_failure():
    calls: list[int] = []

    def down(prompt, system):
        calls.append(1)
        raise RuntimeError("throttled")

    result = ask_in_batches(LLMClient(model="x", transport=down), list(range(10)), size=5,
                            prompt=lambda chunk: "p", parse=json.loads)
    assert len(calls) == 2
    assert result.failed == list(range(10)) and "throttled" in result.last_error


def test_a_failed_value_batch_is_unjudged_not_inert():
    atoms = _atoms(6)

    def judge(prompt, system):
        if "Setting 1 " in prompt:
            raise RuntimeError("throttled")
        return json.dumps([{"n": n, "needed": True, "scaffolding": False}
                           for n in _numbers(prompt)]), None

    report = ClassifyReport()
    verdicts = judge_value(atoms, client=LLMClient(model="x", transport=judge), task="t",
                           batch=3, report=report)
    assert set(report.unjudged) == {a.id for a in atoms[:3]}
    categories = categorize(atoms, verdicts, outcomes={a.id: "added" for a in atoms})
    assert "novel_inert" not in categories.values()
    assert sorted(categories) == sorted(a.id for a in atoms[3:])
    assert any("3 of 6 atoms got no need/scaffolding verdict" in w
               for w in report.warnings(len(atoms)))


def test_a_row_the_judge_skipped_is_unjudged():
    atoms = _atoms(3)

    def skips_two(prompt, system):
        return json.dumps([{"n": 1, "needed": True}, {"n": 3, "needed": True}]), None

    report = ClassifyReport()
    verdicts = judge_value(atoms, client=LLMClient(model="x", transport=skips_two),
                           task="t", report=report)
    assert report.unjudged == [atoms[1].id]
    assert atoms[1].id not in verdicts


def test_an_unjudged_atom_that_conflicts_is_still_harmful():
    atoms = _atoms(2)
    verdicts: dict = {}
    apply_conflicts(verdicts, [{"a": atoms[0].id, "b": atoms[1].id, "why": "x"}])
    assert set(categorize(atoms, verdicts, outcomes={}).values()) == {"harmful_conflicting"}


def test_conflict_coverage_states_pairs_across_batches_were_not_compared():
    atoms = _atoms(10)
    none = LLMClient(model="x", transport=lambda p, s: (json.dumps({"conflicts": []}), None))
    report = ClassifyReport()
    detect_conflicts(atoms, client=none, batch=5, report=report)
    assert (report.pairs_compared, report.pairs_total) == (20, 45)
    assert any("20 of 45 atom pairs" in w for w in report.warnings(len(atoms)))
    one_batch = ClassifyReport()
    detect_conflicts(atoms, client=none, batch=40, report=one_batch)
    assert one_batch.warnings(len(atoms)) == []


def test_a_failed_conflict_batch_is_reported_unchecked():
    atoms = _atoms(4)

    def down(prompt, system):
        raise RuntimeError("throttled")

    report = ClassifyReport()
    assert detect_conflicts(atoms, client=LLMClient(model="x", transport=down),
                            report=report) == []
    assert report.unchecked == [a.id for a in atoms]


def test_a_failed_adjudication_is_not_cached_as_absent(tmp_path):
    atom = Atom(statement="alpha beta gamma delta", question="q", answer="a",
                span_ids=("s1",))
    text = "alpha beta epsilon zeta"  # inside the ambiguous band, so it escalates
    calls: list[int] = []

    def flaky(prompt, system):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("throttled")
        return json.dumps({"verdict": "retained"}), None

    oracle = RetentionOracle(client=LLMClient(model="x", transport=flaky), cache_dir=tmp_path)
    first = oracle.verdict(atom, text)
    assert (first.verdict, first.method) == (ABSENT, "error")
    assert oracle.stats()["llm_errors"] == 1 and not list(tmp_path.iterdir())
    again = RetentionOracle(client=LLMClient(model="x", transport=flaky), cache_dir=tmp_path)
    assert again.verdict(atom, text).retained


def test_a_reply_cut_at_the_output_ceiling_is_logged(monkeypatch, caplog):
    import litellm

    from dokimi import llm

    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)

    def cut(model, messages, api_key=None, **kw):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="[{\"a\": 1"),
                                     finish_reason="length")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4096),
        )

    monkeypatch.setattr(litellm, "completion", cut)
    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        text, _ = llm.llm_call_with_usage("p", model="m", provider="openai", api_key="k")
    assert text == "[{\"a\": 1"
    assert any("output ceiling" in r.getMessage() for r in caplog.records)

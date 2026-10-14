# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A failed or truncated model reply must not read as a verdict.

Each test pins a place where a lost reply used to become a default value: an atom judged
*not needed* (so *inert*), a conflict check that silently saw nothing, a retention
adjudication cached as *absent*, a reply cut at the output ceiling with no trace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from types import SimpleNamespace

import pytest

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
from dokimi.novelty.retention import ABSENT, RETAINED, RetentionOracle, _key


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


def test_ask_in_batches_re_asks_in_place_when_only_the_first_half_fails():
    def first_item_breaks(prompt, system):
        items = [int(m) for m in re.findall(r"^item (\d+)$", prompt, flags=re.M)]
        return ("[" if 0 in items and len(items) > 1 else json.dumps(items)), None

    items = list(range(8))
    result = ask_in_batches(
        LLMClient(model="x", transport=first_item_breaks), items, size=8, parse=json.loads,
        prompt=lambda chunk: "\n".join(f"item {i}" for i in chunk),
    )
    # Re-asking at the end of the queue would answer 4..7 before 0..3.
    assert [i for chunk, _ in result.answered for i in chunk] == items
    assert [reply for _, reply in result.answered] == [[0], [1], [2, 3], [4, 5, 6, 7]]
    assert result.failed == [] and result.last_error == ""  # recovered: nothing to blame


def test_ask_in_batches_does_not_split_an_empty_reply():
    calls: list[int] = []

    def starved(prompt, system):
        calls.append(1)
        return "", None

    result = ask_in_batches(LLMClient(model="x", transport=starved), list(range(10)),
                            size=5, prompt=lambda chunk: "p", parse=json.loads)
    assert len(calls) == result.calls == 2
    assert result.failed == list(range(10)) and "empty reply" in result.last_error


def test_ask_in_batches_does_not_send_a_chunk_below_min_size():
    sizes: list[int] = []

    def answers(prompt, system):
        sizes.append(len(prompt.splitlines()))
        return "[]", None

    result = ask_in_batches(LLMClient(model="x", transport=answers), list(range(5)), size=2,
                            prompt=lambda chunk: "\n".join("x" for _ in chunk),
                            parse=json.loads, min_size=2)
    assert sizes == [2, 2]  # the one-item remainder is never asked
    assert result.too_small == [4] and result.failed == []


def test_ask_in_batches_fails_a_chunk_it_cannot_halve_above_min_size():
    sizes: list[int] = []

    def garbled(prompt, system):
        sizes.append(len(prompt.splitlines()))
        return "not json", None

    result = ask_in_batches(LLMClient(model="x", transport=garbled), list(range(5)), size=5,
                            prompt=lambda chunk: "\n".join("x" for _ in chunk),
                            parse=json.loads, min_size=2)
    # 5 splits into 2 + 3; neither halves into two chunks of 2, so neither is re-asked.
    assert sizes == [5, 2, 3] and result.calls == 3 and result.retried_batches == 1
    assert result.failed == list(range(5)) and result.too_small == []
    assert result.last_error.startswith("JSONDecodeError: ")


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
    # No call failed, so there is no error to quote ("(last error: )" before).
    [warning] = report.warnings(len(atoms))
    assert warning.endswith("(1 skipped by the judge's reply)")


def test_each_stage_quotes_its_own_error_not_one_a_split_recovered_from():
    atoms = _atoms(4)

    def transport(prompt, system):
        if "judge how it functions" in prompt:
            raise TimeoutError("judge timed out")
        if len(_numbers(prompt)) > 2:
            return "{\"conflicts\": [", None  # recovered by splitting
        return json.dumps({"conflicts": []}), None

    client = LLMClient(model="x", transport=transport)
    report = ClassifyReport()
    judge_value(atoms, client=client, task="t", report=report)
    detect_conflicts(atoms, client=client, report=report)
    [unjudged] = [w for w in report.warnings(len(atoms)) if "no need/scaffolding" in w]
    assert "TimeoutError: judge timed out" in unjudged
    assert "ValueError" not in unjudged and "JSONDecodeError" not in unjudged
    assert report.unchecked == [] and report.conflict_error == ""


def test_an_unjudged_atom_that_conflicts_is_still_harmful():
    atoms = _atoms(2)
    verdicts: dict = {}
    apply_conflicts(verdicts, [{"a": atoms[0].id, "b": atoms[1].id, "why": "x"},
                               {"a": "", "b": atoms[1].id}])  # a side with no id is skipped
    assert set(categorize(atoms, verdicts, outcomes={}).values()) == {"harmful_conflicting"}
    assert "" not in verdicts


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
    assert (report.pairs_compared, report.pairs_total) == (0, 6)  # a failed call compared none
    warnings = report.warnings(len(atoms))
    assert any(w.startswith("4 of 4 atoms were not checked for conflicts (4 in calls that "
                            "failed (last error: RuntimeError: throttled)") for w in warnings)
    assert any("0 of 6 atom pairs shared a call" in w for w in warnings)


def test_a_conflict_row_names_the_atoms_of_its_own_chunk():
    atoms = _atoms(4)

    def second_chunk_conflicts(prompt, system):
        if "Setting 3 " in prompt:
            return json.dumps({"conflicts": [{"a": 1, "b": 2, "why": "x"}]}), None
        return json.dumps({"conflicts": []}), None

    conflicts = detect_conflicts(
        atoms, client=LLMClient(model="x", transport=second_chunk_conflicts), batch=2
    )
    assert conflicts == [{"a": atoms[2].id, "b": atoms[3].id, "why": "x"}]


@pytest.mark.parametrize("reply", ["[]", "null", '[{"a": 1, "b": 2, "why": "x"}]',
                                   '{"conflicts": null}', '{"found": []}'])
def test_a_conflict_reply_without_a_conflicts_list_is_unchecked_not_a_crash(reply):
    atoms = _atoms(4)
    report = ClassifyReport()
    conflicts = detect_conflicts(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (reply, None)),
        report=report,
    )
    assert conflicts == [] and sorted(report.unchecked) == sorted(a.id for a in atoms)
    assert report.pairs_compared == 0


def test_conflict_chunks_are_not_split_into_single_statements():
    atoms = _atoms(40)
    sizes: list[int] = []

    def garbled_above_one(prompt, system):
        sizes.append(len(_numbers(prompt)))
        return "{\"conflicts\": [", None

    report = ClassifyReport()
    detect_conflicts(atoms, client=LLMClient(model="x", transport=garbled_above_one),
                     report=report)
    assert min(sizes) == 2  # a one-statement call has no pair to check
    # 40 -> 20 -> 10 -> 5 -> 2 + 3: the 2s and 3s cannot halve into pairs, so they fail
    # with the parse error rather than becoming singletons "alone in a batch".
    assert len(sizes) == report.calls == 31 and report.retried_batches == 15
    assert len(report.unchecked) == 40 and report.unchecked_alone == 0
    assert report.pairs_compared == 0
    assert report.conflict_error.startswith("ValueError: ")
    assert (
        f"40 of 40 atoms were not checked for conflicts (40 in calls that failed "
        f"(last error: {report.conflict_error}))"
    ) in report.warnings(len(atoms))


def test_the_response_cache_still_replays_a_parseable_batch(monkeypatch, tmp_path):
    calls = _completion_counter(monkeypatch, tmp_path, '[{"n": 1, "needed": true}]')
    for _ in range(2):
        verdicts = judge_value(_atoms(1), client=LLMClient(model="m", provider="openai"),
                               task="t")
        assert len(verdicts) == 1
    assert len(calls) == 1


def test_the_response_cache_does_not_replay_an_unparseable_batch(monkeypatch, tmp_path):
    calls = _completion_counter(monkeypatch, tmp_path, '[{"n": 1, "needed": tr')
    for _ in range(2):
        report = ClassifyReport()
        judge_value(_atoms(1), client=LLMClient(model="m", provider="openai"), task="t",
                    report=report)
        assert len(report.unjudged) == 1
    assert len(calls) == 2


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
    # Not memoized either: the same oracle asks again, and both decisions count.
    second = oracle.verdict(atom, text)
    assert (second.verdict, second.method) == (RETAINED, "llm")
    assert oracle.stats()["decisions"] == 2 and len(calls) == 2
    again = RetentionOracle(client=LLMClient(model="x", transport=flaky), cache_dir=tmp_path)
    assert again.verdict(atom, text).retained


_AMBIGUOUS = (Atom(statement="alpha beta gamma delta", question="q", answer="a",
                   span_ids=("s1",)), "alpha beta epsilon zeta")


@pytest.mark.parametrize("reply", [
    '{"verdict": null}', '{"why": "no verdict key"}', '{"verdict": "maybe"}',
    '{"verdict": 1}', "[]", '{"verdict": "reta', '{"verdict": "retained?"}',
    '{"verdict": "Retained ?"}',
])
def test_a_reply_with_no_usable_verdict_is_an_error_not_a_cached_absent(tmp_path, reply):
    atom, text = _AMBIGUOUS
    oracle = RetentionOracle(client=LLMClient(model="x", transport=lambda p, s: (reply, None)),
                             cache_dir=tmp_path)
    result = oracle.verdict(atom, text)
    assert (result.verdict, result.method) == (ABSENT, "error")
    assert oracle.stats()["llm_errors"] == 1 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("reply", ['{"verdict": "Retained"}', '{"verdict": "retained."}',
                                   '{"verdict": " RETAINED! "}'])
def test_a_verdict_is_read_through_case_and_punctuation(reply):
    atom, text = _AMBIGUOUS
    oracle = RetentionOracle(client=LLMClient(model="x", transport=lambda p, s: (reply, None)))
    result = oracle.verdict(atom, text)
    assert (result.verdict, result.method) == (RETAINED, "llm")


def test_a_cache_entry_from_before_the_error_fix_is_not_replayed(tmp_path):
    atom, text = _AMBIGUOUS
    # The pre-fix key: no version salt. An error then was stored as a plain `absent`.
    digest = hashlib.sha256()
    digest.update(atom.statement.strip().encode("utf-8"))
    digest.update(b"\0")
    digest.update(text.encode("utf-8"))
    (tmp_path / f"{digest.hexdigest()[:32]}.json").write_text(
        json.dumps({"verdict": "absent", "score": 0.5}), encoding="utf-8")
    oracle = RetentionOracle(
        client=LLMClient(model="x", transport=lambda p, s: ('{"verdict": "retained"}', None)),
        cache_dir=tmp_path,
    )
    result = oracle.verdict(atom, text)
    assert (result.verdict, result.method) == (RETAINED, "llm")


@pytest.mark.parametrize("stored", ['{"verdict": "maybe", "score": 0.5}', "{not json",
                                    '{"verdict": ["absent"]}'])
def test_an_unusable_disk_cache_entry_is_asked_again(tmp_path, stored):
    atom, text = _AMBIGUOUS
    oracle = RetentionOracle(
        client=LLMClient(model="x", transport=lambda p, s: ('{"verdict": "retained"}', None)),
        cache_dir=tmp_path,
    )
    (tmp_path / f"{_key(atom, text)}.json").write_text(stored, encoding="utf-8")
    assert oracle.verdict(atom, text).method == "llm"


def _completion_counter(monkeypatch, tmp_path, content: str,
                        finish_reason: str = "stop") -> list[int]:
    """Route the real default transport into a fake completion, with the response cache on."""
    import litellm

    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(tmp_path / "responses"))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    calls: list[int] = []

    def completion(model, messages, api_key=None, **kw):
        calls.append(1)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                     finish_reason=finish_reason)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr(litellm, "completion", completion)
    return calls


def test_the_response_cache_does_not_replay_a_failed_adjudication(monkeypatch, tmp_path):
    calls = _completion_counter(monkeypatch, tmp_path, '{"verdict": "reta')
    atom, text = _AMBIGUOUS
    for _ in range(3):
        oracle = RetentionOracle(client=LLMClient(model="m", provider="openai"))
        assert oracle.verdict(atom, text).method == "error"
    assert len(calls) == 3  # every run asks again; a cached failure would make this 1


def test_the_response_cache_still_replays_a_usable_adjudication(monkeypatch, tmp_path):
    calls = _completion_counter(monkeypatch, tmp_path, '{"verdict": "retained"}')
    atom, text = _AMBIGUOUS
    for _ in range(2):
        oracle = RetentionOracle(client=LLMClient(model="m", provider="openai"))
        assert oracle.verdict(atom, text).method == "llm"
    assert len(calls) == 1


@pytest.mark.parametrize("finish_reason, logged",
                         [("length", True), ("max_tokens", True), ("stop", False)])
def test_a_reply_cut_at_the_output_ceiling_is_logged(monkeypatch, caplog, finish_reason,
                                                       logged):
    import litellm

    from dokimi import llm

    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)

    def cut(model, messages, api_key=None, **kw):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="[{\"a\": 1"),
                                     finish_reason=finish_reason)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4096),
        )

    monkeypatch.setattr(litellm, "completion", cut)
    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        text, _ = llm.llm_call_with_usage("p", model="m", provider="openai", api_key="k")
    assert text == "[{\"a\": 1"
    assert any("output ceiling" in r.getMessage() for r in caplog.records) is logged


def test_a_streamed_reply_cut_at_the_output_ceiling_is_logged(monkeypatch, caplog):
    import litellm

    from dokimi import llm

    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)

    def chunk(content, finish_reason=None, usage=None):
        delta = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
                               usage=usage)

    def streamed(model, messages, api_key=None, **kw):
        return iter([chunk("[{\"a\""), chunk(": 1", finish_reason="length"),
                     chunk(None, usage=SimpleNamespace(prompt_tokens=10,
                                                       completion_tokens=4096))])

    monkeypatch.setattr(litellm, "completion", streamed)
    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        text, _ = llm.llm_call_with_usage("p", model="m", provider="openai", api_key="k",
                                          stream=True)
    assert text == "[{\"a\": 1"
    assert any("output ceiling" in r.getMessage() for r in caplog.records)


# Every other caller of the response cache: routing (text), the answerer (text), and the
# json_object callers (the grader, verify_atoms, the need graph). Each reply below parses
# or is prose, so only a shape test keeps it out of the cache.

def _route():
    from dokimi.novelty.routing import Candidate, model_routing

    cards = [Candidate("alpha", "does alpha things"), Candidate("beta", "does beta things")]
    return model_routing(cards, ["do alpha"], client=LLMClient(model="m", provider="openai"))


@pytest.mark.parametrize("reply, asked", [
    ('{"pick":', 2), ('{"choice": 1}', 2), ('{"pick": 1}', 1), ('{"pick": 0}', 1),
    ('{"pick": null}', 1), ('{"pick": "2"}', 1),
    ('{"pick": "abc"}', 2), ('{"pick": 2.9}', 2), ('{"pick": [1]}', 2), ('{"pick": 999}', 2),
    ('{"pick": -1}', 2), ('{"pick": true}', 2),
])
def test_routing_caches_only_a_reply_that_names_a_pick(monkeypatch, tmp_path, reply, asked):
    calls = _completion_counter(monkeypatch, tmp_path, reply)
    for _ in range(2):
        result = _route()
        assert result["n_failed"] == 0
    assert len(calls) == asked  # a cut reply is an abstention this run, not a cached one


def _measure_once(judge_reply: str = '{"verdict": "correct_complete"}'):
    from dokimi.novelty.measure import run_measurement
    from dokimi.novelty.probes import Probe

    atom = Atom(statement="x is 1", question="What is x?", answer="1", span_ids=("s1",))
    probe = Probe(atom_id=atom.id, kind="direct", question=atom.question, expected="1")
    judge = LLMClient(model="j", transport=lambda p, s: (judge_reply, None))
    return run_measurement([atom], [probe], "x is 1", concurrency=1, judge=judge,
                           answerer=LLMClient(model="m", provider="openai"))


@pytest.mark.parametrize("answer, asked", [("1\nconfidence: 0.9", 2), ("The value of x is", 4)])
def test_the_answerer_caches_only_an_answer_that_reached_its_confidence_line(
        monkeypatch, tmp_path, answer, asked):
    calls = _completion_counter(monkeypatch, tmp_path, answer)
    for _ in range(2):
        _measure_once()
    assert len(calls) == asked  # two arms; a cut answer is asked again on every run


def test_an_answer_with_no_text_at_all_gets_the_max_tokens_hint(monkeypatch, tmp_path):
    calls = _completion_counter(monkeypatch, tmp_path, None)
    result = _measure_once()
    assert all(run.errored for run in result.runs)
    assert "raise --max-tokens" in result.runs[0].response
    _measure_once()
    assert len(calls) == 4  # nothing was stored


def test_a_hedged_verdict_stored_by_the_v2_cache_is_not_read(tmp_path, monkeypatch):
    from dokimi.novelty import retention

    atom, text = _AMBIGUOUS
    monkeypatch.setattr(retention, "_CACHE_VERSION", b"retention-cache-v2\0")
    old_key = _key(atom, text)
    monkeypatch.undo()
    (tmp_path / f"{old_key}.json").write_text('{"verdict": "retained", "score": 0.4}',
                                              encoding="utf-8")
    oracle = RetentionOracle(
        client=LLMClient(model="x", transport=lambda p, s: ('{"verdict": "absent"}', None)),
        cache_dir=tmp_path,
    )
    assert oracle.verdict(atom, text).method == "llm"


def test_a_cut_answer_logs_the_truncation_on_every_run(monkeypatch, tmp_path, caplog):
    from dokimi import llm

    _completion_counter(monkeypatch, tmp_path, "The value of x is", finish_reason="length")
    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        for _ in range(2):
            _measure_once()
    assert sum("output ceiling" in r.getMessage() for r in caplog.records) == 4


def test_an_empty_answer_is_a_dropped_pair_not_a_wrong_one(monkeypatch, tmp_path):
    calls = _completion_counter(monkeypatch, tmp_path, "")
    result = _measure_once()
    assert all(run.errored for run in result.runs)
    assert "raise --max-tokens" in result.runs[0].response
    assert result.paired("lenient")["n_unpaired_dropped"] == 1
    _measure_once()
    assert len(calls) == 4  # not cached either


def _grade_twice(monkeypatch, tmp_path, reply):
    from dokimi.novelty.measure import _grade
    from dokimi.novelty.probes import Probe

    calls = _completion_counter(monkeypatch, tmp_path, reply)
    probe = Probe(atom_id="a", kind="direct", question="q", expected="1")
    verdicts = [_grade(probe, "1", LLMClient(model="j", provider="openai")) for _ in range(2)]
    return verdicts, len(calls)


@pytest.mark.parametrize("reply, verdict, asked", [
    ('{"verdict": null}', "error", 2), ('{"why": "no verdict"}', "error", 2),
    ('{"verdict": ""}', "error", 2), ('{"verdict": "incorrect"}', "wrong_other", 2),
    ('{"verdict": "correct_complete"}', "correct_complete", 1),
])
def test_the_grader_caches_only_a_named_verdict(monkeypatch, tmp_path, reply, verdict, asked):
    verdicts, n_calls = _grade_twice(monkeypatch, tmp_path, reply)
    assert verdicts == [verdict, verdict] and n_calls == asked


@pytest.mark.parametrize("reply, label, asked", [
    ('{"verdict": null}', "unchecked", 2), ('{"verdict": "maybe"}', "unchecked", 2),
    ('{"verdict": "supported"}', "supported", 1),
])
def test_verify_atoms_caches_only_a_faithfulness_verdict(monkeypatch, tmp_path, reply, label,
                                                         asked):
    from dokimi.novelty.atoms import verify_atoms
    from dokimi.novelty.spans import segment

    calls = _completion_counter(monkeypatch, tmp_path, reply)
    spans = segment("Setting 1 defaults to 10.")
    for _ in range(2):
        atom = Atom(statement="Setting 1 defaults to 10.", question="q", answer="10",
                    span_ids=(spans[0].id,))
        verify_atoms([atom], spans, client=LLMClient(model="m", provider="openai"))
        assert atom.faithfulness == label
    assert len(calls) == asked


@pytest.mark.parametrize("reply, n_nodes, asked", [
    ('{"nodes": []}', 0, 2), ('{"edges": []}', 0, 2), ('{"nodes": [{"text": ""}]}', 0, 2),
    ('{"nodes": [{"id": "n1", "text": "pick a backend"}]}', 1, 1),
])
def test_the_need_graph_caches_only_a_graph_with_a_node(monkeypatch, tmp_path, reply,
                                                        n_nodes, asked):
    from dokimi.novelty.needs import build_need_graph

    calls = _completion_counter(monkeypatch, tmp_path, reply)
    for _ in range(2):
        graph = build_need_graph("n", "d", client=LLMClient(model="m", provider="openai"))
        assert len(graph.nodes) == n_nodes
    assert len(calls) == asked


_UNUSABLE_ROWS = '[{"text": "Setting 1 defaults to 10.", "ids": ["s0001"]}]'
_USABLE_ROWS = json.dumps([{"statement": "Setting 1 defaults to 10.", "question": "q",
                            "answer": "10", "span_ids": ["s0001"]}])


def _one_span():
    from dokimi.novelty.spans import segment

    spans = segment("Setting 1 defaults to 10.")
    assert [s.id for s in spans] == ["s0001"]
    return spans


def test_the_response_cache_does_not_replay_a_reply_with_no_usable_atom(monkeypatch, tmp_path):
    from dokimi.novelty.atoms import ExtractionError, extract_atoms

    calls = _completion_counter(monkeypatch, tmp_path, _UNUSABLE_ROWS)
    for _ in range(2):
        with pytest.raises(ExtractionError):
            extract_atoms(_one_span(), client=LLMClient(model="m", provider="openai"))
    assert len(calls) == 2


def test_a_healthy_model_is_asked_again_after_a_useless_cached_run(monkeypatch, tmp_path):
    from dokimi.novelty.atoms import ExtractionError, extract_atoms

    _completion_counter(monkeypatch, tmp_path, _UNUSABLE_ROWS)
    with pytest.raises(ExtractionError):
        extract_atoms(_one_span(), client=LLMClient(model="m", provider="openai"))
    calls = _completion_counter(monkeypatch, tmp_path, _USABLE_ROWS)
    for _ in range(2):
        atoms = extract_atoms(_one_span(), client=LLMClient(model="m", provider="openai"))
        assert len(atoms) == 1
    assert len(calls) == 1  # asked once, then the usable reply is replayed


def test_the_response_cache_still_replays_a_legitimately_empty_extraction(monkeypatch, tmp_path):
    from dokimi.novelty.atoms import extract_atoms

    calls = _completion_counter(monkeypatch, tmp_path, "[]")
    for _ in range(2):
        assert extract_atoms(_one_span(), client=LLMClient(model="m", provider="openai")) == []
    assert len(calls) == 1


@pytest.mark.parametrize("row", [
    {"n": 1},
    {"n": 1, "needed": None},
    {"n": 1, "need": True, "scaffold": False},
    {"n": 1, "needed": "false"},
    {"n": 1, "needed": "no"},
    {"n": 1, "needed": 0},
    {"n": 1, "needed": True, "scaffolding": "false"},
    {"n": 1, "needed": True, "scaffolding": None},
    {"n": "\u00b2", "needed": True},
])
def test_a_judge_row_without_a_real_verdict_is_unjudged_not_a_default(row):
    atoms = _atoms(1)
    report = ClassifyReport()
    verdicts = judge_value(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (json.dumps([row]), None)),
        task="t", report=report,
    )
    assert verdicts == {} and report.unjudged == [atoms[0].id]
    assert report.unjudged_skipped == 1


def test_a_malformed_judge_row_does_not_cost_the_atoms_beside_it():
    atoms = _atoms(3)
    reply = json.dumps([{"n": 1, "needed": True}, {"n": 2, "needed": "yes"},
                        {"n": 3, "needed": False, "scaffolding": True}])
    report = ClassifyReport()
    verdicts = judge_value(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (reply, None)),
        task="t", report=report,
    )
    assert sorted(verdicts) == sorted([atoms[0].id, atoms[2].id])
    assert (verdicts[atoms[0].id].needed, verdicts[atoms[0].id].scaffolding) == (True, False)
    assert (verdicts[atoms[2].id].needed, verdicts[atoms[2].id].scaffolding) == (False, True)
    assert report.unjudged == [atoms[1].id]


def test_a_real_false_is_a_verdict_and_raises_no_warning():
    atoms = _atoms(2)
    reply = json.dumps([{"n": 1, "needed": False, "scaffolding": False}, {"n": 2, "needed": False}])
    report = ClassifyReport()
    verdicts = judge_value(
        atoms, client=LLMClient(model="x", transport=lambda p, s: (reply, None)),
        task="t", report=report,
    )
    assert [verdicts[a.id].needed for a in atoms] == [False, False]
    assert report.unjudged == [] and report.warnings(len(atoms)) == []

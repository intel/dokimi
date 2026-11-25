# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""A provider error every later call would meet stops atoms, probes, grades and batches (#708).

Each site gets a mocked transport that raises an authentication error at its second call, and
a counterexample: a context-length rejection of one item is that item's failure and the run goes on.
"""
from __future__ import annotations

import json
import re

import pytest

from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.atoms import Atom, ExtractionReport, extract_atoms, verify_atoms
from dokimi.novelty.batching import ask_in_batches
from dokimi.novelty.classify import ClassifyReport, detect_conflicts, judge_value
from dokimi.novelty.client import LLMClient, SpendCapReached
from dokimi.novelty.measure import ERRORED, run_measurement
from dokimi.novelty.probes import Probe, ProbeSet, build_probe_set
from dokimi.novelty.retention import RetentionOracle
from dokimi.novelty.spans import segment


class AuthenticationError(Exception):
    """Named as litellm's, which is_retryable and is_run_wide read by class name."""


class BadRequestError(Exception):
    """A context-length rejection of one oversized item."""


class Calls:
    """A transport that counts its calls and raises ``error`` at call number ``at``."""

    def __init__(self, reply, *, error=None, at=2):
        self.reply, self.error, self.at, self.n = reply, error, at, 0

    def __call__(self, prompt, system=None):
        self.n += 1
        if self.error is not None and self.n == self.at:
            raise self.error
        reply = self.reply(prompt) if callable(self.reply) else self.reply
        return reply, UsageRecord(cost_usd=0.0)


def _client(transport, model="m") -> LLMClient:
    return LLMClient(model=model, role="x", transport=transport)


AUTH = AuthenticationError("Unable to locate credentials")
TOO_LONG = BadRequestError("maximum context length is 8192 tokens")
ROWS = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 101)) + "\n"


def _atoms(n: int) -> list[Atom]:
    return [Atom(statement=f"Setting {i} defaults to {i}.", question=f"What is setting {i}?",
                 answer=str(i), span_ids=(f"s{i}",)) for i in range(1, n + 1)]


def _one_atom_per_span(prompt):
    return json.dumps([{"statement": f"{sid} holds a default.", "question": f"What of {sid}?",
                        "answer": "a value", "kind": "proposition", "tags": [], "span_ids": [sid]}
                       for sid in re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)])


def test_extraction_stops_at_the_first_run_wide_error_and_keeps_the_atoms_read():
    spans = segment(ROWS)
    transport = Calls(_one_atom_per_span, error=AUTH)
    report = ExtractionReport()
    atoms = extract_atoms(spans, client=_client(transport), report=report)
    assert transport.n == 2  # four batches of 25 spans
    assert len(atoms) == 25
    assert report.partial["reason"] == "permanent_error" and report.partial["spans_read"] == 25
    assert report.partial["message"].endswith("permanent error: Unable to locate credentials")
    assert not any(span.unread for span in spans[:25]) and all(span.unread for span in spans[25:])
    assert report.call_error == ""  # not recorded as one failed batch


def test_extraction_goes_on_after_a_batch_the_model_rejects():
    spans = segment(ROWS)
    transport = Calls(_one_atom_per_span, error=TOO_LONG)
    report = ExtractionReport()
    atoms = extract_atoms(spans, client=_client(transport), report=report)
    assert transport.n == 4 and len(atoms) == 75 and report.partial is None
    assert report.call_error.startswith("BadRequestError")


def test_the_faithfulness_check_stops_at_the_first_run_wide_error():
    atoms = _atoms(4)
    transport = Calls('{"verdict": "supported"}', error=AUTH)
    with pytest.raises(SpendCapReached) as stop:
        verify_atoms(atoms, segment(ROWS), client=_client(transport))
    assert transport.n == 2 and stop.value.reason == "permanent_error"
    assert atoms[0].faithfulness == "supported" and atoms[2].faithfulness == "unchecked"


def test_the_faithfulness_check_leaves_one_atom_unchecked_on_a_rejected_request():
    atoms = _atoms(4)
    transport = Calls('{"verdict": "supported"}', error=TOO_LONG)
    tally = verify_atoms(atoms, segment(ROWS), client=_client(transport))
    assert transport.n == 4 and tally == {"supported": 3, "distorted": 0, "unsupported": 0,
                                          "unchecked": 1}


def test_ask_in_batches_stops_at_a_run_wide_error_and_says_why():
    transport = Calls("[1]", error=AUTH)
    result = ask_in_batches(_client(transport), list(range(6)), size=2,
                            prompt=str, parse=json.loads)
    assert transport.n == 2
    assert result.stop is not None and result.stop.reason == "permanent_error"
    assert str(result.stop) == "permanent error: Unable to locate credentials"
    assert result.answered == [([0, 1], [1])] and result.failed == [2, 3, 4, 5]
    assert result.last_error == ""


def test_ask_in_batches_goes_on_after_a_rejected_request():
    transport = Calls("[1]", error=TOO_LONG)
    result = ask_in_batches(_client(transport), list(range(6)), size=2,
                            prompt=str, parse=json.loads)
    assert transport.n == 3 and result.stop is None
    assert result.failed == [2, 3] and result.last_error.startswith("BadRequestError")
    assert len(result.answered) == 2


def test_the_value_judge_stops_at_a_run_wide_error():
    transport = Calls('[{"n": 1, "needed": true}]', error=AUTH)
    with pytest.raises(SpendCapReached) as stop:
        judge_value(_atoms(3), client=_client(transport), task="t", batch=1)
    assert transport.n == 2 and stop.value.reason == "permanent_error"


def test_the_value_judge_leaves_one_batch_unjudged_on_a_rejected_request():
    transport = Calls('[{"n": 1, "needed": true}]', error=TOO_LONG)
    verdicts = judge_value(_atoms(3), client=_client(transport), task="t", batch=1)
    assert transport.n == 3 and len(verdicts) == 2


def test_the_conflict_check_stops_at_a_run_wide_error():
    transport = Calls('{"conflicts": []}', error=AUTH)
    with pytest.raises(SpendCapReached) as stop:
        detect_conflicts(_atoms(6), client=_client(transport), batch=2)
    assert transport.n == 2 and stop.value.reason == "permanent_error"


def test_the_conflict_check_leaves_one_batch_unchecked_on_a_rejected_request():
    transport = Calls('{"conflicts": []}', error=TOO_LONG)
    report = ClassifyReport()
    detect_conflicts(_atoms(6), client=_client(transport), batch=2, report=report)
    assert transport.n == 3
    assert report.unchecked == [a.id for a in _atoms(6)[2:4]]


def test_wording_generation_stops_at_a_run_wide_error_and_keeps_the_probes_built():
    transport = Calls('["Reworded?"]', error=AUTH)
    built = ProbeSet(probes=[])
    with pytest.raises(SpendCapReached) as stop:
        build_probe_set(_atoms(3), client=_client(transport), batch=1, into=built)
    assert transport.n == 2 and stop.value.reason == "permanent_error"
    assert [probe.kind for probe in built.probes[:3]] == ["direct"] * 3


def test_wording_generation_counts_a_rejected_request_as_rejected_wordings():
    def reply(prompt):
        asked = re.search(r"1\. What is setting (\d+)\?", prompt)
        return f'["Tell me the value of setting {asked[1]}?"]' if asked else "[]"

    transport = Calls(reply, error=TOO_LONG)
    built = build_probe_set(_atoms(3), client=_client(transport), batch=1, reversals=False)
    assert transport.n == 3
    row = built.fidelity["paraphrase"]
    assert (row["generated"], row["kept"], row["rejected"]) == (3, 2, 1)
    assert row["reasons"] == {"empty": 1}
    assert [p.atom_id for p in built.probes if p.kind == "paraphrase"] == [
        a.id for a in _atoms(3) if a.id != _atoms(3)[1].id]


def test_the_retention_oracle_stops_at_a_run_wide_error():
    oracle = RetentionOracle(client=_client(Calls('{"verdict": "retained"}', error=AUTH)),
                             low=-1.0, high=2.0)
    atoms = _atoms(4)
    assert oracle.verdict(atoms[0], "some text").retained
    with pytest.raises(SpendCapReached) as stop:
        oracle.verdict(atoms[1], "some text")
    assert stop.value.reason == "permanent_error" and oracle.errors == 0


def test_the_retention_oracle_counts_a_rejected_request_as_one_error():
    transport = Calls('{"verdict": "retained"}', error=TOO_LONG)
    oracle = RetentionOracle(client=_client(transport), low=-1.0, high=2.0)
    for atom in _atoms(3):
        oracle.verdict(atom, "some text")
    assert transport.n == 3 and oracle.errors == 1


def _probes(n: int) -> list[Probe]:
    return [Probe(atom_id=f"a{i}", kind="direct", question=f"Q{i}?", expected="x")
            for i in range(n)]


_ANSWER = "It is 10.\nconfidence: 0.9"
_GRADE = '{"verdict": "correct_complete"}'


def _measure(answer: Calls, grade: Calls, **kw):
    return run_measurement([], _probes(4), "artifact text", answerer=_client(answer, "a"),
                           judge=_client(grade, "j"), concurrency=1, **kw)


def test_the_measurement_stops_at_a_run_wide_error_in_an_answer():
    answer, grade = Calls(_ANSWER, error=AUTH), Calls(_GRADE)
    result = _measure(answer, grade)  # 4 probes in 2 arms
    assert answer.n == 2 and grade.n == 1
    assert len(result.runs) == 1 and result.not_run == 7 and result.ungraded == 0
    assert result.stop[0] == "permanent_error" and "credentials" in result.stop[1]


def test_the_measurement_records_a_rejected_answer_request_and_goes_on():
    answer, grade = Calls(_ANSWER, error=TOO_LONG), Calls(_GRADE)
    result = _measure(answer, grade)
    assert answer.n == 8 and len(result.runs) == 8 and result.not_run == 0 and result.stop is None
    assert [run.verdict for run in result.runs].count(ERRORED) == 1


def test_the_measurement_stops_at_a_run_wide_error_in_a_grade_and_keeps_the_answer():
    answer, grade = Calls(_ANSWER), Calls(_GRADE, error=AUTH, at=1)
    result = _measure(answer, grade)
    assert answer.n == 1 and grade.n == 1
    assert len(result.runs) == 1 and result.ungraded == 1 and result.not_run == 7
    assert result.runs[0].response == _ANSWER and result.runs[0].verdict == ERRORED
    assert result.stop[0] == "permanent_error"


def test_the_measurement_records_a_rejected_grade_request_and_goes_on():
    answer, grade = Calls(_ANSWER), Calls(_GRADE, error=TOO_LONG, at=1)
    result = _measure(answer, grade)
    assert answer.n == 8 and len(result.runs) == 8 and result.stop is None and result.ungraded == 0
    assert [run.verdict for run in result.runs].count(ERRORED) == 1


def test_a_reply_that_quotes_a_credential_message_is_not_a_provider_error():
    from dokimi.llm import extract_json_array

    replies = iter(["Unable to locate credentials: see the AWS docs.", "[1]", "[2]"])
    transport = Calls(lambda prompt: next(replies))
    result = ask_in_batches(_client(transport), [0, 1], size=2, prompt=str, parse=extract_json_array)
    assert result.stop is None and result.failed == [] and len(result.answered) == 2


def test_a_grade_reply_that_quotes_a_credential_message_is_one_errored_grade():
    answer = Calls(_ANSWER)
    grade = Calls("Unable to locate credentials: see the AWS docs.")
    result = _measure(answer, grade)
    assert result.stop is None and result.not_run == 0 and len(result.runs) == 8
    assert {run.verdict for run in result.runs} == {ERRORED}


def test_a_worker_whose_answer_lands_after_a_run_wide_grade_error_does_not_grade(monkeypatch):
    import threading

    from dokimi.novelty import measure

    stopped = threading.Event()
    first = threading.Lock()

    second_started = threading.Event()

    def answer(prompt, system=None):
        if first.acquire(blocking=False):  # the first worker answers once the second is in flight
            assert second_started.wait(10)
            return _ANSWER, UsageRecord(cost_usd=0.0)
        second_started.set()  # the second worker answers only after the first one's stop is recorded
        assert stopped.wait(10)
        return _ANSWER, UsageRecord(cost_usd=0.0)

    grade = Calls(_GRADE, error=AUTH, at=1)
    capped = measure._capped_grade_run

    def capped_then_signal(*args):
        run = capped(*args)
        stopped.set()
        return run

    monkeypatch.setattr(measure, "_capped_grade_run", capped_then_signal)
    result = run_measurement([], _probes(4), "artifact text", answerer=_client(answer, "a"),
                             judge=_client(grade, "j"), concurrency=2)
    assert grade.n == 1  # the second worker's answer is kept, not graded
    assert len(result.runs) == 2 and result.ungraded == 2 and result.not_run == 6
    assert result.stop[0] == "permanent_error"


def test_analyze_keeps_a_partial_record_when_the_answerer_has_no_credentials(tmp_path):
    from dokimi.novelty.analyze import AnalyzeConfig, analyze, load_artifact_text
    from tests.test_novelty_analyze import SKILL, _clients, _scripted

    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    extractor, _answerer, judge = _clients()
    scripted = _scripted("informed")
    answers = Calls(lambda prompt: scripted(prompt, None)[0], error=AUTH, at=3)
    result = analyze(body, config, extractor=extractor, answerer=_client(answers, "answerer-m"),
                     judge=judge)
    assert answers.n == 3
    assert result.partial["reason"] == "permanent_error" and result.partial["stage"] == "measurement"
    assert result.partial["message"].endswith("permanent error: Unable to locate credentials")
    assert "cause" not in result.partial
    assert result.artifact["status"] == "partial" and len(result.artifact["runs"]) == 2


def test_a_partial_file_of_a_provider_error_is_refused_with_advice_that_helps(tmp_path):
    from dokimi.artifacts import load_artifact
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.files import InputError

    block = {"reason": "permanent_error", "message": "permanent error: bad key"}
    atoms, novelty = tmp_path / "atoms.partial.json", tmp_path / "novelty.partial.json"
    atoms.write_text(json.dumps({"status": "partial", "partial": block}), encoding="utf-8")
    novelty.write_text(json.dumps({"status": "partial", "partial": block}), encoding="utf-8")
    with pytest.raises(InputError, match="permanent error: bad key, so it is not a finished atom set. Fix"):
        load_atoms_record(atoms)
    with pytest.raises(InputError, match="Fix the provider error and rerun") as refused:
        load_artifact("novelty", novelty)
    assert "--max-usd" not in str(refused.value)


def test_the_rerun_hint_of_a_provider_error_does_not_advise_a_higher_cap(monkeypatch, tmp_path):
    from dokimi.caching import RESPONSE_CACHE_ENV
    from dokimi.commands.novelty_budget import replay_hint

    monkeypatch.setenv(RESPONSE_CACHE_ENV, str(tmp_path))
    hint = replay_hint("permanent_error")
    assert "Rerun with the error fixed, and the same" in hint and "--max-usd" not in hint


def test_a_partial_probe_result_of_a_provider_error_is_refused_with_its_message(tmp_path):
    from dokimi.novelty.items import ProbeFileError, load_probe_cells

    def refused(reason, message):
        path = tmp_path / f"{reason}.json"
        path.write_text(json.dumps({"status": "partial", "runs": [],
                                    "partial": {"reason": reason, "message": message}}), encoding="utf-8")
        with pytest.raises(ProbeFileError, match="is a partial probe result") as caught:
            load_probe_cells([str(path)])
        return str(caught.value)

    message = refused("permanent_error", "the probes stopped with 3 of 9 answers: permanent error: bad key")
    assert "permanent error: bad key" in message and "--max-usd" not in message
    assert "--max-usd stopped the measurement" in refused("spend_cap", "--max-usd stopped the probes")

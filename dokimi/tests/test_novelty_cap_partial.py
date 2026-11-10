# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run that --max-usd stops keeps the work it paid for, in a partial file beside the output.

atoms and probe write ``<out stem>.partial.json`` (never over a finished file) and exit; the
readers refuse a partial file. analyze names the estimated cost before it spends.
"""

from __future__ import annotations

import importlib
import json
import os
import re

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.client import LLMClient, SpendCap
from dokimi.utils import InputError
from tests.cli_paths import novelty_argv

ROWS = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 201)) + "\n"


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _one_atom_per_span(prompt, system=None):
    """The extractor gives each span one atom, and each call costs ``0.6`` USD."""
    rows = [{"statement": f"{sid} holds a default.", "question": f"What of {sid}?",
             "answer": "a value", "kind": "proposition", "tags": [], "span_ids": [sid]}
            for sid in re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)]
    return json.dumps(rows), UsageRecord(cost_usd=0.6)


def _paid_extractor(cap: SpendCap, calls: list) -> LLMClient:
    def transport(prompt, system=None):
        calls.append(prompt)
        return _one_atom_per_span(prompt, system)

    return LLMClient(model="e", role="extractor", transport=transport, cap=cap)


def test_extraction_stops_at_the_cap_and_marks_the_unread_spans():
    from dokimi.novelty.atoms import ExtractionReport, extract_atoms
    from dokimi.novelty.spans import segment

    spans = segment(ROWS)
    calls: list = []
    report = ExtractionReport()
    # Two 0.6 USD calls fit under 1.00; the third is refused before it reaches the provider.
    atoms = extract_atoms(spans, client=_paid_extractor(SpendCap(limit_usd=1.0), calls),
                          report=report)
    assert len(calls) == 2
    assert len(atoms) == 50
    assert (report.partial["stage"], report.partial["spans_read"]) == ("extraction", 50)
    assert report.partial["message"] == "--max-usd stopped extraction with 50 of 200 spans read"
    assert not any(span.unread for span in spans[:50])
    assert all(span.unread for span in spans[50:])


def test_the_atoms_command_saves_a_partial_file_and_exits_without_atoms_json(tmp_path, monkeypatch):
    artifact = tmp_path / "rows.md"
    artifact.write_text(ROWS, encoding="utf-8")
    cap = SpendCap(limit_usd=1.0)
    calls: list = []
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args:
                        _paid_extractor(cap, calls))
    out = tmp_path / "atoms.json"
    args = _parse(*novelty_argv("atoms"), "--artifact", str(artifact), "--model", "e",
                                "--out", str(out), "--max-usd", "1")
    partial = tmp_path / "atoms.partial.json"
    with pytest.raises(SystemExit) as exit_info:
        args.func(args)
    assert f"the partial result is in {partial}" in str(exit_info.value)
    assert not out.exists()
    record = json.loads(partial.read_text(encoding="utf-8"))
    assert record["partial"]["reason"] == "spend_cap"
    assert (record["partial"]["spans_read"], record["partial"]["spans_total"]) == (50, 200)
    assert record["status"] == "partial"
    # The readers that take an atoms file refuse a partial one, naming it.
    from dokimi.novelty.atoms import load_atoms_record

    with pytest.raises(InputError, match="partial"):
        load_atoms_record(partial)


def test_the_atoms_command_warns_about_an_empty_reply_the_cap_did_not_cause(tmp_path, monkeypatch,
                                                                             capsys):
    # The first call is empty and spends past the cap: the warning names the empty reply, and
    # not the spans the cap left unread, which the exit names.
    artifact = tmp_path / "rows.md"
    artifact.write_text(ROWS, encoding="utf-8")

    def empty_and_paid(prompt, system=None):
        return "", UsageRecord(cost_usd=0.6)

    client = LLMClient(model="e", role="extractor", transport=empty_and_paid,
                       cap=SpendCap(limit_usd=0.5))
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args: client)
    args = _parse(*novelty_argv("atoms"), "--artifact", str(artifact), "--model", "e",
                  "--out", str(tmp_path / "atoms.json"), "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    err = capsys.readouterr().err
    assert "WARNING atom extraction could not read" in err
    assert "got an empty reply" in err
    assert "could not read 200 of" not in err


def test_a_partial_result_never_replaces_a_finished_one(tmp_path):
    from dokimi.commands.novelty_budget import partial_destination

    finished = tmp_path / "probes.json"
    finished.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    assert partial_destination(finished) == tmp_path / "probes.partial.json"
    (tmp_path / "probes.partial.json").write_text(json.dumps({"partial": {}}), encoding="utf-8")
    assert partial_destination(finished) == tmp_path / "probes.partial.json"  # already partial
    (tmp_path / "probes.partial.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    assert partial_destination(finished) == tmp_path / "probes.partial2.json"


def test_run_measurement_keeps_the_answers_paid_for_and_counts_the_rest(monkeypatch):
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    def answer(question, context, answerer, **kwargs):
        answerer.text(question)  # a paid call, so the cap is spent on the probe's answer
        return "an answer", None

    monkeypatch.setattr(measure, "_answer", answer)
    monkeypatch.setattr(measure, "_grade", lambda probe, response, judge: "correct_complete")
    cap = SpendCap(limit_usd=1.0)
    paid = LLMClient(model="a", role="answerer", cap=cap, transport=lambda p, system=None:
                     ("x", UsageRecord(cost_usd=0.6)))
    probes = [Probe(atom_id=f"a{i}", kind="proposition", question=f"q{i}", expected="x")
              for i in range(3)]
    result = measure.run_measurement([], probes, "artifact", answerer=paid,
                                     judge=LLMClient(model="j", role="judge"),
                                     arms=("no_artifact", "full"), concurrency=1)
    assert len(result.runs) == 2 and result.not_run == 4


def test_the_probe_command_saves_the_partial_measurement_and_exits(tmp_path, monkeypatch):
    from dokimi.novelty import measure

    artifact = tmp_path / "rows.md"
    artifact.write_text(ROWS, encoding="utf-8")
    atoms = tmp_path / "atoms.json"
    calls: list = []
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args:
                        _paid_extractor(SpendCap(limit_usd=100.0), calls))
    args = _parse(*novelty_argv("atoms"), "--artifact", str(artifact), "--model", "e",
                                "--out", str(atoms))
    args.func(args)  # a finished atoms file to probe, with no cap
    real = measure.run_measurement

    def capped(*a, **kw):
        result = real(*a, **kw)
        result.runs, result.not_run = result.runs[:2], 4
        return result

    monkeypatch.setattr(measure, "run_measurement", capped)
    monkeypatch.setattr(novelty_cmd, "_clients", lambda args: (
        LLMClient(model="e", role="extractor", transport=lambda p, system=None: ("[]", None)),
        LLMClient(model="a", role="answerer", transport=lambda p, system=None: ("x", None)),
        LLMClient(model="j", role="judge", transport=lambda p, system=None: ("x", None)),
    ))
    out = tmp_path / "probes.json"
    args = _parse(*novelty_argv("probe"), "--artifact", str(artifact), "--atoms", str(atoms),
                                "--out", str(out), "--max-usd", "1")
    with pytest.raises(SystemExit) as exit_info:
        args.func(args)
    assert f"the partial result is in {tmp_path / 'probes.partial.json'}" in str(exit_info.value)
    assert not out.exists()
    record = json.loads((tmp_path / "probes.partial.json").read_text(encoding="utf-8"))
    assert record["status"] == "partial"
    assert (record["partial"]["calls_done"], record["partial"]["calls_planned"]) == (2, 6)


def test_analyze_names_the_run_cost_before_it_spends(tmp_path, monkeypatch, capsys):
    class Spent(Exception):
        pass

    def stop_before_any_call(*args, **kwargs):
        raise Spent

    # The package re-exports the function under the module's name, so reach the module itself.
    monkeypatch.setattr(importlib.import_module("dokimi.novelty.analyze"), "analyze",
                        stop_before_any_call)
    monkeypatch.setattr(novelty_cmd, "_estimate_usd", lambda *a, **kw: 0.42)
    artifact = tmp_path / "rows.md"
    artifact.write_text(ROWS, encoding="utf-8")
    args = _parse(*novelty_argv("analyze"), "--artifact", str(artifact), "--model", "m",
                                "--tier", "triage", "--out-dir", str(tmp_path / "run"))
    with pytest.raises(Spent):
        args.func(args)
    err = capsys.readouterr().err
    assert "Expected ~$0.42 for this run at list prices (--tier triage)" in err


def test_an_unpriced_model_prints_no_run_estimate_and_does_not_raise(monkeypatch, capsys):
    budget = importlib.import_module("dokimi.commands.novelty_budget")
    monkeypatch.setattr(budget, "run_estimate", lambda *a, **kw: ({"expected_usd": None}, 10))
    budget.print_run_estimate(None, None, "text", lambda *a, **kw: None)
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""


def test_an_unpriced_answer_stops_every_queued_probe_not_only_the_one_that_raised():
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    calls: list = []

    def unpriced(prompt, system=None):
        calls.append(prompt)
        return "an answer", None  # no usage, so the cap cannot see what the call cost

    answerer = LLMClient(model="a", role="answerer", cap=SpendCap(limit_usd=1.0),
                         transport=unpriced)
    probes = [Probe(atom_id=f"a{i}", kind="proposition", question=f"q{i}", expected="x")
              for i in range(3)]
    result = measure.run_measurement([], probes, "artifact", answerer=answerer,
                                     judge=LLMClient(model="j", role="judge"),
                                     arms=("no_artifact", "full"), concurrency=1)
    # One call reached the provider and was paid for: its answer is kept, ungraded. The five jobs
    # behind it were never sent.
    assert len(calls) == 1
    assert len(result.runs) == 1 and result.ungraded == 1
    assert result.runs[0].response == "an answer" and result.not_run == 5


def test_a_whitespace_only_unpriced_reply_is_no_answer():
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    def blank(prompt, system=None):
        return "  \n", None  # paid nothing visible and said nothing

    answerer = LLMClient(model="a", role="answerer", cap=SpendCap(limit_usd=1.0), transport=blank)
    probes = [Probe(atom_id=f"a{i}", kind="proposition", question=f"q{i}", expected="x")
              for i in range(3)]
    result = measure.run_measurement([], probes, "artifact", answerer=answerer,
                                     judge=LLMClient(model="j", role="judge"),
                                     arms=("no_artifact", "full"), concurrency=1)
    assert result.runs == [] and result.ungraded == 0 and result.not_run == 6


def test_an_unpriced_reply_cut_at_the_ceiling_is_stored_as_an_error_not_as_an_answer():
    from dokimi.metrics.usage import UsageRecord
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    def cut(prompt, system=None):
        # no price, and the reply hit the output ceiling: a prefix, so no answer
        usage = UsageRecord(completion_tokens=900, truncated=True)
        return "The first half of an answer that", usage

    answerer = LLMClient(model="a", role="answerer", cap=SpendCap(limit_usd=1.0), transport=cut)
    probes = [Probe(atom_id=f"a{i}", kind="proposition", question=f"q{i}", expected="x")
              for i in range(3)]
    result = measure.run_measurement([], probes, "artifact", answerer=answerer,
                                     judge=LLMClient(model="j", role="judge"),
                                     arms=("no_artifact", "full"), concurrency=1)
    # The priced path stores a cut reply as the error text, so the cut prefix is not an answer.
    assert len(result.runs) == 1 and result.ungraded == 1
    assert result.runs[0].response.startswith("<error: reply cut at the output ceiling")
    assert "The first half" not in result.runs[0].response
    assert result.not_run == 5


def test_extraction_that_loses_its_first_call_to_the_cap_keeps_a_partial_result():
    from dokimi.novelty.atoms import ExtractionReport, extract_atoms
    from dokimi.novelty.spans import segment

    spans = segment(ROWS)
    calls: list = []

    def empty_then_nothing(prompt, system=None):
        calls.append(prompt)
        return "", UsageRecord(cost_usd=0.6)  # an empty reply that still costs

    report = ExtractionReport()
    # The first call is lost and spends 0.6 USD; the cap of 0.5 then stops the second call.
    atoms = extract_atoms(spans, client=LLMClient(model="e", role="extractor",
                          transport=empty_then_nothing, cap=SpendCap(limit_usd=0.5)),
                          report=report)
    assert atoms == [] and len(calls) == 1
    assert report.partial["spans_read"] == 0
    assert report.partial["message"] == "--max-usd stopped extraction with 0 of 200 spans read"
    assert all(span.unread for span in spans)
    # The empty reply before the cap is a cause the user can act on; the spans the cap left
    # unread are the cap's doing, which the exit message names, so they are not counted again.
    warnings = report.warnings(len(spans))
    assert any("got an empty reply" in warning for warning in warnings)
    assert not any("could not read 200 of" in warning for warning in warnings)


def test_the_faithfulness_check_counts_the_spans_extraction_left_unread(monkeypatch):
    from dokimi.commands.novelty_budget import verify_or_partial
    from dokimi.novelty import atoms as atoms_mod
    from dokimi.novelty.client import SpendCapReached
    from dokimi.novelty.spans import segment

    spans = segment(ROWS)[:75]
    for span in spans[:25]:
        span.unread = True  # the first batch of 25 spans was never read

    def capped(sample, spans, client):
        raise SpendCapReached("cap")

    monkeypatch.setattr(atoms_mod, "verify_atoms", capped)
    partial = verify_or_partial([], spans, client=None)
    assert (partial["spans_read"], partial["spans_total"]) == (50, 75)
    assert partial["message"] == ("--max-usd stopped the faithfulness check with 50 of 75 "
                                  "spans read")


@pytest.mark.parametrize("not_run", [1, 3])
def test_analyze_stops_rather_than_publish_a_measurement_the_cap_cut_short(tmp_path, monkeypatch,
                                                                          not_run):
    from dokimi.novelty import measure
    from dokimi.novelty.analyze import AnalyzeConfig, analyze, load_artifact_text
    from dokimi.novelty.client import SpendCapReached

    from tests.test_novelty_analyze import SKILL, _clients

    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(
        artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
        description=description, frontmatter=frontmatter, concurrency=1,
    )
    real = measure.run_measurement

    def cut_short(*a, **kw):
        result = real(*a, **kw)
        result.runs, result.not_run = result.runs[:1], not_run
        return result

    monkeypatch.setattr(measure, "run_measurement", cut_short)
    extractor, answerer, judge = _clients()
    # The match is a prefix: the CLI adds the rest of the exit message.
    with pytest.raises(SpendCapReached,
                       match=f"{not_run} probe answers were not run or graded"):
        analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)


def test_novelty_items_refuses_a_partial_probe_file_by_name(tmp_path):
    from dokimi.novelty.items import ProbeFileError, load_probe_cells

    partial = tmp_path / "probes.partial.json"
    partial.write_text(json.dumps({"partial": {"reason": "spend_cap"}, "runs": []}),
                       encoding="utf-8")
    with pytest.raises(ProbeFileError, match="partial probe result") as refused:
        load_probe_cells([str(partial)])
    assert str(partial) in str(refused.value)


def test_an_earlier_file_that_is_not_json_is_kept_and_not_taken_for_a_partial_one(tmp_path):
    from dokimi.commands.novelty_budget import partial_destination

    finished = tmp_path / "probes.json"
    (tmp_path / "probes.partial.json").write_bytes(b"\xff\xfe not json")
    # A file this cannot read is not a partial result, so it is never written over.
    assert partial_destination(finished) == tmp_path / "probes.partial2.json"


def test_an_unpriced_probe_stop_names_the_missing_price_not_max_usd(tmp_path):
    from dokimi.commands.novelty_budget import save_probe_run
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    def unpriced(prompt, system=None):
        return "an answer", None  # no usage, so no price to meter it with

    answerer = LLMClient(model="a", role="answerer", cap=SpendCap(limit_usd=1.0),
                         transport=unpriced)
    probes = [Probe(atom_id=f"a{i}", kind="proposition", question=f"q{i}", expected="x")
              for i in range(3)]
    result = measure.run_measurement([], probes, "artifact", answerer=answerer,
                                     judge=LLMClient(model="j", role="judge"),
                                     arms=("no_artifact", "full"), concurrency=1)
    out = tmp_path / "probes.json"
    with pytest.raises(SystemExit):
        save_probe_run({"runs": []}, out, done=len(result.runs), not_run=result.not_run,
                       stop=result.stop, ungraded=result.ungraded)
    partial = json.loads((tmp_path / "probes.partial.json").read_text(encoding="utf-8"))["partial"]
    assert partial["reason"] == "no_price"
    assert partial["message"].startswith(
        "the probes stopped with 1 of 6 answers; 1 of them not graded: ")
    assert "no price is known for a" in partial["message"]


def test_an_unpriced_extraction_stop_names_the_missing_price_not_max_usd():
    from dokimi.novelty.atoms import ExtractionReport, extract_atoms
    from dokimi.novelty.spans import segment

    report = ExtractionReport()
    extract_atoms(segment(ROWS), client=LLMClient(
        model="e", role="extractor", cap=SpendCap(limit_usd=1.0),
        transport=lambda prompt, system=None: ("[]", None)), report=report)
    assert report.partial["reason"] == "no_price"
    assert "no price is known for e" in report.partial["message"]
    assert "--max-usd stopped" not in report.partial["message"]


def test_an_unpriced_extraction_call_keeps_the_atoms_its_reply_carried():
    from dokimi.novelty.atoms import ExtractionReport, extract_atoms
    from dokimi.novelty.spans import segment

    spans = segment(ROWS)
    report = ExtractionReport()
    # The first batch comes back with usable atoms but no price: the run stops after that call,
    # and the paid-for reply is kept, not marked unread with the rest of the queue.
    atoms = extract_atoms(spans, client=LLMClient(
        model="e", role="extractor", cap=SpendCap(limit_usd=1.0),
        transport=lambda prompt, system=None: (_one_atom_per_span(prompt)[0], UsageRecord())),
        batch_spans=25, report=report)
    assert report.partial["reason"] == "no_price"
    assert len(atoms) == 25
    assert report.partial["spans_read"] == 25
    assert not any(span.unread for span in spans[:25])
    assert all(span.unread for span in spans[25:])


def test_an_unpriced_reply_that_fails_part_way_keeps_no_atoms_and_its_batch_stays_unread():
    from dokimi.novelty.atoms import ExtractionReport, extract_atoms
    from dokimi.novelty.spans import segment

    spans = segment(ROWS)
    report = ExtractionReport()
    good = [{"statement": "a default", "question": "what default?", "span_ids": [spans[0].id]}]
    # The second row is malformed (its span_ids is not a list): the reply is not usable as a whole.
    bad = [{"statement": "s", "question": "q", "span_ids": 5}]
    atoms = extract_atoms(spans, client=LLMClient(
        model="e", role="extractor", cap=SpendCap(limit_usd=1.0),
        transport=lambda prompt, system=None: (json.dumps(good + bad), UsageRecord())),
        batch_spans=25, report=report)
    assert atoms == []
    assert report.partial["reason"] == "no_price"
    assert all(span.unread for span in spans[:25])


def test_analyze_names_a_missing_price_when_the_measurement_stops_on_one(tmp_path, monkeypatch):
    from dokimi.novelty import measure
    from dokimi.novelty.analyze import AnalyzeConfig, analyze, load_artifact_text
    from dokimi.novelty.client import SpendCapReached

    from tests.test_novelty_analyze import SKILL, _clients

    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(
        artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
        description=description, frontmatter=frontmatter, concurrency=1,
    )
    real = measure.run_measurement

    def stopped_on_a_price(*a, **kw):
        result = real(*a, **kw)
        result.runs, result.not_run = result.runs[:1], 2
        result.stop = ("no_price", "no price is known for judge, so --max-usd cannot be enforced")
        return result

    monkeypatch.setattr(measure, "run_measurement", stopped_on_a_price)
    extractor, answerer, judge = _clients()
    with pytest.raises(SpendCapReached) as refused:
        analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)
    assert refused.value.reason == "no_price"
    assert "Raise --max-usd" not in str(refused.value)
    assert "no price is known for judge" in str(refused.value)


def test_an_unpriced_partial_atoms_file_is_refused_with_its_cause(tmp_path):
    from dokimi.novelty.atoms import load_atoms_record

    partial = tmp_path / "atoms.partial.json"
    partial.write_text(json.dumps({"status": "partial", "partial": {
        "reason": "no_price", "stage": "extraction", "spans_read": 0, "spans_total": 3,
        "message": "extraction stopped with 0 of 3 spans read: no price is known for e"}}),
        encoding="utf-8")
    with pytest.raises(InputError, match="no price is known for e, so it is not") as refused:
        load_atoms_record(partial)
    assert "--max-usd stopped" not in str(refused.value)


def test_an_answer_the_cap_paid_for_is_kept_when_its_grade_is_refused(monkeypatch):
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    def answer(question, context, answerer, **kwargs):
        answerer.text(question)  # $0.60 against a $0.50 cap: the answer is paid for
        return "an answer", None

    monkeypatch.setattr(measure, "_answer", answer)
    cap = SpendCap(limit_usd=0.5)
    paid = LLMClient(model="a", role="answerer", cap=cap, transport=lambda p, system=None:
                     ("x", UsageRecord(cost_usd=0.6)))
    # The judge shares the cap, so its call is refused before it reaches the transport.
    judge = LLMClient(model="j", role="judge", cap=cap, transport=lambda p, system=None:
                      ('{"verdict": "correct_complete"}', UsageRecord(cost_usd=0.01)))
    probes = [Probe(atom_id=f"a{i}", kind="proposition", question=f"q{i}", expected="x")
              for i in range(3)]
    result = measure.run_measurement([], probes, "artifact", answerer=paid, judge=judge,
                                     arms=("no_artifact", "full"), concurrency=1)
    assert len(result.runs) == 1 and result.runs[0].response == "an answer"
    assert result.runs[0].verdict == measure.ERRORED
    assert result.not_run == 5 and result.ungraded == 1
    assert result.stop[0] == "spend_cap"


def test_an_answer_whose_grade_the_cap_refused_makes_the_probe_file_partial(tmp_path):
    from dokimi.commands.novelty_budget import save_probe_run

    out = tmp_path / "probes.json"
    with pytest.raises(SystemExit, match="1 of them not graded"):
        save_probe_run({"runs": []}, out, done=1, not_run=0, ungraded=1)
    partial = json.loads((tmp_path / "probes.partial.json").read_text(encoding="utf-8"))["partial"]
    assert partial["calls_done"] == 1 and partial["calls_planned"] == 1
    assert not out.exists()


def test_a_judge_reply_the_cap_refused_after_it_came_back_keeps_its_verdict(monkeypatch):
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    monkeypatch.setattr(measure, "_answer", lambda question, context, answerer, **kwargs:
                        ("an answer", None))
    # The judge is unpriced: its call goes through and comes back, then the cap refuses it.
    judge = LLMClient(model="j", role="judge", cap=SpendCap(limit_usd=1.0), transport=lambda p,
                      system=None: ('{"verdict": "correct_complete"}', UsageRecord()))
    probes = [Probe(atom_id="a0", kind="proposition", question="q0", expected="x")]
    result = measure.run_measurement([], probes, "artifact", answerer=LLMClient(model="a",
                                     role="answerer"), judge=judge, arms=("full",), concurrency=1)
    assert [run.verdict for run in result.runs] == ["correct_complete"]
    assert result.ungraded == 0 and result.not_run == 0
    assert result.stop[0] == "no_price"


def test_a_judge_reply_the_cap_refused_that_names_no_verdict_stays_ungraded(monkeypatch):
    from dokimi.novelty import measure
    from dokimi.novelty.probes import Probe

    monkeypatch.setattr(measure, "_answer", lambda question, context, answerer, **kwargs:
                        ("an answer", None))
    judge = LLMClient(model="j", role="judge", cap=SpendCap(limit_usd=1.0), transport=lambda p,
                      system=None: ("not json", UsageRecord()))
    probes = [Probe(atom_id="a0", kind="proposition", question="q0", expected="x")]
    result = measure.run_measurement([], probes, "artifact", answerer=LLMClient(model="a",
                                     role="answerer"), judge=judge, arms=("full",), concurrency=1)
    assert [run.verdict for run in result.runs] == ["error"]
    assert result.ungraded == 1 and result.stop[0] == "no_price"


def test_a_partial_write_skips_a_dangling_symlink_and_a_read_only_partial_file(tmp_path):
    # A symlink at the sidecar name is not followed, and a read-only partial file is passed over
    # for the next name: the routing sidecar rules (#516, #521), which the budget writers share.
    from dokimi.commands.novelty_budget import write_run

    target = tmp_path / "elsewhere.json"
    (tmp_path / "probes.partial.json").symlink_to(target)
    partial = {"reason": "spend_cap", "calls_done": 1, "calls_planned": 2, "message": "m"}
    dest = write_run({"runs": []}, tmp_path / "probes.json", partial)
    assert dest == tmp_path / "probes.partial2.json"
    assert not target.exists()

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root writes a read-only file regardless of its mode")
    kept = tmp_path / "earlier.partial.json"
    kept.write_text(json.dumps({"partial": {"reason": "spend_cap"}}), encoding="utf-8")
    kept.chmod(0o444)
    try:
        second = write_run({"runs": []}, tmp_path / "earlier.json", partial)
    finally:
        kept.chmod(0o644)
    assert second == tmp_path / "earlier.partial2.json"
    assert json.loads(kept.read_text(encoding="utf-8")) == {"partial": {"reason": "spend_cap"}}
    assert json.loads(second.read_text(encoding="utf-8"))["partial"]["message"] == "m"


def test_a_partial_probe_file_drops_the_summaries_computed_over_the_cut_subset(tmp_path):
    # Jobs run arm by arm, so a cut leaves the arms uneven: the summaries over the answers that were
    # kept are not results, and the partial file keeps the runs only.
    from dokimi.commands.novelty_budget import save_probe_run

    payload = {"runs": [{"verdict": "correct"}], "paired": {"lenient": {"n_paired": 1}},
               "arms": {"lenient": {"full": 1.0}}, "abstentions": {"n": 0},
               "reversal_paired": {"n": 1}, "probes": {"n": 3},
               # the second judge shares the run's cap, so it is refused once the cap stops the run
               "judge2": {"model": "j2", "stopped": "spend cap reached", "cost": 0.0}}
    out = tmp_path / "probes.json"
    with pytest.raises(SystemExit):
        save_probe_run(payload, out, done=1, not_run=5, stop=("spend_cap", "cap"))
    saved = json.loads((tmp_path / "probes.partial.json").read_text(encoding="utf-8"))
    assert saved["runs"] == [{"verdict": "correct"}] and saved["probes"] == {"n": 3}
    for block in ("paired", "arms", "abstentions", "reversal_paired", "judge2"):
        assert saved[block] is None


@pytest.mark.parametrize("reason", ["spend_cap", "no_price"])
def test_a_cut_analyze_names_the_cap_once_and_keeps_its_figures(reason):
    from dokimi.commands.novelty_run import stops_at_cap
    from dokimi.novelty import measure
    from dokimi.novelty.client import SpendCapReached

    if reason == "spend_cap":
        # The message the cap itself raises, built by the cap, not written out by hand.
        cap = SpendCap(limit_usd=1.0, spent_usd=1.0, calls=4)
        with pytest.raises(SpendCapReached) as cut:
            cap.before_call("model-x")
        cause = str(cut.value)
    else:
        cause = "no price is known for a, so --max-usd cannot be enforced"

    analyze = importlib.import_module("dokimi.novelty.analyze")
    result = measure.MeasureResult(runs=[], arms=("full",), with_arm="full", not_run=3,
                                   ungraded=0, stop=(reason, cause), probes=[])

    @stops_at_cap
    def command(args):
        analyze._refuse_cut_short(result)

    with pytest.raises(SystemExit) as info:
        command(None)
    message = str(info.value)
    assert message.count("nothing was written") == 1
    assert message.count("Raise --max-usd") == (1 if reason == "spend_cap" else 0)
    assert cause in message

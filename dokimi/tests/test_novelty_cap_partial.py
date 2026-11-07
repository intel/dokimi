# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run that --max-usd stops keeps the work it paid for, in a partial file beside the output.

atoms and probe write ``<out stem>.partial.json`` (never over a finished file) and exit; the
readers refuse a partial file. analyze names the estimated cost before it spends.
"""

from __future__ import annotations

import importlib
import json
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
    with pytest.raises(SystemExit, match="partial"):
        args.func(args)
    assert not out.exists()
    partial = tmp_path / "atoms.partial.json"
    record = json.loads(partial.read_text(encoding="utf-8"))
    assert record["partial"]["reason"] == "spend_cap"
    assert (record["partial"]["spans_read"], record["partial"]["spans_total"]) == (50, 200)
    assert record["status"] == "partial"
    # The readers that take an atoms file refuse a partial one, naming it.
    from dokimi.novelty.atoms import load_atoms_record

    with pytest.raises(InputError, match="partial"):
        load_atoms_record(partial)


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
    with pytest.raises(SystemExit, match="partial"):
        args.func(args)
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
    # One call reached the provider; the six jobs behind it were never sent.
    assert len(calls) == 1
    assert result.runs == [] and result.not_run == 6


def test_analyze_stops_rather_than_publish_a_measurement_the_cap_cut_short(tmp_path, monkeypatch):
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
        result.runs, result.not_run = result.runs[:1], 3
        return result

    monkeypatch.setattr(measure, "run_measurement", cut_short)
    extractor, answerer, judge = _clients()
    with pytest.raises(SpendCapReached, match="3 probe answers were not run; nothing was written"):
        analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)


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
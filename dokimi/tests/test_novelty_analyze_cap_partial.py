# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`skill novelty analyze` stopped by --max-usd keeps what it paid for, beside the finished file.

The cap is hit in extraction, in the faithfulness check, in the measurement and in the paid
classify stage; each leaves ``<out stem>.partial.json`` with ``status: partial``, the extracted
atoms and, once measured, the probe answers. A finished ``novelty.json`` is never replaced, and
every reader of one refuses the partial file.
"""

from __future__ import annotations

import json
import re

import pytest

from dokimi.artifacts import load_artifact, validate_artifact
from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.analyze import AnalyzeConfig, analyze, load_artifact_text
from dokimi.novelty.client import LLMClient, SpendCap
from dokimi.utils import InputError
from tests.cli_paths import novelty_argv
from tests.test_novelty_analyze import SKILL, _scripted

ROWS = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 201)) + "\n"


def _priced(cost_of):
    """The scripted model of ``test_novelty_analyze``, each reply billed ``cost_of(prompt)``."""
    base = _scripted("informed")

    def transport(prompt, system=None):
        reply, _usage = base(prompt, system)
        return reply, UsageRecord(cost_usd=cost_of(prompt))

    return transport


def _clients(cost_of, limit=1.0):
    cap = SpendCap(limit_usd=limit)
    transport = _priced(cost_of)
    return tuple(LLMClient(model=f"{role}-m", role=role, transport=transport, cap=cap)
                 for role in ("extractor", "answerer", "judge"))


def _skill(tmp_path):
    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SKILL, encoding="utf-8")
    return path


def _run(tmp_path, cost_of, limit=1.0):
    path = _skill(tmp_path)
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    extractor, answerer, judge = _clients(cost_of, limit)
    return analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)


def _free(prompt):
    return 0.0


def _answers_cost(prompt):
    # Each probe answer is 0.6 USD under a 1.00 cap: two fit, the third is refused.
    return 0.6 if prompt.startswith(("Reference material:", "Question:")) else 0.0


def _after(marker):
    """The call that carries ``marker`` spends the whole cap; the next call, whoever's, is refused."""
    return lambda prompt: 100.0 if marker in prompt else 0.0


def test_a_finished_analyze_has_no_partial_marker(tmp_path):
    result = _run(tmp_path, _free)
    assert result.partial is None
    assert "status" not in result.artifact and "partial" not in result.artifact


def test_a_record_without_status_still_needs_its_summaries(tmp_path):
    from dokimi.artifacts import ArtifactValidationError

    record = dict(_run(tmp_path, _answers_cost).artifact)
    del record["status"], record["partial"]
    with pytest.raises(ArtifactValidationError, match="'paired' is a required property"):
        validate_artifact("novelty", record)


def test_a_cap_in_measurement_keeps_the_atoms_and_the_answers_paid_for(tmp_path):
    result = _run(tmp_path, _answers_cost)
    record = result.artifact
    assert record["status"] == "partial"
    assert record["partial"]["stage"] == "measurement" and record["partial"]["reason"] == "spend_cap"
    assert record["partial"]["calls_done"] == 2 and record["partial"]["calls_planned"] > 2
    # The cap spent on the second answer, so the judge's call for it was refused: kept ungraded.
    assert re.fullmatch(r"--max-usd stopped the probes with 2 of \d+ answers; 1 of them not graded",
                        record["partial"]["message"])
    assert "spend cap reached" in record["partial"]["cause"]
    assert len(record["extracted_atoms"]) == 2 and record["atoms"]["n"] == 2
    assert len(record["runs"]) == 2 and all(run["verdict"] for run in record["runs"])
    assert record["probes"]["n_probes"] > 0 and record["source_spans"]
    # Nothing summarises the uneven arms: no paired figures, no views.
    assert not {"paired", "k0", "arms", "views", "shortening"} & set(record)
    validate_artifact("novelty", record)


def test_a_cap_after_the_wordings_keeps_every_probe_before_the_first_answer(tmp_path):
    record = _run(tmp_path, _after("Rewrite each statement below")).artifact
    assert record["partial"]["stage"] == "measurement" and record["partial"]["calls_done"] == 0
    assert record["runs"] == []
    items = record["probes"]["items"]
    assert len(items) == record["probes"]["n_probes"] > 0
    assert all(item["question"] and item["expected"] and item["atom_id"] for item in items)
    assert {"paraphrase", "reversal"} <= {item["kind"] for item in items}
    validate_artifact("novelty", record)


def test_a_cap_between_the_two_wording_calls_keeps_the_paraphrases_already_paid_for(tmp_path):
    record = _run(tmp_path, _after("Reword each question")).artifact
    assert record["partial"]["stage"] == "probes" and record["partial"]["reason"] == "spend_cap"
    items = record["probes"]["items"]
    kinds = {item["kind"] for item in items}
    assert kinds == {"direct", "paraphrase"}
    assert len(items) == record["probes"]["n_probes"]
    assert record["probes"]["by_kind"]["paraphrase"]["kept"] > 0
    assert "reversal" not in record["probes"]["by_kind"]
    validate_artifact("novelty", record)


def test_a_cap_in_the_paid_classify_stage_keeps_the_complete_measurement(tmp_path):
    result = _run(tmp_path, _after("judge how it functions"))
    partial = result.artifact["partial"]
    assert partial["stage"] == "classify"
    assert partial["calls_done"] == partial["calls_planned"] == len(result.artifact["runs"]) > 0
    validate_artifact("novelty", result.artifact)


def test_a_cap_in_the_faithfulness_check_keeps_the_extracted_atoms(tmp_path):
    result = _run(tmp_path, _after("Does the source text below support"))
    record = result.artifact
    assert record["partial"]["stage"] == "verify"
    assert len(record["extracted_atoms"]) == 2 and "runs" not in record and "probes" not in record
    validate_artifact("novelty", record)


def test_a_cap_in_extraction_keeps_the_atoms_read_and_marks_the_rest_unread():
    def one_atom_per_span(prompt, system=None):
        rows = [{"statement": f"{sid} holds a default.", "question": f"What of {sid}?",
                 "answer": "a value", "kind": "proposition", "tags": [], "span_ids": [sid]}
                for sid in re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)]
        return json.dumps(rows), UsageRecord(cost_usd=0.6)

    cap = SpendCap(limit_usd=1.0)
    extractor = LLMClient(model="e", role="extractor", transport=one_atom_per_span, cap=cap)
    idle = LLMClient(model="a", role="answerer", transport=one_atom_per_span, cap=cap)
    judge = LLMClient(model="j", role="judge", transport=one_atom_per_span, cap=cap)
    config = AnalyzeConfig(artifact_ref="text:rows", artifact_kind="text", concurrency=1)
    record = analyze(ROWS, config, extractor=extractor, answerer=idle, judge=judge).artifact
    assert record["partial"]["stage"] == "extraction"
    assert (record["partial"]["spans_read"], record["partial"]["spans_total"]) == (50, 200)
    assert len(record["extracted_atoms"]) == 50 and "runs" not in record
    assert len(record["extraction"]["unread_span_ids"]) == 150
    assert len(record["source_spans"]) == 200
    validate_artifact("novelty", record)


def test_a_call_with_no_price_stops_analyze_and_is_named_as_such(tmp_path):
    result = _run(tmp_path, lambda prompt: None)
    partial = result.artifact["partial"]
    assert partial["reason"] == "no_price" and "cause" not in partial
    assert "no price is known" in partial["message"]
    validate_artifact("novelty", result.artifact)


# --- the command ---------------------------------------------------------------------------

def _command(tmp_path, monkeypatch, cost_of, *extra):
    path = _skill(tmp_path)
    clients = _clients(cost_of)
    monkeypatch.setattr(novelty_cmd, "_clients", lambda args: clients)
    out_dir = tmp_path / "run"
    args = build_parser().parse_args([*novelty_argv("analyze"), "--artifact", f"skill:{path}",
                                      "--model", "m", "--out-dir", str(out_dir), "--max-usd", "1",
                                      "--concurrency", "1", *extra])
    return args, out_dir


def test_the_command_writes_the_partial_file_beside_novelty_json_and_names_it(tmp_path, monkeypatch):
    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)
    args, out_dir = _command(tmp_path, monkeypatch, _answers_cost)
    with pytest.raises(SystemExit) as stop:
        args.func(args)
    message = str(stop.value)
    partial = out_dir / "novelty.partial.json"
    assert f"the partial result is in {partial}" in message
    assert message.startswith("--max-usd stopped the probes with 2 of ")
    assert "spend cap reached" in message  # what was spent, from the cap itself
    assert "DOKIMI_RESPONSE_CACHE was not set on this run" in message
    assert sorted(path.name for path in out_dir.iterdir()) == ["novelty.partial.json"]
    record = json.loads(partial.read_text(encoding="utf-8"))
    assert record["status"] == "partial" and record["partial"]["stage"] == "measurement"
    validate_artifact("novelty", record)


def test_the_message_says_a_rerun_replays_the_paid_calls_when_the_cache_was_set(tmp_path,
                                                                              monkeypatch):
    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(tmp_path / "cache"))
    args, _out_dir = _command(tmp_path, monkeypatch, _answers_cost)
    with pytest.raises(SystemExit, match="same DOKIMI_RESPONSE_CACHE: the calls already paid for "
                                         "replay at \\$0"):
        args.func(args)


def test_a_no_price_stop_does_not_advise_a_higher_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(tmp_path / "cache"))
    args, _out_dir = _command(tmp_path, monkeypatch, lambda prompt: None)
    with pytest.raises(SystemExit) as stop:
        args.func(args)
    message = str(stop.value)
    assert "Rerun with the model priced, or without --max-usd, and the same DOKIMI_RESPONSE_CACHE" \
        in message
    assert "higher --max-usd" not in message


def test_the_message_does_not_claim_a_replay_when_the_cache_variable_names_a_file(tmp_path,
                                                                               monkeypatch):
    not_a_directory = tmp_path / "cache-file"
    not_a_directory.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(not_a_directory))
    args, _out_dir = _command(tmp_path, monkeypatch, _answers_cost)
    with pytest.raises(SystemExit) as stop:
        args.func(args)
    message = str(stop.value)
    assert "DOKIMI_RESPONSE_CACHE was not set on this run" in message
    assert "a rerun pays for these calls again" in message
    assert "already paid for replay" not in message


def test_a_partial_result_never_replaces_a_finished_novelty_json(tmp_path, monkeypatch):
    args, out_dir = _command(tmp_path, monkeypatch, _answers_cost)
    out_dir.mkdir()
    finished = out_dir / "novelty.json"
    finished.write_text('{"schema_version": "novelty.v1", "finished": true}', encoding="utf-8")
    with pytest.raises(SystemExit):
        args.func(args)
    assert json.loads(finished.read_text(encoding="utf-8"))["finished"] is True
    assert json.loads((out_dir / "novelty.partial.json").read_text(encoding="utf-8"))["status"] \
        == "partial"
    assert not (out_dir / "novelty.md").exists()


def test_a_partial_analyze_file_is_refused_by_the_readers_of_a_finished_one(tmp_path,
                                                                           monkeypatch):
    from dokimi.commands.novelty_view_inputs import load_view_inputs
    from dokimi.subjects.novelty_track import load_novelty_artifacts

    args, out_dir = _command(tmp_path, monkeypatch, _answers_cost)
    with pytest.raises(SystemExit):
        args.func(args)
    partial = out_dir / "novelty.partial.json"
    record = json.loads(partial.read_text(encoding="utf-8"))
    del record["partial"]  # the schema lets `status` stand alone
    status_only = tmp_path / "status-only.json"
    status_only.write_text(json.dumps(record), encoding="utf-8")
    for source in (partial, status_only):
        for validate in (True, False):
            with pytest.raises(InputError, match="partial novelty artifact"):
                load_artifact("novelty", source, validate=validate)
    for read in (lambda: load_artifact("novelty", partial),
                 lambda: load_artifact("novelty", partial, validate=False),
                 lambda: load_view_inputs(str(partial), str(tmp_path / "atoms.json")),
                 lambda: load_novelty_artifacts([partial])):
        with pytest.raises(InputError, match="partial novelty artifact") as refused:
            read()
        assert str(partial) in str(refused.value)


def test_a_partial_analyze_file_is_not_listed_as_a_finished_run(tmp_path, monkeypatch):
    from dokimi.runs.scan import scan

    args, out_dir = _command(tmp_path, monkeypatch, _answers_cost)
    with pytest.raises(SystemExit):
        args.func(args)
    result = scan(out_dir)
    assert result.artifacts == [] and result.skipped["no_leading_schema_version"] == 1

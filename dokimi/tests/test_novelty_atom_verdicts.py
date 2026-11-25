# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`skill novelty analyze` saves each atom's judged and measured verdicts beside the artifact (#1005).

The runs go through the CLI with the journey test's scripted models, bent so the two atoms
differ: one the model already knew, one the skill adds under lenient grading only.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from dokimi.commands.novelty_view_inputs import load_atom_verdicts
from dokimi.novelty import client as client_mod
from dokimi.novelty.atoms import load_atoms_record
from dokimi.files import InputError
from tests.test_novelty_analyze import _scripted
from tests.test_novelty_journey import _run
from tests.test_novelty_journey import skill as _journey_skill

skill = _journey_skill  # the journey test's skill fixture, registered under its own name here

OUTCOMES = ("added", "redundant", "degraded", "still_missing")
KNOWN = "Which flag enables the accelerator?"
GATED = "May the accelerator be enabled on driver 1.0?"


def _install(monkeypatch, *, conflict: bool, omit_second: bool = False, fail_conflict: bool = False):
    """Every client answers from the scripted transport, bent as the module docstring says."""
    base = _scripted("informed")

    def transport(prompt, system):
        if "judge how it functions" in prompt:
            rows = [{"n": 1, "needed": True, "scaffolding": False},
                    {"n": 2, "needed": False, "scaffolding": False}]
            return json.dumps(rows[:1] if omit_second else rows), None
        if "Find pairs of statements" in prompt:
            if fail_conflict:
                return "sorry, I cannot", None
            pairs = [{"a": 1, "b": 2, "why": "the flag and the gate disagree"}] if conflict else []
            return json.dumps({"conflicts": pairs}), None
        if "Grade the RESPONSE" in prompt and "QUESTION: " in prompt:
            question = prompt.split("QUESTION: ", 1)[1].splitlines()[0]
            informed = "INFORMED" in prompt
            if question.endswith(KNOWN):
                return json.dumps({"verdict": "correct_complete"}), None
            if question.endswith(GATED) and informed:
                return json.dumps({"verdict": "correct_missing_qualifier"}), None
        return base(prompt, system)

    real = client_mod.LLMClient
    monkeypatch.setattr(client_mod, "LLMClient",
                        lambda **kwargs: real(**{"transport": transport, **kwargs}))


@pytest.fixture
def mixed_models(monkeypatch):
    _install(monkeypatch, conflict=False)


@pytest.fixture
def conflicting_models(monkeypatch):
    _install(monkeypatch, conflict=True)


def _analyze(tmp_path, skill, name="run", *extra):
    out_dir = tmp_path / name
    _run("skill", "novelty", "analyze", "--artifact", f"skill:{skill}", "--tier", "study",
         "--model", "model-a", "--judge-model", "judge-m", "--out-dir", str(out_dir),
         "--view-format", "md", *extra)
    return out_dir


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(out_dir):
    return {r["question"]: r for r in _load(out_dir / "novelty.atom_verdicts.json")["atoms"]}


def test_the_rows_add_up_to_the_totals_in_novelty_json(tmp_path, skill, mixed_models, capsys):
    out_dir = _analyze(tmp_path, skill)
    artifact = _load(out_dir / "novelty.json")
    sidecar = _load(out_dir / "novelty.atom_verdicts.json")
    rows = sidecar["atoms"]

    assert f"OK Saved atom verdicts:    {out_dir / 'novelty.atom_verdicts.json'}" in capsys.readouterr().out
    assert sidecar["schema_version"] == "novelty_atoms.v1"
    assert len(rows) == artifact["atoms"]["n"] == 2
    assert (sidecar["artifact_ref"], sidecar["model"]) == (artifact["artifact"]["ref"], artifact["model"])
    assert sidecar["with_arm"] == "full"
    assert Counter(r["faithfulness"] for r in rows) == Counter(
        {k: v for k, v in artifact["atoms"]["faithfulness"].items() if v})
    assert Counter(r["category"] for r in rows) == Counter(
        {k: v for k, v in artifact["categories"].items() if v})
    # `bundle.by_source` counts the same lenient per-atom outcomes, file by file.
    by_source = Counter()
    for source in artifact["bundle"]["by_source"]:
        by_source.update({k: source.get(k, 0) for k in OUTCOMES})
    assert Counter(r["outcome"]["lenient"] for r in rows) == +by_source
    assert artifact["conflicts"] == sidecar["conflicts"] == []


def test_each_row_has_its_own_judged_and_measured_verdicts(tmp_path, skill, mixed_models):
    rows = _rows(_analyze(tmp_path, skill))
    known, gated = rows[KNOWN], rows[GATED]
    assert known["statement"] == "The --accel flag enables the accelerator."
    assert (known["answer"], known["kind"], known["tags"], known["span_ids"]) == (
        "--accel", "proposition", ["procedural"], ["s0002"])
    assert (known["needed"], known["scaffolding"], known["conflict"], known["conflict_why"]) == (
        True, False, False, "")
    assert (known["outcome"], known["category"]) == (
        {"lenient": "redundant", "strict": "redundant"}, "known_useful")
    assert (gated["needed"], gated["scaffolding"]) == (False, False)
    # Graded `correct_missing_qualifier` with the skill: right under lenient scoring, not strict.
    assert (gated["outcome"], gated["category"]) == (
        {"lenient": "added", "strict": "still_missing"}, "novel_inert")
    assert gated["faithfulness"] == "supported"


def test_a_conflict_pair_is_kept_with_atom_ids_and_marks_both_rows(tmp_path, skill, conflicting_models):
    out_dir = _analyze(tmp_path, skill)
    sidecar = _load(out_dir / "novelty.atom_verdicts.json")
    by_id = {r["id"]: r for r in sidecar["atoms"]}
    (pair,) = sidecar["conflicts"]
    assert pair == {"a": pair["a"], "b": pair["b"], "why": "the flag and the gate disagree"}
    assert {pair["a"], pair["b"]} == set(by_id)
    assert all(r["conflict"] is True and r["conflict_why"] == pair["why"]
               and r["category"] == "harmful_conflicting" for r in by_id.values())


def test_a_conflicting_atom_the_value_judge_skipped_keeps_null_needed_and_scaffolding(
        tmp_path, skill, monkeypatch):
    _install(monkeypatch, conflict=True, omit_second=True)
    rows = _rows(_analyze(tmp_path, skill))
    answered, skipped = rows[KNOWN], rows[GATED]
    assert (answered["needed"], answered["scaffolding"]) == (True, False)
    assert (skipped["needed"], skipped["scaffolding"]) == (None, None)
    assert (skipped["conflict"], skipped["conflict_why"], skipped["category"]) == (
        True, "the flag and the gate disagree", "harmful_conflicting")


def test_a_run_without_classification_has_null_judged_fields(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill, "plain", "--no-classify")
    sidecar = _load(out_dir / "novelty.atom_verdicts.json")
    assert len(sidecar["atoms"]) == 2 and sidecar["conflicts"] == []
    for row in sidecar["atoms"]:
        assert [row[k] for k in ("needed", "scaffolding", "conflict", "conflict_why", "category")] == [None] * 5
    assert {q: r["outcome"]["lenient"] for q, r in _rows(out_dir).items()} == {
        KNOWN: "redundant", GATED: "added"}


def test_a_stale_sidecar_in_the_output_is_replaced(tmp_path, skill, mixed_models):
    out_dir = tmp_path / "again"
    out_dir.mkdir()
    stale = out_dir / "novelty.atom_verdicts.json"
    stale.write_text("not json", encoding="utf-8")
    _analyze(tmp_path, skill, "again")
    assert _load(stale)["schema_version"] == "novelty_atoms.v1"


def test_analyze_saves_the_atoms_it_probed_so_view_needs_no_second_extraction(
        tmp_path, skill, mixed_models, capsys):
    out_dir = _analyze(tmp_path, skill)
    atoms_file = out_dir / "novelty.atoms.json"
    assert f"OK Saved atoms:            {atoms_file}" in capsys.readouterr().out
    atoms, spans, record = load_atoms_record(atoms_file)
    assert {a.id for a in atoms} == {r["id"] for r in _rows(out_dir).values()}
    assert {a.statement for a in atoms} == {r["statement"] for r in _rows(out_dir).values()}
    assert record["extractor"] and record["bundle"]["artifact_name"]  # what probe and shorten read
    assert spans and all(a.span_ids for a in atoms)
    outcomes, categories = load_atom_verdicts(
        str(out_dir / "novelty.atom_verdicts.json"), _load(out_dir / "novelty.json"), atoms, record)
    assert outcomes and categories


def test_a_capped_run_pins_the_sampled_atoms_in_the_atoms_file(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill, "capped", "--max-atoms", "1")
    sampled = _load(out_dir / "novelty.atoms.json")["sampled_atom_ids"]
    assert sampled == [r["id"] for r in _rows(out_dir).values()] and len(sampled) == 1


def test_an_atom_the_judge_skipped_and_the_conflict_check_cleared_reads_false(tmp_path, skill, monkeypatch):
    _install(monkeypatch, conflict=False, omit_second=True)
    rows = _rows(_analyze(tmp_path, skill))
    assert rows[GATED]["conflict"] is False


def test_a_conflict_check_that_failed_leaves_conflict_null_not_false(tmp_path, skill, monkeypatch):
    _install(monkeypatch, conflict=False, fail_conflict=True)
    out_dir = _analyze(tmp_path, skill)
    artifact = _load(out_dir / "novelty.json")
    assert artifact["conflict_coverage"]["pairs_compared"] == 0 < artifact["conflict_coverage"]["pairs_total"]
    assert any("not checked for conflicts" in w for w in artifact["warnings"])
    for row in _rows(out_dir).values():
        assert (row["conflict"], row["conflict_why"]) == (None, None)
        assert row["category"] in {"known_useful", "novel_inert"}  # the judged axes still stand


def _span_labels(path):
    return re.findall(r"^`(s\d+)` \[([^\]/]+)/", path.read_text(encoding="utf-8"), re.M)


def test_view_with_the_flag_colours_spans_as_analyze_did_and_without_it_does_not(
        tmp_path, skill, mixed_models, capsys):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    capsys.readouterr()

    def view(name, *extra):
        out = tmp_path / name
        _run("skill", "novelty", "view", "--novelty", str(out_dir / "novelty.json"), "--atoms",
             str(atoms_out), "--format", "md", "--out", str(out), *extra)
        return out, capsys.readouterr().out

    with_flag, said = view("with.md", "--atom-verdicts", str(out_dir / "novelty.atom_verdicts.json"))
    plain, plain_said = view("plain.md")

    analyzed = _span_labels(out_dir / "annotated.md")
    assert {"added", "redundant"} <= {label for _, label in analyzed}
    assert _span_labels(with_flag) == analyzed
    assert not {"added", "redundant", "degraded", "harmful"} & {label for _, label in _span_labels(plain)}
    assert "NOTE outcome and category colours come from" in said
    assert "pass --atom-verdicts" in plain_said


def _view_args(out_dir, atoms_out, sidecar, tmp_path):
    return ["skill", "novelty", "view", "--novelty", str(out_dir / "novelty.json"), "--atoms",
            str(atoms_out), "--atom-verdicts", str(sidecar), "--out", str(tmp_path / "v.html")]


def test_view_refuses_verdicts_written_for_another_artifact(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    sidecar = out_dir / "novelty.atom_verdicts.json"
    data = _load(sidecar)
    data["artifact_ref"] = "skill:/elsewhere/SKILL.md"
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError) as exc:
        _run(*_view_args(out_dir, atoms_out, sidecar, tmp_path))
    assert "skill:/elsewhere/SKILL.md" in str(exc.value)
    assert _load(out_dir / "novelty.json")["artifact"]["ref"] in str(exc.value)
    assert not (tmp_path / "v.html").exists()


@pytest.mark.parametrize("field, value", [
    ("model", "model-b"),
    ("ref_digest", "sha256:another-revision"),
    ("run_id", "20200101T000000Z-other"),
])
def test_view_refuses_verdicts_of_another_run_of_the_same_artifact(
        tmp_path, skill, mixed_models, field, value):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    sidecar = out_dir / "novelty.atom_verdicts.json"
    data = _load(sidecar)
    assert data[field] != value
    data[field] = value
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError, match="another analysis") as exc:
        _run(*_view_args(out_dir, atoms_out, sidecar, tmp_path))
    assert field.replace("ref_digest", "artifact revision (ref_digest)") in str(exc.value)
    assert value in str(exc.value)
    assert not (tmp_path / "v.html").exists()


def test_view_refuses_verdicts_of_another_analysis_that_shares_the_run_id(
        tmp_path, skill, mixed_models, monkeypatch):
    monkeypatch.setenv("DOKIMI_RUN_ID", "ci-job-1")
    first = _analyze(tmp_path, skill, "classified")
    second = _analyze(tmp_path, skill, "plain", "--no-classify")
    sidecar = first / "novelty.atom_verdicts.json"
    assert _load(sidecar)["run_id"] == _load(second / "novelty.json")["run_id"] == "ci-job-1"
    with pytest.raises(InputError, match="another analysis") as exc:
        _run(*_view_args(second, second / "novelty.atoms.json", sidecar, tmp_path))
    assert "its analysis_digest is" in str(exc.value)
    assert not (tmp_path / "v.html").exists()


def test_view_takes_the_verdicts_of_its_own_analysis_under_a_shared_run_id(
        tmp_path, skill, mixed_models, monkeypatch):
    monkeypatch.setenv("DOKIMI_RUN_ID", "ci-job-1")
    out_dir = _analyze(tmp_path, skill)
    _run(*_view_args(out_dir, out_dir / "novelty.atoms.json", out_dir / "novelty.atom_verdicts.json", tmp_path))
    assert (tmp_path / "v.html").exists()


def test_view_refuses_verdicts_with_a_run_id_for_a_novelty_saved_without_one(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    sidecar = out_dir / "novelty.atom_verdicts.json"
    novelty = out_dir / "novelty.json"
    data = _load(novelty)
    assert data.pop("run_id")
    novelty.write_text(json.dumps(data), encoding="utf-8")
    assert _load(sidecar)["run_id"]
    with pytest.raises(InputError, match="another analysis") as exc:
        _run(*_view_args(out_dir, atoms_out, sidecar, tmp_path))
    assert "its run_id is" in str(exc.value) and "--novelty has None" in str(exc.value)
    assert not (tmp_path / "v.html").exists()


def test_view_refuses_verdicts_that_name_atoms_the_atoms_file_lacks(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    sidecar = out_dir / "novelty.atom_verdicts.json"
    data = _load(sidecar)
    data["atoms"][0]["id"] = "feedfacefeed"
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError, match="feedfacefeed"):
        _run(*_view_args(out_dir, atoms_out, sidecar, tmp_path))
    assert not (tmp_path / "v.html").exists()


def test_view_refuses_atoms_extracted_from_another_revision_of_the_source(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    data = _load(atoms_out)
    assert data["bundle"]["measured_digest"]
    data["bundle"]["measured_digest"] = "sha256:edited-source"
    atoms_out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError, match="extracted from 'sha256:edited-source'"):
        _run(*_view_args(out_dir, atoms_out, out_dir / "novelty.atom_verdicts.json", tmp_path))
    assert not (tmp_path / "v.html").exists()


def test_view_refuses_atoms_whose_ids_match_but_cite_other_spans(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    data = _load(atoms_out)
    moved = data["atoms"][0]
    moved["span_ids"] = ["s9999"]
    atoms_out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError, match="cites other spans") as exc:
        _run(*_view_args(out_dir, atoms_out, out_dir / "novelty.atom_verdicts.json", tmp_path))
    assert moved["id"] in str(exc.value)
    assert not (tmp_path / "v.html").exists()


def test_view_refuses_a_file_that_is_not_atom_verdicts(tmp_path, skill, mixed_models):
    out_dir = _analyze(tmp_path, skill)
    atoms_out = out_dir / "novelty.atoms.json"
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(InputError, match="not a valid novelty_atoms artifact"):
        _run(*_view_args(out_dir, atoms_out, bad, tmp_path))

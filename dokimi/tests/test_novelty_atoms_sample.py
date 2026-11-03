# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A capped run indexes spans from every extracted atom and probes only the cap's sample (#371)."""

from __future__ import annotations

import json
import re

import pytest

from dokimi.novelty.analyze import AnalyzeConfig, analyze
from dokimi.novelty.atoms import (Atom, load_atoms, load_atoms_or_exit, recorded_sample,
                                  sample_atoms, save_atoms)
from dokimi.novelty.client import LLMClient
from dokimi.novelty.spans import segment
from dokimi.novelty.tiers import resolve_settings
from tests import test_novelty_cli as novelty_cli
from tests.test_novelty_analyze import _scripted

ROWS = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 201)) + "\n"


def _one_atom_per_span(prompt, system=None):
    """The extractor gives each span one atom; every other call is the scripted pipeline's."""
    if "Extract the checkable knowledge" not in prompt:
        return _scripted("informed")(prompt, system)
    return json.dumps([{"statement": f"{sid} holds a default.", "question": f"What of {sid}?",
                        "answer": "a value", "kind": "proposition", "tags": [],
                        "span_ids": [sid]}
                       for sid in re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)]), None


def _counting(calls: list):
    def transport(prompt, system=None):
        calls.append(prompt)
        return _scripted("informed")(prompt, system)
    return transport


def test_a_capped_run_indexes_every_atom_and_probes_only_the_sample():
    """The issue's case: 200 one-atom spans at `--tier standard`, whose cap is 60."""
    answers: list = []
    grades: list = []
    config = AnalyzeConfig(artifact_ref="text:rows.md", artifact_kind="text", name="rows",
                           description="Settings and their defaults", concurrency=1,
                           **resolve_settings("standard", {}).config_fields())
    result = analyze(ROWS, config,
                     extractor=LLMClient(model="e", role="extractor", transport=_one_atom_per_span),
                     answerer=LLMClient(model="a", role="answerer", transport=_counting(answers)),
                     judge=LLMClient(model="j", role="judge", transport=_counting(grades)))
    artifact = result.artifact
    spans = artifact["spans"]
    # Every span yields an atom, so every span carries one: none reads as dead weight.
    assert len(result.spans) == 200 and all(span.atom_ids for span in result.spans)
    assert (spans["n_atoms_indexed"], spans["capped_atom_set"]) == (200, False)
    assert spans["frac_carrying_nothing"] == 0.0
    assert spans["sole_carrier"] == 200 and artifact["views"]["dead_weight"] == []
    assert artifact["attribution"]["spans_by_label"]["empty"] == 0
    # The paid stages stay at the cap: 60 atoms, each a question and a paraphrase in two arms.
    assert artifact["extraction"]["sampled"] == {"atoms": 60, "of": 200, "rule": "cap-spread"}
    assert (artifact["atoms"]["n"], artifact["atoms"]["n_probed"]) == (60, 60)
    assert len(answers) == len(grades) == 60 * 2 * 2
    # Carrier confirmation and each shortening candidate ask the oracle about the 60 only.
    assert artifact["retention"]["decisions"] == 60 * (1 + len(artifact["shortening"]))
    forfeits = artifact["run_config"]["tier"]["forfeits"]
    assert not any("frac_carrying_nothing" in line or "looks free" in line for line in forfeits)
    assert any(line.startswith("shortening candidates are scored against a spread sample of 60")
               for line in forfeits)


def _capped_file(tmp_path, *, n: int = 6, cap: int = 2):
    spans = segment("\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, n + 1)))
    atoms = [Atom(statement=f"{span.id} holds a default.", question=f"What of {span.id}?",
                  answer="a value", span_ids=(span.id,)) for span in spans]
    sample = sample_atoms(atoms, spans, cap)
    path = tmp_path / "atoms.json"
    save_atoms(atoms, spans, path, cap=cap, sample=sample)
    return path, atoms, sample


def test_atoms_json_holds_every_atom_and_names_the_sample(tmp_path):
    path, atoms, sample = _capped_file(tmp_path)
    saved = json.loads(path.read_text())
    assert len(saved["atoms"]) == 6
    assert saved["sampled_atom_ids"] == [atom.id for atom in sample]
    loaded, spans, data = load_atoms_or_exit(path)
    assert [atom.id for atom in loaded] == [atom.id for atom in atoms]
    assert [atom.span_ids for atom in recorded_sample(data, loaded)] == [("s0002",), ("s0005",)]
    # A cap that kept every atom records no sample: probe and shorten then measure them all.
    save_atoms(atoms, spans, path, cap=6, sample=sample_atoms(atoms, spans, 6))
    assert json.loads(path.read_text())["sampled_atom_ids"] is None


def test_an_atoms_file_without_a_sample_record_reads_as_every_atom_sampled(tmp_path):
    # Written before the record existed, when the file held only the atoms its run probed.
    path, atoms, _sample = _capped_file(tmp_path)
    data = json.loads(path.read_text())
    del data["sampled_atom_ids"]
    path.write_text(json.dumps(data))
    loaded, _spans, data = load_atoms_or_exit(path)
    assert [atom.id for atom in recorded_sample(data, loaded)] == [atom.id for atom in atoms]


@pytest.mark.parametrize("record, error", [
    (["a0000000000"], "'sampled_atom_ids' names 1 atom id(s) the file's 'atoms' do not hold: "
                      "a0000000000"),
    ("a0000000000", "'sampled_atom_ids' is not a list of strings"),
], ids=["unknown-id", "not-a-list"])
def test_a_sample_record_the_atoms_do_not_hold_is_refused(tmp_path, capsys, record, error):
    path, _atoms, _sample = _capped_file(tmp_path)
    data = json.loads(path.read_text())
    data["sampled_atom_ids"] = record
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=re.escape(error)):
        load_atoms(path)
    with pytest.raises(SystemExit) as exit_info:
        load_atoms_or_exit(path)
    assert exit_info.value.code == 2
    assert f"cannot read atoms from {path}: {error}" in capsys.readouterr().err


def _cli(monkeypatch, *argv):
    real_client = novelty_cli.novelty_cmd._client
    monkeypatch.setattr(novelty_cli.novelty_cmd, "_client",
                        lambda model, provider, role, args: novelty_cli._with_transport(
                            real_client(model, provider, role, args), _one_atom_per_span))
    args = novelty_cli._parse("novelty", *argv)
    args.func(args)


def _capped_atoms_json(tmp_path, monkeypatch):
    """`novelty atoms --max-atoms 2` over a six-span skill: the skill and its atoms.json."""
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n" + "\n\n".join(
        f"Setting {i} defaults to {i}." for i in range(1, 7)) + "\n", encoding="utf-8")
    atoms_json = tmp_path / "atoms.json"
    _cli(monkeypatch, "atoms", "--artifact", f"skill:{skill}", "--out", str(atoms_json),
         "--max-atoms", "2", "--no-verify")
    saved = json.loads(atoms_json.read_text())
    assert (len(saved["atoms"]), len(saved["sampled_atom_ids"])) == (6, 2)
    return skill, atoms_json, saved


@pytest.mark.parametrize("flags, scored", [([], 2), (["--all-atoms"], 6)],
                         ids=["the-sample", "all-atoms"])
def test_shorten_indexes_every_atom_and_scores_the_recorded_sample(
        tmp_path, monkeypatch, capsys, flags, scored):
    from dokimi.novelty import shorten

    skill, atoms_json, saved = _capped_atoms_json(tmp_path, monkeypatch)
    seen: list = []
    build = shorten.build_candidates

    def recording(spans, atoms, **kwargs):
        seen.append((len(atoms), [atom.id for atom in kwargs["scored_atoms"]]))
        return build(spans, atoms, **kwargs)

    monkeypatch.setattr(shorten, "build_candidates", recording)
    capsys.readouterr()
    _cli(monkeypatch, "shorten", "--artifact", f"skill:{skill}", "--atoms", str(atoms_json),
         "--lexical-only", *flags)
    assert [(n, len(ids)) for n, ids in seen] == [(6, scored)]
    if scored == 2:
        assert seen[0][1] == saved["sampled_atom_ids"]
    assert ("NOTE shorten measures the 2 atoms" in capsys.readouterr().err) is (scored == 2)


@pytest.mark.parametrize("flags, probed", [([], 2), (["--all-atoms"], 6)],
                         ids=["the-sample", "all-atoms"])
def test_probe_measures_the_recorded_sample_or_with_all_atoms_every_atom(
        tmp_path, monkeypatch, capsys, flags, probed):
    skill, atoms_json, saved = _capped_atoms_json(tmp_path, monkeypatch)
    probes_json = tmp_path / "p.json"
    capsys.readouterr()
    _cli(monkeypatch, "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms_json),
         "--concurrency", "1", "--no-paraphrase", "--no-reversal", "--out", str(probes_json),
         *flags)
    runs = json.loads(probes_json.read_text())["runs"]
    assert len({run["atom_id"] for run in runs}) == probed
    if probed == 2:
        assert {run["atom_id"] for run in runs} == set(saved["sampled_atom_ids"])
    note = (f"NOTE probe measures the 2 atoms {atoms_json} records as the cap's sample, of its 6; "
            "spans are indexed from all 6. Pass --all-atoms to measure every atom")
    assert (note in capsys.readouterr().err) is (probed == 2)

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
from tests.test_novelty_report import _artifact as _report_artifact

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
    # The result keeps every extracted atom: each id the spans and attributions cite resolves.
    assert (len(result.extracted_atoms), len(result.atoms)) == (200, 60)
    cited = ({atom_id for span in result.spans for atom_id in span.atom_ids}
             | {atom_id for row in result.attributions for atom_id in row.atom_ids})
    assert cited == {atom.id for atom in result.extracted_atoms}
    # The paid stages stay at the cap: 60 atoms, each a question and a paraphrase in two arms.
    assert artifact["extraction"]["sampled"] == {"atoms": 60, "of": 200, "rule": "cap-spread"}
    assert (artifact["atoms"]["n"], artifact["atoms"]["n_probed"]) == (60, 60)
    assert len(answers) == len(grades) == 60 * 2 * 2
    # Carrier confirmation and each shortening candidate ask the oracle about the 60 only.
    assert artifact["retention"]["decisions"] == 60 * (1 + len(artifact["shortening"]))
    # A cut that drops unsampled atoms, which those decisions never score, is not offered.
    lossy = [c for c in artifact["shortening"] if c["dropped_unscored_atoms"]]
    assert lossy and not any(c["safe"] for c in lossy)
    assert any("drop every span of an atom outside the cap's sample" in line
               for line in artifact["warnings"])
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


def test_a_sample_record_that_repeats_an_id_is_refused(tmp_path, capsys):
    """Counted twice, one retained atom would read as half the sample's knowledge."""
    path, atoms, _sample = _capped_file(tmp_path, n=3, cap=2)
    data = json.loads(path.read_text())
    data["sampled_atom_ids"] = [atoms[0].id, atoms[0].id]
    path.write_text(json.dumps(data))
    error = f"'sampled_atom_ids' repeats 1 atom id(s): {atoms[0].id}"
    with pytest.raises(SystemExit) as exit_info:
        load_atoms_or_exit(path)
    assert exit_info.value.code == 2
    assert f"cannot read atoms from {path}: {error}" in capsys.readouterr().err


@pytest.mark.parametrize("cap", [0, -1])
def test_sample_atoms_refuses_a_cap_below_one(cap):
    spans = segment("One.\n\nTwo.")
    atoms = [Atom(statement=s.text, question="q", answer="a", span_ids=(s.id,)) for s in spans]
    with pytest.raises(ValueError, match="the atom cap must be at least 1, or None for no cap"):
        sample_atoms(atoms, spans, cap)
    assert sample_atoms(atoms, spans, None) == atoms
    assert sample_atoms(atoms, spans, 1) == [atoms[1]]


def _cli(monkeypatch, *argv):
    real_client = novelty_cli.novelty_cmd._client
    monkeypatch.setattr(novelty_cli.novelty_cmd, "_client",
                        lambda model, provider, role, args: novelty_cli._with_transport(
                            real_client(model, provider, role, args), _one_atom_per_span))
    args = novelty_cli._parse("novelty", *argv)
    args.func(args)


def _capped_atoms_json(tmp_path, monkeypatch, cap: int = 2):
    """`novelty atoms --max-atoms <cap>` over a six-span skill: the skill and its atoms.json."""
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n" + "\n\n".join(
        f"Setting {i} defaults to {i}." for i in range(1, 7)) + "\n", encoding="utf-8")
    atoms_json = tmp_path / "atoms.json"
    _cli(monkeypatch, "atoms", "--artifact", f"skill:{skill}", "--out", str(atoms_json),
         "--max-atoms", str(cap), "--no-verify")
    saved = json.loads(atoms_json.read_text())
    assert len(saved["atoms"]) == 6
    assert len(saved["sampled_atom_ids"] or saved["atoms"]) == min(cap, 6)
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


def test_both_shorten_tables_show_the_unsampled_atoms_a_candidate_loses(
        tmp_path, monkeypatch, capsys):
    """`prot 1.000 safe False` has a visible reason: the `lost` column."""
    from dokimi.novelty import shorten
    from dokimi.novelty.report import render_novelty_markdown

    skill, atoms_json, _saved = _capped_atoms_json(tmp_path, monkeypatch)
    lossy = shorten.Candidate(strategy="greedy_value", budget_frac=0.5, kept_span_ids=["s0001"],
                              text="t", tokens=5, tokens_full=10, knowledge=1.0, value=1.0,
                              protection=1.0, dropped_unscored_atoms=2)
    monkeypatch.setattr(shorten, "build_candidates", lambda spans, atoms, **kwargs: [lossy])
    capsys.readouterr()
    _cli(monkeypatch, "shorten", "--artifact", f"skill:{skill}", "--atoms", str(atoms_json),
         "--lexical-only")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("value   prot lost safe")
    assert lines[1].endswith("  1.000  1.000  1.000    2 False")
    markdown = render_novelty_markdown(_report_artifact(shortening=[lossy.as_dict()]))
    assert "| protection | lost | safe |" in markdown
    assert "| 1.0 | 1.0 | 1.0 | 2 | False |" in markdown
    assert "or every span of an atom outside the cap's sample (`lost`" in markdown


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


def test_all_atoms_warns_about_the_atoms_it_adds_that_were_never_checked(
        tmp_path, monkeypatch, capsys):
    """`novelty atoms` verifies only the sample, so the atoms outside it read `unchecked`."""
    skill, atoms_json, saved = _capped_atoms_json(tmp_path, monkeypatch)
    sampled = set(saved["sampled_atom_ids"])
    for row in [row for row in saved["atoms"] if row["id"] not in sampled][:2]:
        row["faithfulness"] = "supported"
    atoms_json.write_text(json.dumps(saved))
    argv = ("probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms_json), "--concurrency",
            "1", "--no-paraphrase", "--no-reversal", "--out", str(tmp_path / "p.json"))
    warning = (f"WARNING: probe --all-atoms measures 4 atom(s) outside the sample {atoms_json} "
               "records, 2 of them with faithfulness \"unchecked\": their answer key was never "
               "checked against the text. To check every atom, re-run `novelty atoms "
               "--all-atoms`, a new uncapped extraction that verifies each atom it extracts")
    capsys.readouterr()
    _cli(monkeypatch, *argv, "--all-atoms")
    assert warning in capsys.readouterr().err
    _cli(monkeypatch, *argv)  # the sample only: nothing unchecked is added
    assert "--all-atoms measures" not in capsys.readouterr().err
    for row in saved["atoms"]:
        row["faithfulness"] = "supported"
    atoms_json.write_text(json.dumps(saved))
    _cli(monkeypatch, *argv, "--all-atoms")
    assert "--all-atoms measures" not in capsys.readouterr().err


def test_shorten_refuses_a_sample_the_entry_point_leaves_empty(tmp_path, monkeypatch, capsys):
    """Two entry-point atoms, five resource atoms, and a cap of one that sampled a resource atom."""
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    path, atoms, sample = _capped_file(tmp_path, n=7, cap=1)
    data = json.loads(path.read_text())
    assert sample[0].span_ids == ("s0004",)  # a resource span: the entry point ends at s0002
    data["bundle"] = {"include_resources": True, "n_files": 2, "files": [
        {"path": "SKILL.md", "start": 0, "end": data["spans"][2]["start"]},
        {"path": "references/r.md", "start": data["spans"][2]["start"]}]}
    path.write_text(json.dumps(data))
    out = tmp_path / "short.md"
    argv = ("shorten", "--artifact", f"skill:{skill}", "--atoms", str(path), "--lexical-only",
            "--budgets", "0.5", "--out", str(out))
    with pytest.raises(SystemExit) as exit_info:
        _cli(monkeypatch, *argv)
    assert exit_info.value.code == 2 and not out.exists()
    assert (f"shorten: {path} records a sample of 1 atom(s), none of them among the 2 of its 7 "
            "atoms shorten measures here, so it would score nothing; pass --all-atoms to measure "
            "those 2") in capsys.readouterr().err
    _cli(monkeypatch, *argv, "--all-atoms")
    assert "OK Wrote candidate" in capsys.readouterr().out and out.exists()


HOLDS_THE_SAMPLE = "it holds only the atoms its run probed"
MAY_HOLD_THE_SAMPLE = ("it may hold only the atoms its run probed (its cap was met at the input's "
                       "last span and may have cut that reply)")


def _old_capped_warning(holds: str = HOLDS_THE_SAMPLE) -> str:
    return ("was written by a capped `novelty atoms` run before atoms files recorded their sample "
            f"(`sampled_atom_ids`): {holds}, so spans whose atoms fell outside them read as "
            "carrying nothing and a shortening can cut them; re-run `novelty atoms` to index "
            "every atom")


OLD_CAPPED_WARNING = _old_capped_warning()


def _as_written_before_371(atoms_json, saved, **extraction) -> None:
    """Rewrite ``saved`` as a capped run wrote it before #371: only the probed atoms, spans
    indexed from them, no ``sampled_atom_ids``; ``extraction`` overrides its record."""
    kept = set(saved.pop("sampled_atom_ids"))
    saved["atoms"] = [row for row in saved["atoms"] if row["id"] in kept]
    for span in saved["spans"]:
        span["atom_ids"] = [atom_id for atom_id in span["atom_ids"] if atom_id in kept]
    saved["extraction"].update(extraction)
    atoms_json.write_text(json.dumps(saved))


def _reach(saved, spans: int) -> dict:
    """A document-order cap's ``extraction.reached`` (#299) that stopped at span ``spans``."""
    return {"sampled": None, "reached": {"last_span": saved["spans"][spans - 1]["id"],
                                         "spans": spans, "of": len(saved["spans"]),
                                         "tokens_share": spans / len(saved["spans"])}}


@pytest.mark.parametrize("cap, written, holds", [
    (2, "spread-cap", HOLDS_THE_SAMPLE), (2, "reach-short", HOLDS_THE_SAMPLE),
    (2, "reach-end", MAY_HOLD_THE_SAMPLE), (2, "this-version", None), (6, "this-version", None),
], ids=["spread-cap-before-371", "document-order-cap-short-of-the-end",
        "document-order-cap-at-the-last-span", "this-version-capped", "this-version-cap-kept-all"])
def test_shorten_warns_on_an_older_capped_atoms_file_only(
        tmp_path, monkeypatch, capsys, cap, written, holds):
    """A document-order cap recorded `reached` only when it bound, so even one that reached the
    last span may have cut that reply's later atoms: it warns, saying it may."""
    skill, atoms_json, saved = _capped_atoms_json(tmp_path, monkeypatch, cap)
    if written == "spread-cap":  # `novelty atoms` from #358 on: `extraction.sampled` is set
        _as_written_before_371(atoms_json, saved)
    elif written.startswith("reach"):  # a document-order cap, before #358
        _as_written_before_371(atoms_json, saved,
                               **_reach(saved, 3 if written == "reach-short" else len(saved["spans"])))
    capsys.readouterr()
    _cli(monkeypatch, "shorten", "--artifact", f"skill:{skill}", "--atoms", str(atoms_json),
         "--lexical-only")
    err = capsys.readouterr().err
    if holds is None:
        assert "was written by a capped `novelty atoms` run" not in err
    else:
        assert f"WARNING: {atoms_json} {_old_capped_warning(holds)}" in err


def test_the_sample_key_not_its_value_marks_a_file_this_version_wrote():
    from dokimi.commands.novelty_atoms_file import holds_only_a_capped_sample

    sampled = {"atoms": 2, "of": 6, "rule": "cap-spread"}
    assert holds_only_a_capped_sample({"cap": 2, "extraction": {"sampled": sampled}})
    assert not holds_only_a_capped_sample(
        {"cap": 2, "extraction": {"sampled": sampled}, "sampled_atom_ids": None})
    assert not holds_only_a_capped_sample({"cap": None, "extraction": {"sampled": None}})
    # Any `reached` object means a binding document-order cap, even one at the last span.
    reached = {"last_span": "s0002", "spans": 2, "of": 2, "tokens_share": 1.0}
    assert holds_only_a_capped_sample({"cap": 1, "extraction": {"reached": reached}})
    assert not holds_only_a_capped_sample({"cap": 1, "extraction": {"reached": None}})


@pytest.mark.parametrize("n_atoms, warns", [(3, True), (2, False)],
                         ids=["as-many-atoms-as-the-cap", "fewer-atoms-than-the-cap"])
def test_a_capped_file_from_before_reached_warns_that_it_may_hold_only_its_sample(
        capsys, n_atoms, warns):
    """Before `extraction.reached` (#299) a document-order cap stopped at exactly `cap` atoms."""
    from dokimi.commands.novelty_atoms_file import holds_only_a_capped_sample, warn_capped_sample

    saved = {"cap": 3, "atoms": [{"id": f"a{i}"} for i in range(n_atoms)],
             "extraction": {"batches": 2}}
    assert holds_only_a_capped_sample(saved) is warns
    warn_capped_sample(saved, "atoms.json")
    holds = ("it may hold only the atoms its run probed (it records no `extraction.reached`, so "
             "whether its cap bound is unknown)")
    assert (f"WARNING: atoms.json {_old_capped_warning(holds)}" in capsys.readouterr().err) is warns
    # A file after #299 records `reached: null` when its cap did not bind: no warning.
    assert not holds_only_a_capped_sample(dict(saved, extraction={"reached": None}))


@pytest.mark.parametrize("before_371", [True, False], ids=["older-capped-file", "this-version"])
def test_view_and_probe_warn_on_an_older_capped_atoms_file(
        tmp_path, monkeypatch, capsys, before_371):
    from dokimi.artifacts import save_artifact

    skill, atoms_json, saved = _capped_atoms_json(tmp_path, monkeypatch)
    if before_371:
        _as_written_before_371(atoms_json, saved)
    novelty_json = tmp_path / "novelty.json"
    save_artifact("novelty", novelty_cli._minimal_artifact(), novelty_json)
    warning = f"WARNING: {atoms_json} {OLD_CAPPED_WARNING}"
    capsys.readouterr()
    _cli(monkeypatch, "view", "--novelty", str(novelty_json), "--atoms", str(atoms_json),
         "--format", "md", "--out", str(tmp_path / "annotated.md"))
    assert (warning in capsys.readouterr().err) is before_371
    _cli(monkeypatch, "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms_json),
         "--concurrency", "1", "--no-paraphrase", "--no-reversal", "--out", str(tmp_path / "p.json"))
    assert (warning in capsys.readouterr().err) is before_371

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI wiring for `novelty`: parsing, role defaults, and the guards that refuse a run."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def test_every_subcommand_is_registered():
    for sub in ("analyze", "atoms", "probe", "shorten", "view"):
        args = _parse("novelty", sub, *(
            ["--artifact", "skill:x"] if sub != "view" else ["--novelty", "n.json"]
        ), *(["--atoms", "a.json"] if sub in ("probe", "shorten", "view") else []))
        assert args.novelty_cmd == sub
        assert callable(args.func)


def test_the_judge_defaults_to_a_different_model_than_the_answerer():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--model", "gpt-4o-mini")
    extractor, answerer, judge = novelty_cmd._clients(args)
    assert judge.model != answerer.model
    assert extractor.role == "extractor" and judge.role == "judge"


def test_the_extractor_follows_the_judge_not_the_answerer():
    """The model being measured must not write its own answer key."""
    args = _parse(
        "novelty", "analyze", "--artifact", "skill:x",
        "--model", "model-a", "--judge-model", "model-b",
    )
    extractor, answerer, _judge = novelty_cmd._clients(args)
    assert extractor.model == "model-b"
    assert answerer.model == "model-a"


def test_dropping_the_baseline_arm_is_refused():
    with pytest.raises(SystemExit, match="paired"):
        novelty_cmd._arms("full,placebo")
    assert novelty_cmd._arms("no_artifact,full,shuffled") == ["no_artifact", "full", "shuffled"]


def test_a_missing_artifact_exits_with_a_message(capsys):
    args = _parse("novelty", "analyze", "--artifact", "text:/nonexistent/skill.md")
    with pytest.raises(SystemExit, match="cannot load"):
        args.func(args)
    capsys.readouterr()


def test_max_atoms_rejects_zero():
    with pytest.raises(SystemExit):
        _parse("novelty", "analyze", "--artifact", "skill:x", "--max-atoms", "0")


def test_atoms_subcommand_writes_atoms_and_span_stats(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: s\ndescription: d\n---\n\n# H\n\nDo not enable the legacy driver.\n",
        encoding="utf-8",
    )
    rows = [{
        "statement": "The legacy driver must not be enabled.",
        "question": "Should the legacy driver be enabled?",
        "answer": "No.", "kind": "contract", "tags": ["negative_constraint"],
        "span_ids": ["s0002"],
    }]

    def transport(prompt, system=None):
        if "Extract the checkable knowledge" in prompt:
            return json.dumps(rows), None
        return json.dumps({"verdict": "supported"}), None

    real_client = novelty_cmd._client
    monkeypatch.setattr(
        novelty_cmd, "_client",
        lambda model, provider, role, args: _with_transport(
            real_client(model, provider, role, args), transport
        ),
    )
    out = tmp_path / "atoms.json"
    args = _parse("novelty", "atoms", "--artifact", f"skill:{skill}", "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text())
    assert len(payload["atoms"]) == 1
    assert payload["atoms"][0]["faithfulness"] == "supported"
    assert any("negation" in span["protection"] for span in payload["spans"])
    assert "Saved 1 atoms" in capsys.readouterr().out
    assert (payload["extractor"], payload["cap"]) == ("gpt-4o-mini", 120)

    # With defaults the model under measurement wrote this answer key; probe says so.
    args = _parse("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(out),
                  "--no-paraphrase", "--no-reversal", "--out", str(tmp_path / "p.json"))
    args.func(args)
    assert "own answer key" in capsys.readouterr().err


def test_probe_result_describes_the_text_probed(tmp_path, monkeypatch):
    from dokimi.novelty.bundle import load_bundle

    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\n"
                     "Do not enable the legacy driver. Use driver 2.1 or newer.\n",
                     encoding="utf-8")
    rows = [{"statement": "The legacy driver must not be enabled.",
             "question": "Should the legacy driver be enabled?", "answer": "No.",
             "kind": "contract", "tags": [], "span_ids": ["s0002"]}]

    def transport(prompt, system=None):
        if "Extract the checkable knowledge" in prompt:
            return json.dumps(rows), None
        return json.dumps({"verdict": "supported"}), None

    real_client = novelty_cmd._client
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args:
                        _with_transport(real_client(model, provider, role, args), transport))
    atoms = tmp_path / "atoms.json"
    args = _parse("novelty", "atoms", "--artifact", f"skill:{skill}", "--out", str(atoms))
    args.func(args)
    extracted = json.loads(atoms.read_text())["bundle"]["measured_digest"]

    # The re-measure step: a shortened SKILL.md probed with the original atoms.
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nNo legacy driver.\n",
                     encoding="utf-8")
    out = tmp_path / "p.json"
    args = _parse("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms),
                  "--model", "m-a", "--judge-model", "m-b", "--extract-model", "m-c",
                  "--no-paraphrase", "--no-reversal", "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text())
    now = load_bundle(f"skill:{skill}")
    assert payload["ref_digest"] == now.measured_digest() != extracted
    assert payload["atoms_ref_digest"] == extracted
    assert payload["bundle"]["entry_point_words"] == len(now.body.split())


def test_view_says_its_colours_come_from_spans_only(tmp_path, capsys):
    from dokimi.artifacts import save_artifact
    from dokimi.novelty.atoms import Atom, save_atoms
    from dokimi.novelty.spans import index_atoms, segment

    spans = segment("# H\n\nDo not enable the legacy driver.\n")
    atom = Atom(statement="No legacy driver.", question="q", answer="No.",
                span_ids=(spans[1].id,))
    index_atoms(spans, [atom])
    atoms_path = tmp_path / "atoms.json"
    save_atoms([atom], spans, atoms_path)

    novelty_path = tmp_path / "novelty.json"
    save_artifact("novelty", _minimal_artifact(), novelty_path)
    out = tmp_path / "annotated.md"
    args = _parse(
        "novelty", "view", "--novelty", str(novelty_path), "--atoms", str(atoms_path),
        "--format", "md", "--out", str(out),
    )
    args.func(args)
    assert "legacy driver" in out.read_text()
    assert "colours here come from spans" in capsys.readouterr().out


def _with_transport(client, transport):
    client.transport = transport
    return client


def _minimal_artifact() -> dict:
    empty = {
        "added": 0, "redundant": 0, "degraded": 0, "still_missing": 0,
        "n_paired": 0, "n_unpaired_dropped": 0, "ci95_added": None,
    }
    return {
        "generated_at": "2026-09-23T00:00:00Z",
        "artifact": {"kind": "skill", "ref": "skill:x", "ref_digest": None, "tokens": 10},
        "model": "model-a",
        "judge_model": "model-b",
        "atoms": {"n": 1, "n_probed": 0, "sampling": "full", "faithfulness": {}},
        "paired": {"lenient": dict(empty), "strict": dict(empty)},
        "k0": {"lenient": 0.0, "strict": 0.0},
        "spans": {"n": 2, "tokens": 10, "tokens_carrying_nothing": 4,
                  "frac_carrying_nothing": 0.4, "sole_carrier": 1, "protected": 1,
                  "by_kind": {"heading": 1, "sentence": 1}},
    }


def test_novelty_ref_digest_is_the_subject_scorecard_digest(tmp_path):
    # Both artifacts write `ref_digest`; a directory hashed two ways could never be matched.
    from dokimi.commands.novelty import _digest
    from dokimi.subjects.scorecard import _digest_ref

    (tmp_path / "references").mkdir()
    (tmp_path / "SKILL.md").write_text("---\nname: s\n---\nbody\n")
    (tmp_path / "references" / "a.md").write_text("ref\n")
    assert _digest(f"skill:{tmp_path}") == _digest_ref(str(tmp_path), base=tmp_path)


# ── Which files of the artifact a command measures ───────────────────────────

_BUNDLE_SKILL = """\
---
name: accel-tuning
description: Configure the accelerator
---

# Accelerator tuning

Enable the accelerator with `--accel`.
"""


def _bundle_skill(tmp_path):
    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(_BUNDLE_SKILL, encoding="utf-8")
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nNever exceed 64 threads.\n", encoding="utf-8")
    return root


def test_the_bundle_flags_are_on_extraction_too_not_only_on_analyze():
    """`atoms` once, `probe` per model is the cheap path; it has to see the bundle."""
    for sub in ("analyze", "atoms"):
        args = _parse("novelty", sub, "--artifact", "skill:x", "--include-resources",
                      "--resource-roles", "reference", "--max-file-words", "100")
        assert (args.include_resources, args.resource_roles) == (True, ["reference"])
        assert args.max_file_words == 100


def test_an_entry_point_only_run_says_what_it_will_not_measure(tmp_path, capsys):
    root = _bundle_skill(tmp_path)
    args = _parse("novelty", "atoms", "--artifact", f"skill:{root}")
    bundle = novelty_cmd._bundle_from_args(args)
    assert bundle.summary()["n_files"] == 1
    assert "will not be measured" in capsys.readouterr().err


def test_probing_takes_its_file_set_from_the_atoms_not_from_flags(tmp_path, capsys):
    root = _bundle_skill(tmp_path)
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    saved = {"bundle": {"include_resources": True, "n_files": 2,
                        "resource_words_measured": 6,
                        "files": [{"path": "SKILL.md", "role": "entry_point"},
                                  {"path": "references/tuning.md", "role": "reference"}]}}
    text = novelty_cmd._artifact_text_for_saved_atoms(args, saved)
    # The reference is injected even though no flag asked for it, because the answer key
    # was extracted from it.
    assert "Never exceed 64 threads" in text
    assert "2 files" in capsys.readouterr().err


def test_an_artifact_whose_files_changed_refuses_to_probe_a_stale_answer_key(tmp_path):
    root = _bundle_skill(tmp_path)
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    saved = {"bundle": {"include_resources": True, "n_files": 3,
                        "files": [{"path": "SKILL.md", "role": "entry_point"},
                                  {"path": "references/tuning.md", "role": "reference"},
                                  {"path": "references/gone.md", "role": "reference"}]}}
    with pytest.raises(SystemExit, match="files changed"):
        novelty_cmd._artifact_text_for_saved_atoms(args, saved)


def test_atoms_without_recorded_provenance_fall_back_and_warn(tmp_path, capsys):
    root = _bundle_skill(tmp_path)
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    text = novelty_cmd._artifact_text_for_saved_atoms(args, {})
    assert "Never exceed 64 threads" not in text
    assert "predates bundle provenance" in capsys.readouterr().err


def test_probing_reuses_a_bundle_extracted_with_larger_word_budgets(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    (root / "references" / "big.md").write_text("word " * 5000, encoding="utf-8")
    bundle = load_bundle(f"skill:{root}", include_resources=True, max_file_words=6000)
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    text = novelty_cmd._artifact_text_for_saved_atoms(args, {"bundle": bundle.summary()})
    assert text == bundle.text


def test_an_edited_bundled_file_refuses_to_probe_a_stale_answer_key(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nNever exceed 128 threads on driver 1.0.\n", encoding="utf-8")
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    with pytest.raises(SystemExit, match="files changed"):
        novelty_cmd._artifact_text_for_saved_atoms(args, {"bundle": bundle.summary()})


def test_a_shortened_entry_point_can_still_be_probed_with_the_original_atoms(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    (root / "SKILL.md").write_text(
        "---\nname: accel-tuning\ndescription: Configure the accelerator\n---\n\n"
        "Use `--accel`.\n", encoding="utf-8")
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    text = novelty_cmd._artifact_text_for_saved_atoms(args, {"bundle": bundle.summary()})
    assert "Use `--accel`." in text and "Never exceed 64 threads" in text


def test_shortening_a_bundle_is_restricted_to_the_file_it_rewrites(tmp_path, capsys):
    from dokimi.novelty.atoms import Atom, save_atoms
    from dokimi.novelty.bundle import load_bundle
    from dokimi.novelty.spans import segment

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    spans = segment(bundle.text)
    entry_end = bundle.files[0].end
    in_entry = next(s for s in spans if s.start < entry_end and "`--accel`" in s.text)
    in_reference = next(s for s in spans if s.start >= entry_end and "64" in s.text)
    atoms = [
        Atom(statement="s1", question="q1", answer="a1", span_ids=(in_entry.id,)),
        Atom(statement="s2", question="q2", answer="a2", span_ids=(in_reference.id,)),
    ]
    path = tmp_path / "atoms.json"
    save_atoms(atoms, spans, path, extractor="e", cap=None, bundle=bundle.summary())

    kept_spans, kept_atoms = novelty_cmd._entry_point_only(str(path), spans, atoms)
    assert all(span.start < entry_end for span in kept_spans)
    assert [atom.statement for atom in kept_atoms] == ["s1"]
    note = capsys.readouterr().err
    assert "bundle spans" in note and "SKILL.md" in note


def test_an_edit_that_keeps_the_word_count_still_refuses_to_probe(tmp_path):
    from dokimi.novelty.atoms import save_atoms
    from dokimi.novelty.bundle import load_bundle
    from dokimi.novelty.spans import segment

    root = _bundle_skill(tmp_path)
    reference = root / "references" / "tuning.md"
    reference.write_text("# Tuning\n\nNever exceed 64 threads.\n", encoding="utf-8")
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    atoms_path = tmp_path / "atoms.json"
    save_atoms([], segment(bundle.text), atoms_path, extractor="e", cap=None,
               bundle=bundle.summary())

    # Same path, same word count, different fact: the digest is what catches this.
    reference.write_text("# Tuning\n\nNever exceed 128 threads.\n", encoding="utf-8")
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}",
                  "--atoms", str(atoms_path))
    with pytest.raises(SystemExit, match="files changed"):
        novelty_cmd._artifact_text_for_saved_atoms(args, json.loads(
            atoms_path.read_text(encoding="utf-8")))


def test_the_digest_covers_what_was_measured_and_nothing_else(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    entry = f"skill:{root}"
    entry_only = novelty_cmd._digest(entry, bundle=load_bundle(entry))
    whole = novelty_cmd._digest(entry, bundle=load_bundle(entry, include_resources=True))
    # Hashing the directory gave both modes the same hash, so a stored result could not
    # say which one produced it.
    assert entry_only and whole and entry_only != whole

    # A sibling the run never read must not move the digest...
    (root / "unread.md").write_text("word " * 6000, encoding="utf-8")
    assert novelty_cmd._digest(entry, bundle=load_bundle(entry)) == entry_only
    # ...and a measured file's bytes must.
    (root / "references" / "tuning.md").write_text("Something else.\n", encoding="utf-8")
    assert novelty_cmd._digest(
        entry, bundle=load_bundle(entry, include_resources=True)) != whole


@pytest.mark.parametrize("added", ["notes.md", "scripts/run.sh"])
def test_probe_refuses_a_new_file_in_a_role_not_seen_at_extraction(tmp_path, added):
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    recorded = load_bundle(f"skill:{root}", include_resources=True)
    saved = {"bundle": dict(recorded.summary(), artifact_name=recorded.name)}
    # The extraction had only a reference, so the rebuild is filtered to that role and a
    # new file in any other role never reached the file-set comparison.
    (root / added).parent.mkdir(parents=True, exist_ok=True)
    (root / added).write_text("Always pass --force.\n", encoding="utf-8")
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    with pytest.raises(SystemExit, match=f"added: \\['{added}'\\]"):
        novelty_cmd._artifact_text_for_saved_atoms(args, saved)


def test_a_text_artifact_still_gets_a_path_digest(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n", encoding="utf-8")
    assert novelty_cmd._digest(f"text:{path}")


def test_an_answer_key_from_another_skill_is_refused(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\nname: other-skill\ndescription: Tune a database\n---\n\n# Other\n",
        encoding="utf-8")
    root = _bundle_skill(tmp_path)
    recorded = load_bundle(f"skill:{other}")
    saved = {"bundle": dict(recorded.summary(), artifact_name=recorded.name,
                            artifact_ref=f"skill:{other}",
                            measured_digest=recorded.measured_digest())}
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    # Both sides are just ["SKILL.md"], so the file-set check could never catch this.
    with pytest.raises(SystemExit, match="belongs to one artifact"):
        novelty_cmd._artifact_text_for_saved_atoms(args, saved)


def test_a_changed_entry_point_proceeds_but_says_so(tmp_path, capsys):
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    recorded = load_bundle(f"skill:{root}", include_resources=True)
    saved = {"bundle": dict(recorded.summary(), artifact_name=recorded.name)}
    (root / "SKILL.md").write_text(
        "---\nname: accel-tuning\ndescription: Configure the accelerator\n---\n\n"
        "# Accelerator tuning\n\nShortened: the flag is `--accel`.\n", encoding="utf-8")
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    text = novelty_cmd._artifact_text_for_saved_atoms(args, saved)
    assert "Shortened: the flag is" in text
    note = capsys.readouterr().err
    assert "has changed since these atoms were extracted" in note


def test_a_bundle_record_without_file_rows_is_refused(tmp_path):
    root = _bundle_skill(tmp_path)
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    for broken in ({"bundle": {"include_resources": True, "files": []}},
                   {"bundle": {"include_resources": True,
                               "files": [{"path": "SKILL.md"}]}}):
        with pytest.raises(SystemExit, match="provenance"):
            novelty_cmd._artifact_text_for_saved_atoms(args, broken)


def test_an_atom_whose_evidence_spans_two_files_is_not_scored_for_shortening(tmp_path):
    from dokimi.novelty.atoms import Atom, save_atoms
    from dokimi.novelty.bundle import load_bundle
    from dokimi.novelty.spans import segment

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    spans = segment(bundle.text)
    entry_end = bundle.files[0].end
    inside = next(s for s in spans if s.start < entry_end and "`--accel`" in s.text)
    outside = next(s for s in spans if s.start >= entry_end)
    atoms = [
        Atom(statement="entry only", question="q", answer="a", span_ids=(inside.id,)),
        Atom(statement="both files", question="q", answer="a",
             span_ids=(inside.id, outside.id)),
    ]
    path = tmp_path / "atoms.json"
    save_atoms(atoms, spans, path, extractor="e", cap=None, bundle=bundle.summary())
    _kept_spans, kept_atoms = novelty_cmd._entry_point_only(str(path), spans, atoms)
    # Half the evidence cannot be scored against a candidate that holds one file.
    assert [atom.statement for atom in kept_atoms] == ["entry only"]


def _saved(root, **kwargs):
    from dokimi.novelty.bundle import load_bundle

    recorded = load_bundle(f"skill:{root}", include_resources=True, **kwargs)
    return recorded, {"bundle": dict(recorded.summary(), artifact_name=recorded.name)}


def _reprobe(root, saved):
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", "a.json")
    return novelty_cmd._saved_atoms_bundle(args, saved)


def test_probe_keeps_a_budget_gap_a_gap(tmp_path):
    """Rebuilding with the selected files' roles relabelled an over-budget script `policy`."""
    root = _bundle_skill(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "big.py").write_text("x = 1  # w\n" * 6000, encoding="utf-8")
    recorded, saved = _saved(root)
    assert recorded.summary()["unmeasured_words"] > 0
    _, probed = _reprobe(root, saved)
    assert probed.summary()["unmeasured_words"] == recorded.summary()["unmeasured_words"]
    assert probed.summary()["policy_excluded_words"] == 0


def test_probe_replays_requested_roles_when_none_was_selected(tmp_path):
    root = _bundle_skill(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "big.py").write_text("x = 1  # w\n" * 6000, encoding="utf-8")
    recorded, saved = _saved(root, roles=("script",))
    assert [f.path for f in recorded.files] == ["SKILL.md"]
    _reprobe(root, saved)       # the unchanged tree is not "changed"


def test_probe_accepts_a_new_eval_case(tmp_path):
    root = _bundle_skill(tmp_path)
    _, saved = _saved(root)
    (root / "evals").mkdir()
    (root / "evals" / "q.md").write_text("Q: how many threads?\n", encoding="utf-8")
    _reprobe(root, saved)       # evals are not measured by default; the text is unchanged


def test_probe_refuses_a_new_eval_case_when_evals_were_requested(tmp_path):
    root = _bundle_skill(tmp_path)
    (root / "evals").mkdir()
    (root / "evals" / "a.md").write_text("Q: one?\n", encoding="utf-8")
    _, saved = _saved(root, roles=("reference", "eval"))
    (root / "evals" / "q.md").write_text("Q: two?\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="evals/q.md"):
        _reprobe(root, saved)


def test_item_analysis_refuses_a_file_that_carries_no_probe_runs(tmp_path):
    path = tmp_path / "novelty.json"
    path.write_text(json.dumps({"model": "m", "paired": {}}), encoding="utf-8")
    args = _parse("novelty", "items", "--probes", str(path))
    with pytest.raises(SystemExit, match="no probe runs"):
        args.func(args)


def test_item_analysis_over_three_models_reports_discrimination(tmp_path, capsys):
    def probes_file(name, outcomes):
        path = tmp_path / name
        path.write_text(json.dumps({"model": name.removesuffix(".json"), "runs": [
            {"atom_id": atom, "probe_kind": "direct", "arm": "full", "question": "q",
             "response": "r", "verdict": "correct_complete" if ok else "wrong_other"}
            for atom, ok in outcomes.items()
        ]}), encoding="utf-8")
        return str(path)

    files = [
        probes_file("strong.json", {"u": True, "hard": True, "f1": True}),
        probes_file("middle.json", {"u": True, "hard": False, "f1": True}),
        probes_file("weak.json", {"u": True, "hard": False, "f1": False}),
    ]
    out = tmp_path / "items.json"
    args = _parse("novelty", "items", "--probes", *files, "--out", str(out))
    args.func(args)

    payload = json.loads(out.read_text(encoding="utf-8"))
    lenient = payload["discrimination"]["lenient"]
    assert lenient["measured"] is True and lenient["n_atoms"] == 3
    rows = {row["atom_id"]: row for row in lenient["per_atom"]}
    assert rows["u"]["r"] is None            # every model knew it
    assert rows["hard"]["r"] > 0
    # Three respondents is below the floor's calibration, so the headline says so rather
    # than counting atoms against a cut that flags a third of them by chance.
    assert "floor not applied at 3 respondents" in capsys.readouterr().out


def test_item_analysis_of_one_model_prints_why_it_is_not_computable(tmp_path, capsys):
    path = tmp_path / "one.json"
    path.write_text(json.dumps({"model": "m1", "runs": [
        {"atom_id": "a1", "probe_kind": "direct", "arm": "full", "question": "q",
         "response": "r", "verdict": "correct_complete"}]}), encoding="utf-8")
    args = _parse("novelty", "items", "--probes", str(path))
    args.func(args)
    assert "at least 3" in capsys.readouterr().out


def test_two_runs_of_the_same_model_do_not_overwrite_each_other(tmp_path, capsys):
    def probes_file(name, model):
        path = tmp_path / name
        path.write_text(json.dumps({"model": model, "runs": [
            {"atom_id": "a1", "probe_kind": "direct", "arm": "full", "question": "q",
             "response": "r", "verdict": "correct_complete"},
            {"atom_id": "a1", "probe_kind": "paraphrase", "arm": "full", "question": "q2",
             "response": "r", "verdict": "wrong_other"},
        ]}), encoding="utf-8")
        return str(path)

    files = [probes_file("seed0.json", "m"), probes_file("seed1.json", "m")]
    out = tmp_path / "items.json"
    args = _parse("novelty", "items", "--probes", *files, "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    # Keyed by source, so two seeds of one model are two rows, not one.
    assert len(payload["kind_agreement"]) == 2
    assert {row["model"] for row in payload["kind_agreement"].values()} == {"m"}
    assert "seed0.json" in capsys.readouterr().out


@pytest.mark.parametrize("field", ["artifact", "ref_digest"])
def test_items_refuses_runs_of_different_artifacts(tmp_path, field):
    files = []
    for n, model in enumerate(("m1", "m2", "m3")):
        path = tmp_path / f"{model}.json"
        meta = {"artifact": "skill:a", "ref_digest": "sha256:aa"}
        if n == 2:
            meta[field] = "skill:b" if field == "artifact" else "sha256:bb"
        path.write_text(json.dumps({"model": model, **meta, "runs": [
            {"atom_id": "a1", "probe_kind": "direct", "arm": "full", "question": "q",
             "response": "r", "verdict": "correct_complete"}]}), encoding="utf-8")
        files.append(str(path))
    args = _parse("novelty", "items", "--probes", *files)
    with pytest.raises(SystemExit, match=f"different texts \\({field}"):
        args.func(args)


_UNREAD_SKILL = (
    "---\nname: s\ndescription: d\n---\n\n# H\n\n"
    "Enable the accelerator with the accel flag.\n\n"
    "The project began as a research prototype with many contributors over the years.\n\n"
    "Do not enable the legacy driver.\n"
)


def _unread_transport(prompt, system=None):
    """Extraction never parses the background span; the adjudicator is throttled."""
    import re

    if "Extract the checkable knowledge" in prompt:
        if "research prototype" in prompt:
            return '[{"statement": "The project', None
        rows = []
        for sid, text in re.findall(r"^\[(s\d+)\] \(\w+\) (.*)$", prompt, flags=re.M):
            if "accel flag" in text:
                rows.append({"statement": "The accelerator is enabled with the accel flag "
                             "in production.", "question": "How?", "answer": "accel",
                             "kind": "proposition", "tags": [], "span_ids": [sid]})
        return json.dumps(rows), None
    if "Can the STATEMENT be recovered" in prompt:
        raise RuntimeError("throttled")
    return json.dumps({"verdict": "supported"}), None


def test_unread_spans_travel_from_novelty_atoms_to_shorten(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_UNREAD_SKILL, encoding="utf-8")
    real_client = novelty_cmd._client
    monkeypatch.setattr(novelty_cmd, "_client", lambda model, provider, role, args:
                        _with_transport(real_client(model, provider, role, args),
                                        _unread_transport))
    atoms = tmp_path / "atoms.json"
    args = _parse("novelty", "atoms", "--artifact", f"skill:{skill}", "--out", str(atoms))
    args.func(args)
    captured = capsys.readouterr()
    assert "WARNING atom extraction could not read 1 of" in captured.err
    summary = json.loads(captured.out[captured.out.index("{"):])
    assert summary["n_unread"] == 1  # and the printed share leaves it out
    record = json.loads(atoms.read_text())["extraction"]
    assert len(record["unread_span_ids"]) == 1

    args = _parse("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(atoms),
                  "--no-paraphrase", "--no-reversal", "--out", str(tmp_path / "p.json"))
    args.func(args)
    assert "the atoms probed here leave out" in capsys.readouterr().err

    out = tmp_path / "short.md"
    args = _parse("novelty", "shorten", "--artifact", f"skill:{skill}", "--atoms", str(atoms),
                  "--budgets", "0.5", "--allow-unsafe", "--out", str(out))
    args.func(args)
    captured = capsys.readouterr()
    assert "1 of" in captured.err and "were never read by extraction" in captured.err
    assert "retention adjudications got no usable verdict" in captured.err
    assert "knowledge under-counted" in captured.out
    assert "research prototype" in out.read_text()  # an unread span is never cut


def test_view_and_probe_repeat_the_unread_warning(tmp_path, capsys):
    from dokimi.artifacts import save_artifact
    from dokimi.novelty.atoms import Atom, save_atoms
    from dokimi.novelty.spans import index_atoms, segment

    spans = segment("# H\n\nDo not enable the legacy driver.\n\nSome history here.\n")
    spans[2].unread = True
    atom = Atom(statement="No legacy driver.", question="q", answer="No.",
                span_ids=(spans[1].id,))
    index_atoms(spans, [atom])
    atoms_path = tmp_path / "atoms.json"
    save_atoms([atom], spans, atoms_path)
    novelty_path = tmp_path / "novelty.json"
    save_artifact("novelty", _minimal_artifact(), novelty_path)
    out = tmp_path / "annotated.md"
    args = _parse("novelty", "view", "--novelty", str(novelty_path), "--atoms",
                  str(atoms_path), "--format", "md", "--out", str(out))
    args.func(args)
    assert "render as `unread`" in capsys.readouterr().err
    assert f"`{spans[2].id}` [unread/hatched]" in out.read_text()

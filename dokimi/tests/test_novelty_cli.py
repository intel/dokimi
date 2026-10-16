# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI wiring for `novelty`: parsing, role defaults, and the guards that refuse a run."""

from __future__ import annotations

import json
import re

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)


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
    args = _parse("novelty", "analyze", "--artifact", "skill:x")
    extractor, answerer, judge = novelty_cmd._clients(args)
    assert answerer.model == DEFAULT_ANSWER_MODEL
    assert (judge.model, judge.provider) == (DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER)
    assert extractor.role == "extractor" and judge.role == "judge"


@pytest.mark.parametrize("model", [DEFAULT_JUDGE_MODEL, f"us.anthropic.{DEFAULT_JUDGE_MODEL}"])
def test_measuring_the_default_judge_moves_the_judge_to_the_other_family(model):
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--model", model,
                  "--provider", "amazon-bedrock")
    _extractor, answerer, judge = novelty_cmd._clients(args)
    assert answerer.model == model
    assert (judge.model, judge.provider) == (DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER)


def test_a_judge_provider_alone_picks_the_default_judge_that_runs_there():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--judge-provider", "amazon-bedrock")
    extractor, _answerer, judge = novelty_cmd._clients(args)
    assert (judge.model, judge.provider) == (DEFAULT_JUDGE_MODEL, "amazon-bedrock")
    assert (extractor.model, extractor.provider) == (DEFAULT_JUDGE_MODEL, "amazon-bedrock")


def test_a_judge_provider_alone_takes_the_generator_when_the_judge_is_measured():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--model", DEFAULT_JUDGE_MODEL,
                  "--provider", "anthropic", "--judge-provider", DEFAULT_GENERATOR_PROVIDER)
    _extractor, _answerer, judge = novelty_cmd._clients(args)
    assert (judge.model, judge.provider) == (DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER)


@pytest.mark.parametrize("argv", [
    # Measuring the default judge leaves only the generator, which Bedrock and anthropic
    # do not serve: the swap must not build bedrock/gpt-6.1-sol or anthropic/gpt-6.1-sol.
    ("--model", f"us.anthropic.{DEFAULT_JUDGE_MODEL}", "--provider", "amazon-bedrock",
     "--judge-provider", "amazon-bedrock"),
    ("--model", DEFAULT_JUDGE_MODEL, "--provider", "anthropic", "--judge-provider", "anthropic"),
    # The default answerer is the generator, so openai has no default judge left either.
    ("--judge-provider", "openai"),
])
def test_a_judge_provider_with_no_default_judge_there_exits(argv):
    args = _parse("novelty", "analyze", "--artifact", "skill:x", *argv)
    with pytest.raises(SystemExit, match="--judge-model"):
        novelty_cmd._clients(args)


def test_a_named_judge_without_a_provider_runs_on_the_answer_provider():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--provider", "openrouter",
                  "--judge-model", "j")
    _extractor, _answerer, judge = novelty_cmd._clients(args)
    assert (judge.model, judge.provider) == ("j", "openrouter")


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



def test_coverage_note_printed_to_stderr_for_atoms(tmp_path, monkeypatch, capsys):
    """The coverage note is printed to stderr on entry-point run."""
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    ref_file = skill.parent / "references" / "ref.md"
    ref_file.parent.mkdir()
    skill.write_text(
        "---\nname: s\ndescription: d\n---\n\n# H\n\nSome text.\n",
        encoding="utf-8",
    )
    # Create a reference file so there is bundled text
    ref_file.write_text("Reference text here.", encoding="utf-8")
    rows = [{"statement": "Some.", "question": "What?", "answer": "Text.", 
             "kind": "contract", "span_ids": ["s0002"]}]

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
    # Entry-point-only run will note that references exist and won't be measured
    args = _parse("novelty", "atoms", "--artifact", f"skill:{skill}", "--out", str(out))
    args.func(args)
    payload = json.loads(out.read_text())
    assert len(payload["atoms"]) >= 1
    # Check stderr for coverage note about bundled files not being measured
    captured = capsys.readouterr()
    assert "NOTE:" in captured.err
    assert "will not be measured" in captured.err


def test_answer_key_notes_printed_to_stderr_for_probe(tmp_path, monkeypatch, capsys):
    """Answer-key notes are printed to stderr."""
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: s\ndescription: d\n---\n\n# H\n\nSome text.\n",
        encoding="utf-8",
    )
    
    # Create atoms file
    rows = [{"statement": "Some.", "question": "What?", "answer": "Text.", 
             "kind": "contract", "span_ids": ["s0002"]}]

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
    
    # Now probe and edit the artifact - this should trigger answer-key notes
    skill.write_text(
        "---\nname: s\ndescription: d\n---\n\n# H\n\nEdited text.\n",
        encoding="utf-8",
    )
    
    args = _parse("novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(out),
                  "--no-paraphrase", "--no-reversal", "--out", str(tmp_path / "p.json"))
    args.func(args)
    
    # The probe command should have stderr output about changed files
    captured = capsys.readouterr()
    assert "has changed since" in captured.err


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
    # `atoms` extracts with the default judge, as `analyze` does, not with the answerer.
    assert (payload["extractor"], payload["cap"]) == (DEFAULT_JUDGE_MODEL, 120)

    probe = ["novelty", "probe", "--artifact", f"skill:{skill}", "--atoms", str(out),
             "--no-paraphrase", "--no-reversal", "--out", str(tmp_path / "p.json")]
    args = _parse(*probe)
    args.func(args)
    assert "own answer key" not in capsys.readouterr().err

    # Measuring the model that extracted the atoms means it wrote its own answer key.
    args = _parse(*probe, "--model", DEFAULT_JUDGE_MODEL, "--provider", DEFAULT_JUDGE_PROVIDER)
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


def test_probe_refuses_a_stale_answer_key_before_building_a_client(tmp_path):
    """The wiring: a refused verdict exits with its message; the verdicts themselves are
    tested at `check_answer_key` (tests/test_novelty_bundle.py)."""
    from dokimi.novelty.atoms import save_atoms
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    recorded = load_bundle(f"skill:{root}", include_resources=True)
    atoms = tmp_path / "atoms.json"
    save_atoms([], [], atoms, extractor="e", cap=None, bundle=recorded.summary())
    (root / "references" / "tuning.md").write_text("Never exceed 128 threads.\n",
                                                   encoding="utf-8")
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", str(atoms))
    with pytest.raises(SystemExit, match="files changed"):
        args.func(args)


def test_shorten_restricts_a_bundle_answer_key_to_the_entry_point(tmp_path, capsys):
    from dokimi.novelty.atoms import save_atoms
    from dokimi.novelty.bundle import load_bundle
    from dokimi.novelty.spans import segment

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    atoms = tmp_path / "atoms.json"
    save_atoms([], segment(bundle.text), atoms, extractor="e", cap=None,
               bundle=bundle.summary())
    args = _parse("novelty", "shorten", "--artifact", f"skill:{root}", "--atoms", str(atoms),
                  "--lexical-only")
    args.func(args)
    assert "bundle spans" in capsys.readouterr().err


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
    assert re.search(r"knowledge under-counted: \d+ adjudications got no usable verdict\)",
                     captured.out)
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

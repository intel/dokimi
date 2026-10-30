# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI wiring for `novelty`: parsing, role defaults, and the guards that refuse a run."""

from __future__ import annotations

import inspect
import json
import os
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
    handlers = {
        "analyze": novelty_cmd.cmd_novelty_analyze,
        "atoms": novelty_cmd.cmd_novelty_atoms,
        "probe": novelty_cmd.cmd_novelty_probe,
        "shorten": novelty_cmd.cmd_novelty_shorten,
        "view": novelty_cmd.cmd_novelty_view,
    }
    for sub, handler in handlers.items():
        args = _parse("novelty", sub, *(
            ["--artifact", "skill:x"] if sub != "view" else ["--novelty", "n.json"]
        ), *(["--atoms", "a.json"] if sub in ("probe", "shorten", "view") else []))
        # The command that runs is the subcommand's own handler (`_stops_at_cap` wraps it).
        assert inspect.unwrap(args.func) is handler


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


def test_dropping_the_baseline_arm_is_refused(tmp_path):
    from dokimi.novelty.measure import parse_arms

    with pytest.raises(ValueError, match="paired"):
        parse_arms("full,placebo")
    assert parse_arms(" no_artifact, full,,shuffled") == ("no_artifact", "full", "shuffled")
    # The CLI exits with the same message.
    (tmp_path / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                       encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{tmp_path}", "--estimate-only",
                  "--arms", "full")
    with pytest.raises(SystemExit, match="^--arms must include no_artifact: the 2x2"):
        args.func(args)


def test_a_missing_artifact_exits_with_a_message(capsys):
    args = _parse("novelty", "analyze", "--artifact", "text:/nonexistent/skill.md")
    with pytest.raises(SystemExit, match="cannot load"):
        args.func(args)
    capsys.readouterr()



def _non_utf8_file(tmp_path):
    path = tmp_path / "bin.txt"
    path.write_bytes(b"\xff\xfe\x00")
    return path


@pytest.mark.parametrize("make_path", [
    lambda tmp_path: tmp_path / "missing.txt",
    lambda tmp_path: tmp_path,
    _non_utf8_file,
], ids=["missing", "directory", "not-utf8"])
def test_an_unreadable_placebo_text_exits_with_a_message(tmp_path, make_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                    encoding="utf-8")
    placebo = make_path(tmp_path)
    args = _parse("novelty", "analyze", "--artifact", f"skill:{skill}",
                  "--placebo-text", str(placebo))
    with pytest.raises(SystemExit, match=f"^cannot read --placebo-text {re.escape(str(placebo))}"):
        args.func(args)


@pytest.mark.parametrize("sub", ["analyze", "probe"])
def test_placebo_text_is_read_before_a_model_client_is_built(tmp_path, monkeypatch, sub):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                    encoding="utf-8")
    argv = ["novelty", sub, "--artifact", f"skill:{skill}",
            "--placebo-text", str(tmp_path / "missing.txt")]
    if sub == "probe":
        atoms = tmp_path / "atoms.json"
        atoms.write_text('{"atoms": [], "spans": [], "bundle": {}}', encoding="utf-8")
        argv += ["--atoms", str(atoms)]

    def built(*_args, **_kwargs):
        raise AssertionError("a model client was built before --placebo-text was read")

    monkeypatch.setattr("dokimi.commands.novelty._clients", built)
    args = _parse(*argv)
    with pytest.raises(SystemExit, match="^cannot read --placebo-text"):
        args.func(args)


@pytest.mark.parametrize(("arms", "placebo", "message"), [
    ("no_artifact,full,placebo", "", "^the placebo arm needs --placebo-text"),
    ("no_artifact,full,placebo", None, "^the placebo arm needs --placebo-text"),
    ("no_artifact,full,bogus", None, r"^unknown novelty arms: \['bogus'\]"),
])
def test_analyze_exits_with_the_message_when_the_arms_cannot_run(
        tmp_path, monkeypatch, arms, placebo, message):
    from tests.test_novelty_analyze import SKILL

    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
    argv = ["novelty", "analyze", "--artifact", f"skill:{skill}", "--arms", arms]
    if placebo is not None:
        (tmp_path / "placebo.txt").write_text(placebo, encoding="utf-8")
        argv += ["--placebo-text", str(tmp_path / "placebo.txt")]

    def built(*_args, **_kwargs):
        raise AssertionError("a model client was built for arms that cannot run")

    monkeypatch.setattr("dokimi.commands.novelty._clients", built)
    args = _parse(*argv)
    with pytest.raises(SystemExit, match=message):
        args.func(args)


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


def test_probe_exits_with_a_message_on_an_unreadable_placebo_text(tmp_path):
    from dokimi.novelty.atoms import save_atoms
    from dokimi.novelty.bundle import load_bundle

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    atoms = tmp_path / "atoms.json"
    save_atoms([], [], atoms, extractor="e", cap=None, bundle=bundle.summary())
    missing = tmp_path / "missing.txt"
    args = _parse("novelty", "probe", "--artifact", f"skill:{root}", "--atoms", str(atoms),
                  "--placebo-text", str(missing))
    with pytest.raises(SystemExit, match=f"^cannot read --placebo-text {re.escape(str(missing))}"):
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


def test_shorten_exits_with_a_message_on_a_bundle_block_it_cannot_slice(tmp_path):
    """A malformed ``bundle`` block (a file row without ``end``) is an exit naming the file, not a traceback."""
    from dokimi.novelty.atoms import save_atoms
    from dokimi.novelty.bundle import load_bundle
    from dokimi.novelty.spans import segment

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    atoms = tmp_path / "atoms.json"
    save_atoms([], segment(bundle.text), atoms, extractor="e", cap=None, bundle=bundle.summary())
    saved = json.loads(atoms.read_text(encoding="utf-8"))
    del saved["bundle"]["files"][0]["end"]
    atoms.write_text(json.dumps(saved), encoding="utf-8")
    args = _parse("novelty", "shorten", "--artifact", f"skill:{root}", "--atoms", str(atoms),
                  "--lexical-only")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert str(exc.value) == f"{atoms}: the atoms record no span range for SKILL.md"


def test_item_analysis_refuses_a_file_that_carries_no_probe_runs(tmp_path):
    path = tmp_path / "novelty.json"
    path.write_text(json.dumps({"model": "m", "paired": {}}), encoding="utf-8")
    args = _parse("novelty", "items", "--probes", str(path))
    with pytest.raises(SystemExit, match="no probe runs"):
        args.func(args)


@pytest.mark.parametrize("content", [
    None, "{not json", b"\xff\xfe\x00bad",
    pytest.param("[" * 100000 + "]" * 100000, id="nested_deeply"),
])
def test_item_analysis_exits_with_a_message_on_an_unreadable_probes_file(tmp_path, content):
    path = tmp_path / "novelty.json"
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    elif content is not None:
        path.write_bytes(content)
    args = _parse("novelty", "items", "--probes", str(path))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert str(exc.value).startswith(str(path))  # 3.14 parses deep nesting, then refuses the list


@pytest.mark.parametrize("top, run", [
    ({"model": ["m"]}, {}),
    ({"model": None}, {}),
    ({"artifact": {"a": 1}}, {}),
    ({"ref_digest": [1]}, {}),
    ({}, {"atom_id": ["a"]}),
    ({}, {"probe_kind": ["x"]}),
    ({}, {"arm": {}}),
    ({}, {"verdict": ["x"]}),
], ids=["model", "model_null", "artifact", "ref_digest", "atom_id", "probe_kind", "arm", "verdict"])
def test_item_analysis_refuses_a_probe_field_that_is_not_a_string(tmp_path, top, run):
    path = tmp_path / "novelty.json"
    body = {"model": "m", "runs": [{"atom_id": "a1", "probe_kind": "direct", "arm": "full",
                                    "verdict": "correct", **run}], **top}
    path.write_text(json.dumps(body), encoding="utf-8")
    args = _parse("novelty", "items", "--probes", str(path))
    with pytest.raises(SystemExit, match="not a string"):
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


def test_item_analysis_warns_when_the_cells_cover_different_atoms(tmp_path, capsys):
    files = []
    for model, atoms in (("m1", ("a1", "a2")), ("m2", ("a1",))):
        path = tmp_path / f"{model}.json"
        path.write_text(json.dumps({"model": model, "runs": [
            {"atom_id": atom, "probe_kind": "direct", "arm": "full", "question": "q",
             "response": "r", "verdict": "correct_complete"} for atom in atoms]}),
            encoding="utf-8")
        files.append(str(path))
    args = _parse("novelty", "items", "--probes", *files)
    args.func(args)
    assert "WARNING: these runs do not cover the same atom set" in capsys.readouterr().err


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


def test_the_headline_states_the_measured_share_and_warns_on_stdout_at_half_or_less(capsys):
    record = {"include_resources": False, "n_files": 1, "entry_point_words": 10,
              "resource_words_measured": 0, "resource_words_on_disk": 30,
              "policy_excluded_words": 0, "unreadable_files": 0, "excluded": [],
              "coverage_basis": "readable"}
    artifact = dict(_minimal_artifact(), bundle=record)
    novelty_cmd._print_headline(artifact)
    out = capsys.readouterr().out
    assert "coverage: 25% of the artifact's readable words were measured (entry point only)" in out
    assert "WARNING: only 25% of the artifact's readable words" in out
    assert "pass --include-resources" in out

    # The line is the analyzer's own: exactly half warns, just over half does not.
    novelty_cmd._print_headline(dict(artifact, bundle=dict(record, entry_point_words=30)))
    out = capsys.readouterr().out
    assert "coverage: 50%" in out and "WARNING: only" in out
    novelty_cmd._print_headline(dict(artifact, bundle=dict(record, entry_point_words=31)))
    out = capsys.readouterr().out
    assert "coverage: 50%" in out and "WARNING: only" not in out

    # A file that did not decode leaves the share unknowable, and the headline says so,
    # with the scope and, while the entry point is half or less of the words, the warning.
    novelty_cmd._print_headline(dict(artifact, bundle=dict(record, unreadable_files=1)))
    out = capsys.readouterr().out
    assert "coverage: unknown" in out and out.count("(entry point only)") == 1
    assert "WARNING: the entry point is half or less" in out
    assert "pass --include-resources" in out
    # An entry point that is most of the words does not warn, as the stderr warning does not.
    novelty_cmd._print_headline(dict(artifact, bundle=dict(record, unreadable_files=1,
                                                           entry_point_words=90,
                                                           resource_words_on_disk=10)))
    out = capsys.readouterr().out
    assert "coverage: unknown" in out and "WARNING" not in out


def _readable_record(**over):
    return dict({"include_resources": False, "n_files": 1, "entry_point_words": 10,
                 "resource_words_measured": 0, "resource_words_on_disk": 30,
                 "policy_excluded_words": 0, "unreadable_files": 0, "excluded": [],
                 "coverage_basis": "readable"}, **over)


def test_an_undecodable_file_in_a_bundle_run_does_not_claim_an_entry_point_only_warning(capsys):
    # --include-resources was passed and every counted word was read: the unknown-share line
    # must not say the entry point is half or less of the words, which is false for a bundle run.
    record = _readable_record(include_resources=True, unreadable_files=1,
                              resource_words_measured=30)
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record))
    out = capsys.readouterr().out
    assert "coverage: unknown" in out and "WARNING" not in out


def test_an_undecodable_file_in_a_capped_bundle_run_still_warns(capsys):
    # The budgets left 30 of the 40 counted words unread; the undecoded file's words are unread
    # too, so this run read at most a quarter, and stdout says so as it does without that file.
    record = _readable_record(include_resources=True, unreadable_files=1)
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record))
    out = capsys.readouterr().out
    assert "coverage: unknown" in out
    assert "WARNING: what this run read is half or less of the artifact's readable words" in out
    assert "entry point" not in out and "--include-resources" not in out


def test_an_unknown_share_warns_at_exactly_half_as_the_stderr_warning_does(capsys):
    record = _readable_record(unreadable_files=1, entry_point_words=50, resource_words_on_disk=50)
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record))
    assert "WARNING: the entry point is half or less" in capsys.readouterr().out


@pytest.mark.parametrize("entry, printed", [(29, "29%"), (57, "57%"), (58, "58%"),
                                            (49, "49%"), (1, "1%")])
def test_the_printed_percentage_is_the_share_not_a_float_product(capsys, entry, printed):
    # 29 entry words in 100: 0.29 * 100 is 28.999999999999996, which int() floors to 28.
    record = {"include_resources": False, "n_files": 1, "entry_point_words": entry,
              "resource_words_measured": 0, "resource_words_on_disk": 100 - entry,
              "policy_excluded_words": 0, "unreadable_files": 0, "excluded": [],
              "coverage_basis": "readable"}
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record))
    out = capsys.readouterr().out
    assert f"coverage: {printed} of the artifact's readable words" in out


def test_a_tiny_non_zero_share_prints_as_under_one_percent(capsys):
    record = {"include_resources": False, "n_files": 1, "entry_point_words": 1,
              "resource_words_measured": 0, "resource_words_on_disk": 9999,
              "policy_excluded_words": 0, "unreadable_files": 0, "excluded": [],
              "coverage_basis": "readable"}
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record))
    out = capsys.readouterr().out
    assert "coverage: <1% of the artifact's readable words were measured (entry point only)" in out
    assert "WARNING: only <1% of the artifact's readable words" in out


def test_a_capped_run_s_headline_does_not_say_the_whole_artifact_was_measured(capsys):
    # The entry point is every readable word, but the atoms stopped at 22.97% of its tokens.
    reached = {"last_span": "s0012", "spans": 12, "of": 61, "tokens_share": 0.2297}
    record = _readable_record(resource_words_on_disk=0)
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record,
                                     extraction={"reached": reached}))
    out = capsys.readouterr().out
    assert ("coverage: 100% of the artifact's readable words went into the input (entry point "
            "only); the capped atoms come from the first 22% of its tokens\n") in out
    assert "measured" not in out
    # The unknown share keeps the reach too; a run the cap did not stop keeps its wording.
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=dict(record, unreadable_files=1),
                                     extraction={"reached": reached}))
    assert "counted) (entry point only); the capped atoms come from the first 22% of its " \
           "tokens\n" in capsys.readouterr().out
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record,
                                     extraction={"reached": None}))
    assert "coverage: 100% of the artifact's readable words were measured (entry point only)\n" \
           in capsys.readouterr().out


def test_a_capped_run_under_the_floor_warns_in_the_coverage_line_s_words(capsys):
    # 10 of the 40 readable words went into the input, and the atoms stopped short of them.
    reached = {"last_span": "s0002", "spans": 2, "of": 5, "tokens_share": 0.3571}
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=_readable_record(),
                                     extraction={"reached": reached}))
    out = capsys.readouterr().out
    assert ("WARNING: only 25% of the artifact's readable words went into the input, so every "
            "rate here describes that part, not the artifact") in out
    assert "measured" not in out


def test_a_bundle_run_capped_by_the_budget_warns_without_advising_the_flag_it_passed(capsys):
    # --include-resources was passed; the budget left most of the reference unread. The
    # stderr warning list is silent about this, the stdout line is not, and it must not tell
    # the user to pass a flag they already passed.
    record = {"include_resources": True, "n_files": 2, "entry_point_words": 100,
              "resource_words_measured": 50, "resource_words_on_disk": 500,
              "policy_excluded_words": 0, "unreadable_files": 0, "excluded": [],
              "coverage_basis": "readable"}
    novelty_cmd._print_headline(dict(_minimal_artifact(), bundle=record))
    out = capsys.readouterr().out
    assert "coverage: 25% of the artifact's readable words were measured\n" in out
    assert "WARNING: only 25%" in out
    assert "--include-resources" not in out


def _deep_skill(tmp_path, name="deep"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: " + "[" * 5000 + "\n---\n\nBody.\n", encoding="utf-8")
    return f"skill:{root}"


def test_load_artifact_or_exit_names_a_frontmatter_nested_too_deeply(tmp_path):
    from dokimi.novelty.artifact import load_artifact_or_exit

    with pytest.raises(SystemExit, match="^cannot load --artifact skill:.*maximum recursion"):
        load_artifact_or_exit(_deep_skill(tmp_path))


@pytest.mark.parametrize("sub", ["analyze", "atoms", "probe", "shorten", "routing"])
def test_a_command_given_a_frontmatter_nested_too_deeply_exits_with_a_message(tmp_path, sub):
    atoms = tmp_path / "atoms.json"
    atoms.write_text('{"atoms": [], "spans": []}', encoding="utf-8")
    extra = {
        "probe": ["--atoms", str(atoms)],
        "shorten": ["--atoms", str(atoms)],
        "routing": ["--competitors", _deep_skill(tmp_path, "other")],
    }.get(sub, [])
    args = _parse("novelty", sub, "--artifact", _deep_skill(tmp_path), *extra)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert "maximum recursion" in str(exc.value)
    assert "RecursionError" not in str(exc.value)


def test_all_vs_all_names_a_competitor_nested_too_deeply_and_exits_with_a_message(tmp_path):
    queries = tmp_path / "q.json"
    queries.write_text('{"deep": ["a request"]}', encoding="utf-8")
    args = _parse("novelty", "routing", "--all-vs-all", "--competitors", _deep_skill(tmp_path),
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit, match="maximum recursion") as exc:
        args.func(args)
    assert "RecursionError" not in str(exc.value)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any file")
def test_all_vs_all_names_an_unreadable_competitor_and_exits_with_a_message(tmp_path):
    root = tmp_path / "locked"
    root.mkdir()
    skill = root / "SKILL.md"
    skill.write_text("---\nname: locked\ndescription: x\n---\n\nBody.\n", encoding="utf-8")
    skill.chmod(0)
    queries = tmp_path / "q.json"
    queries.write_text('{"locked": ["a request"]}', encoding="utf-8")
    args = _parse("novelty", "routing", "--all-vs-all", "--competitors", f"skill:{root}",
                  "--queries-by-skill", str(queries), "--out", str(tmp_path / "o.json"))
    try:
        with pytest.raises(SystemExit, match="cannot load the field"):
            args.func(args)
    finally:
        skill.chmod(0o644)


def test_item_analysis_accepts_a_probe_file_that_records_no_digest(tmp_path):
    from dokimi.novelty.items import load_probe_cells

    path = tmp_path / "novelty.json"
    path.write_text(json.dumps({"model": "m", "artifact": "skill:x", "ref_digest": None, "runs": [
        {"atom_id": "a1", "probe_kind": "direct", "arm": "full", "verdict": "correct"}]}),
        encoding="utf-8")
    cells, _ = load_probe_cells([str(path)])
    assert (cells[0]["artifact"], cells[0]["ref_digest"]) == ("skill:x", None)

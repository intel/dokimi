# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""End-to-end `analyze` with every model call stubbed, plus the artifact's schema."""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import ArtifactValidationError, load_artifact, save_artifact, validate_artifact
from dokimi.gate.novelty_gate import evaluate_novelty
from dokimi.novelty.analyze import AnalyzeConfig, analyze, load_artifact_text
from dokimi.novelty.client import LLMClient
from dokimi.novelty.report import render_novelty_markdown

SKILL = """\
---
name: accel-tuning
description: Configure the accelerator for training runs
---

# Accelerator tuning

Enable the accelerator with `--accel`. Do not enable it on driver 1.0.

## Background

The project began as a research prototype with many contributors.
"""

_ATOMS = [
    {
        "statement": "The --accel flag enables the accelerator.",
        "question": "Which flag enables the accelerator?",
        "answer": "--accel",
        "kind": "proposition",
        "tags": ["procedural"],
        "span_ids": ["s0002"],
    },
    {
        "statement": "The accelerator must not be enabled on driver 1.0.",
        "question": "May the accelerator be enabled on driver 1.0?",
        "answer": "No.",
        "kind": "contract",
        "tags": ["negative_constraint", "version_bound"],
        "span_ids": ["s0003"],
    },
]

_GRAPH = {
    "nodes": [
        {"id": "n1", "text": "turn the accelerator on", "type": "action", "path_class": "core"},
        {"id": "n2", "text": "driver version gate", "type": "constraint",
         "path_class": "rare_critical"},
        {"id": "n3", "text": "project history", "type": "evidence",
         "path_class": "out_of_scope"},
    ],
    "edges": [{"from": "n1", "to": "n2", "kind": "requires"}],
}


def _scripted(kind: str):
    """A transport that answers by prompt shape, so no network is touched.

    ``kind="informed"`` answers correctly only when the artifact is in the prompt, which
    is what makes both atoms come out `added`.
    """

    def transport(prompt: str, system: str | None):
        if "Extract the checkable knowledge" in prompt:
            return json.dumps(_ATOMS), None
        if "Does the source text below support" in prompt:
            return json.dumps({"verdict": "supported", "why": "verbatim"}), None
        if "TASK NAME:" in prompt:
            return json.dumps(_GRAPH), None
        if "Reword each question" in prompt:
            questions = prompt.split("QUESTIONS:", 1)[1].strip().splitlines()
            # Drop the "1. " list numbering the prompt adds, as a real generator does.
            questions = [q.split(". ", 1)[1] for q in questions if q.strip()]
            return json.dumps([f"Reworded: {q}" for q in questions]), None
        if "Rewrite each statement" in prompt:
            statements = prompt.split("STATEMENTS:", 1)[1].strip().splitlines()
            statements = [s.split(". ", 1)[1] for s in statements if s.strip()]
            # A plausible generator widens the claim instead of stacking negations, which
            # is what the reversal prompt asks for.
            flipped = [f"Is it safe to ignore any of this: {s}?" for s in statements]
            return json.dumps(flipped), None
        if "judge how it functions" in prompt:
            rows = [
                {"n": 1, "needed": True, "scaffolding": False},
                {"n": 2, "needed": True, "scaffolding": False},
            ]
            return json.dumps(rows), None
        if "Find pairs of statements" in prompt:
            return json.dumps({"conflicts": []}), None
        if "Grade the RESPONSE" in prompt or "false premise" in prompt:
            informed = "INFORMED" in prompt
            verdict = "correct_complete" if informed else "wrong_other"
            return json.dumps({"verdict": verdict, "why": "stub"}), None
        if prompt.startswith("Reference material:"):
            return "INFORMED answer\nconfidence: 0.8", None
        if prompt.startswith("Question:"):
            return "I am not sure\nconfidence: 0.2", None
        return json.dumps({"verdict": "retained"}), None

    assert kind == "informed"
    return transport


def _clients():
    transport = _scripted("informed")
    return (
        LLMClient(model="extractor-m", role="extractor", transport=transport),
        LLMClient(model="answerer-m", role="answerer", transport=transport),
        LLMClient(model="judge-m", role="judge", transport=transport),
    )


def _analyze(tmp_path, **overrides):
    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(
        artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
        description=description, frontmatter=frontmatter, concurrency=1, **overrides,
    )
    extractor, answerer, judge = _clients()
    return analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)


def test_a_self_judged_run_is_refused_before_any_extraction_call(tmp_path):
    """The judge check runs before the first paid call, not after the extraction."""
    from dokimi.novelty.measure import SelfJudgeError

    calls = []

    def counting(prompt, system):
        calls.append(prompt)
        return ("x", None)

    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(
        artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
        description=description, frontmatter=frontmatter, concurrency=1,
    )
    extractor = LLMClient(model="same-m", role="extractor", transport=counting)
    answerer = LLMClient(model="same-m", role="answerer", transport=counting)
    judge = LLMClient(model="same-m", role="judge", transport=counting)
    with pytest.raises(SelfJudgeError):
        analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)
    assert calls == []


def test_skill_reference_splits_frontmatter_from_body(tmp_path):
    path = tmp_path / "accel-tuning" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    assert (kind, name) == ("skill", "accel-tuning")
    assert "Configure the accelerator" in description
    assert "name: accel-tuning" in frontmatter
    assert body.lstrip().startswith("# Accelerator tuning")


def test_skill_frontmatter_is_kept_verbatim_so_a_candidate_still_loads(tmp_path):
    import yaml

    from dokimi.skills.loader import load_skill

    head = '---\nname: s\ndescription: "Use when: tuning"\nlicense: MIT\n---'
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "SKILL.md").write_text(f"{head}\n# Body\n", encoding="utf-8")
    frontmatter = load_artifact_text(f"skill:{tmp_path / 's'}")[3]
    assert frontmatter == head
    yaml.safe_load(frontmatter.strip("-"))
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "SKILL.md").write_text(f"{frontmatter}\n\nbody\n", encoding="utf-8")
    assert load_skill(tmp_path / "c").description == "Use when: tuning"


def test_a_plain_text_artifact_has_no_description(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nAlways pin the driver.\n", encoding="utf-8")
    kind, _name, description, frontmatter, body = load_artifact_text(f"text:{path}")
    assert (kind, description, frontmatter) == ("text", "", "")
    assert "pin the driver" in body


def test_end_to_end_artifact_validates_and_reports_added_knowledge(tmp_path):
    result = _analyze(tmp_path)
    artifact = result.artifact
    validate_artifact("novelty", dict(artifact, schema_version="novelty.v1"))
    assert artifact["atoms"]["n"] == 2
    assert artifact["paired"]["lenient"]["added"] > 0
    assert artifact["paired"]["strict"]["added"] > 0
    assert artifact["k0"]["lenient"] == 0.0      # the baseline arm answered nothing right
    assert artifact["k0"]["strict"] == 0.0
    assert artifact["model"] == "answerer-m"
    assert artifact["judge_model"] == "judge-m"
    assert artifact["categories"]["novel_useful"] == 2
    assert artifact["conflict_coverage"]["pairs_total"] == 1
    assert (
        artifact["conflict_coverage"]["pairs_compared"]
        == artifact["conflict_coverage"]["pairs_total"]
    )
    # the kind/tag cut is present under both scorings, even when every slice is thin
    kinds = artifact["by_knowledge_kind"]
    assert set(kinds) == {"lenient", "strict"}
    assert kinds["strict"]["by_kind"]
    assert artifact["spans"]["capped_atom_set"] is False


def test_the_probe_budget_reports_what_the_fidelity_check_threw_away(tmp_path):
    result = _analyze(tmp_path)
    probes = result.artifact["probes"]
    assert probes["n_probes"] >= 2
    assert set(probes["by_kind"]) == {"paraphrase", "reversal"}
    assert probes["malformed_rate"] == 0.0
    validate_artifact("novelty", dict(result.artifact, schema_version="novelty.v1"))


def test_a_generator_that_drops_the_version_bound_is_dropped_and_warned_about(tmp_path):
    transport = _scripted("informed")

    def unfaithful(prompt, system):
        if "Reword each question" in prompt:
            # The second rewording loses the driver version its atom is about.
            return json.dumps([
                "Which command-line option turns the accelerator on?",
                "May it be enabled?",
            ]), None
        return transport(prompt, system)[0], None

    path = tmp_path / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    _, answerer, judge = _clients()
    extractor = LLMClient(model="extractor-m", role="extractor", transport=unfaithful)
    artifact = analyze(body, config, extractor=extractor, answerer=answerer,
                       judge=judge).artifact
    paraphrase = artifact["probes"]["by_kind"]["paraphrase"]
    assert (paraphrase["kept"], paraphrase["rejected"]) == (1, 1)
    assert paraphrase["reasons"]["anchor_dropped"] == 1
    assert any("paraphrase wordings failed the fidelity check" in w
               for w in artifact["warnings"])


def test_a_bundle_run_reports_which_file_carried_each_atom(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nSet `--threads` to the core count.\n", encoding="utf-8")

    bundle = load_bundle(f"skill:{root}", include_resources=True)
    config = AnalyzeConfig(
        artifact_ref=f"skill:{root}", artifact_kind=bundle.kind, name=bundle.name,
        description=bundle.description, frontmatter=bundle.frontmatter, concurrency=1,
        include_resources=True,
    )
    extractor, answerer, judge = _clients()
    artifact = analyze(bundle.text, config, extractor=extractor, answerer=answerer,
                       judge=judge, artifact_bundle=bundle).artifact

    reported = artifact["bundle"]
    assert reported["include_resources"] is True
    assert reported["n_files"] == 2
    assert reported["outside_share"] > 0
    assert {row["source"] for row in reported["by_source"]} <= {"SKILL.md",
                                                                "references/tuning.md"}
    assert sum(row["n_atoms"] for row in reported["by_source"]) == artifact["atoms"]["n"]
    assert artifact["run_config"]["include_resources"] is True
    # One meaning for `ref_digest`: the files this cell measured, as the record says.
    assert artifact["artifact"]["ref_digest"] == bundle.measured_digest()
    assert reported["measured_digest"] == bundle.measured_digest()
    validate_artifact("novelty", dict(artifact, schema_version="novelty.v1"))


def test_a_bundle_file_extraction_never_read_is_unread_not_empty(tmp_path):
    import re

    from dokimi.novelty.bundle import load_bundle
    from dokimi.novelty.spans import segment

    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nSet `--threads` to the core count.\n", encoding="utf-8")
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    in_tuning = {s.id for s in segment(bundle.text)
                 if bundle.source_of(s.start) == "references/tuning.md"}
    assert in_tuning
    scripted = _scripted("informed")

    def ceiling(prompt, system):
        # Every multi-span reply is cut; a span of tuning.md never parses even alone.
        if "Extract the checkable knowledge" not in prompt:
            return scripted(prompt, system)
        ids = set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M))
        rows = json.dumps([a for a in _ATOMS if set(a["span_ids"]) & ids])
        return ((rows[: len(rows) // 2] or "[") if len(ids) > 1 or ids & in_tuning
                else rows), None

    config = AnalyzeConfig(
        artifact_ref=f"skill:{root}", artifact_kind=bundle.kind, name=bundle.name,
        description=bundle.description, frontmatter=bundle.frontmatter, concurrency=1,
        include_resources=True,
    )
    _, answerer, judge = _clients()
    extractor = LLMClient(model="extractor-m", role="extractor", transport=ceiling)
    artifact = analyze(bundle.text, config, extractor=extractor, answerer=answerer,
                       judge=judge, artifact_bundle=bundle).artifact
    rows = {row["source"]: row for row in artifact["bundle"]["by_source"]}
    tuning = rows["references/tuning.md"]
    assert tuning["n_atoms"] is None and tuning["unread"] is True
    assert tuning["n_spans_unread"] == len(in_tuning)
    assert rows["SKILL.md"]["n_atoms"] == artifact["atoms"]["n"]
    assert "unread" not in rows["SKILL.md"] and "n_spans_unread" not in rows["SKILL.md"]
    validate_artifact("novelty", dict(artifact, schema_version="novelty.v1"))
    assert "| `references/tuning.md` | reference |" in render_novelty_markdown(artifact)
    assert "| not read |" in render_novelty_markdown(artifact)


def test_an_entry_point_only_run_says_how_much_of_the_artifact_it_ignored(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "references" / "big.md").write_text("word " * 500, encoding="utf-8")

    bundle = load_bundle(f"skill:{root}")
    config = AnalyzeConfig(
        artifact_ref=f"skill:{root}", artifact_kind=bundle.kind, name=bundle.name,
        description=bundle.description, frontmatter=bundle.frontmatter, concurrency=1,
    )
    extractor, answerer, judge = _clients()
    artifact = analyze(bundle.text, config, extractor=extractor, answerer=answerer,
                       judge=judge, artifact_bundle=bundle).artifact

    assert artifact["bundle"]["outside_share"] > 0.5
    assert artifact["bundle"]["resource_words_measured"] == 0
    assert any("were not measured (--include-resources)" in w
               for w in artifact["warnings"])


def test_artifact_round_trips_through_save_and_load(tmp_path):
    result = _analyze(tmp_path)
    out = tmp_path / "novelty.json"
    save_artifact("novelty", result.artifact, out)
    loaded = load_artifact("novelty", out)
    assert loaded["schema_version"] == "novelty.v1"
    assert loaded["paired"]["lenient"]["n_paired"] == result.artifact["paired"]["lenient"][
        "n_paired"
    ]


def test_caveats_and_markdown_state_the_limits(tmp_path):
    result = _analyze(tmp_path)
    markdown = render_novelty_markdown(result.artifact)
    caveats = " ".join(result.artifact["caveats"]).lower()
    assert "model" in caveats  # the result is a property of the (artifact, model) pair
    assert "novelty" in markdown.lower()
    assert "answerer-m" in markdown


def test_an_extractor_that_fails_every_batch_fails_the_run(tmp_path):
    import pytest

    from dokimi.novelty.atoms import ExtractionError

    path = tmp_path / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)

    def no_model(prompt, system):
        raise RuntimeError("model gpt-6.1-sol is not available on this provider")

    _, answerer, judge = _clients()
    extractor = LLMClient(model="extractor-m", role="extractor", transport=no_model)
    with pytest.raises(ExtractionError, match="not available"):
        analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)


def test_a_failed_value_judge_leaves_atoms_out_of_the_categories_and_says_so(tmp_path):
    scripted = _scripted("informed")

    def judge_down(prompt, system):
        if "judge how it functions" in prompt:
            raise RuntimeError("throttled")
        return scripted(prompt, system)

    path = tmp_path / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    _, answerer, judge = _clients()
    extractor = LLMClient(model="extractor-m", role="extractor", transport=judge_down)
    artifact = analyze(body, config, extractor=extractor, answerer=answerer,
                       judge=judge).artifact
    assert sum(artifact["categories"].values()) == 0
    assert any(w.startswith(f"{len(_ATOMS)} of {len(_ATOMS)} atoms got no need/scaffolding")
               for w in artifact["warnings"])


def test_a_failed_conflict_check_reaches_the_artifact(tmp_path):
    scripted = _scripted("informed")

    def conflicts_down(prompt, system):
        if "Find pairs of statements" in prompt:
            raise RuntimeError("throttled")
        return scripted(prompt, system)

    artifact = _analyze_with(tmp_path, conflicts_down)
    n = len(_ATOMS)
    assert any(w.startswith(f"{n} of {n} atoms were not checked for conflicts")
               for w in artifact["warnings"])
    assert any("compared 0 of 1 atom pairs" in w for w in artifact["warnings"])
    assert artifact["conflict_coverage"] == {"pairs_compared": 0, "pairs_total": 1}
    withheld = evaluate_novelty(artifact)
    check = next(c for c in withheld.checks if c.name == "harmful_atoms")
    assert check.measured is False


def test_a_truncating_extractor_is_recovered_and_an_unreadable_span_is_a_warning(tmp_path):
    import re

    scripted = _scripted("informed")

    def ceiling(prompt, system):
        # Truncates any multi-span extraction reply, as a 4096-token default does on a
        # long batch, and never parses the background span at all.
        if "Extract the checkable knowledge" not in prompt:
            return scripted(prompt, system)
        ids = set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M))
        rows = json.dumps([a for a in _ATOMS if set(a["span_ids"]) & ids])
        if len(ids) > 1 or "research prototype" in prompt:
            rows = rows[: len(rows) // 2] or "["
        return rows, None

    path = tmp_path / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    _, answerer, judge = _clients()
    extractor = LLMClient(model="extractor-m", role="extractor", transport=ceiling)
    result = analyze(body, config, extractor=extractor, answerer=answerer, judge=judge)
    artifact = result.artifact
    assert artifact["atoms"]["n"] == len(_ATOMS)
    lost = [w for w in artifact["warnings"]
            if w.startswith("atom extraction could not read 1 of")]
    assert len(lost) == 1
    background = next(s for s in result.spans if "research prototype" in s.text)
    assert artifact["extraction"]["unread_span_ids"] == [background.id]
    assert artifact["extraction"]["retried_batches"] >= 1
    assert artifact["extraction"]["calls"] > artifact["extraction"]["batches"]
    # The unread span is reported, not counted as dead weight.
    no_atom = sum(s.tokens for s in result.spans if not s.atom_ids)
    assert (artifact["spans"]["n_unread"], artifact["spans"]["tokens_unread"]) == (
        1, background.tokens)
    assert artifact["spans"]["tokens_carrying_nothing"] == no_atom - background.tokens
    assert all(row["span_id"] != background.id for row in artifact["views"]["dead_weight"])
    assert artifact["attribution"]["spans_by_label"]["unread"] == 1
    for candidate in result.candidates:
        assert background.id in candidate.kept_span_ids
    save_artifact("novelty", artifact, tmp_path / "novelty.json")  # validates the new fields
    assert f"| unread spans | 1 ({background.tokens} tok) |" in render_novelty_markdown(artifact)


def test_no_atoms_is_not_reported_as_a_skipped_classification(tmp_path):
    transport = _scripted("informed")
    empty = LLMClient(model="extractor-m", role="extractor", transport=lambda p, s: (
        "[]" if "Extract the checkable knowledge" in p else transport(p, s)[0], None))
    path = tmp_path / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    _, answerer, judge = _clients()
    warnings = analyze(body, config, extractor=empty, answerer=answerer,
                       judge=judge).artifact["warnings"]
    assert not any("--no-classify" in w for w in warnings)


def test_skipping_classification_is_recorded_as_a_warning(tmp_path):
    result = _analyze(tmp_path, classify_content=False)
    assert any("categories" in w for w in result.artifact["warnings"])
    assert result.artifact["categories"] == {}
    assert "conflict_coverage" not in result.artifact


def test_span_citations_are_confirmed_by_default(tmp_path):
    result = _analyze(tmp_path)
    confirmation = result.artifact["attribution"]["carrier_confirmation"]
    assert confirmation["n_atoms"] == 2
    assert confirmation["n_pairs_checked"] >= 2
    assert confirmation["checked_with_heading_path"] is True


def test_skipping_carrier_confirmation_is_recorded_as_a_warning(tmp_path):
    result = _analyze(tmp_path, confirm_carriers=False)
    assert any("--no-confirm-carriers" in w for w in result.artifact["warnings"])
    assert "carrier_confirmation" not in result.artifact["attribution"]


def test_protected_spans_survive_every_offered_candidate(tmp_path):
    result = _analyze(tmp_path)
    offered = [c for c in result.artifact["shortening"] if c["safe"]]
    assert all(c["protection"] == 1.0 for c in offered)
    assert all(not c["dropped_protected_spans"] for c in offered)


def test_the_version_gate_is_coloured_amber_not_grey(tmp_path):
    result = _analyze(tmp_path)
    gate = next(a for a in result.attributions if "driver 1.0" in a.span.text)
    assert gate.colour in {"green", "amber", "red"}
    assert gate.span.protection


def test_the_cell_records_what_decided_its_numbers(tmp_path, monkeypatch):
    from dokimi.novelty import measure

    result = _analyze(tmp_path, seed=7, classify_content=False)
    run_config = result.artifact["run_config"]
    assert (run_config["seed"], run_config["stages"]["classify"]) == (7, False)
    validate_artifact("novelty", dict(result.artifact, schema_version="novelty.v1"))

    monkeypatch.setattr(measure, "_JUDGE_PROMPT", measure._JUDGE_PROMPT + " ")
    again = _analyze(tmp_path / "again", seed=7, classify_content=False).artifact["run_config"]
    assert again["prompts_sha256"] != run_config["prompts_sha256"]
    monkeypatch.setattr(measure, "_ANSWER_SYSTEM", measure._ANSWER_SYSTEM + " ")
    third = _analyze(tmp_path / "third", seed=7, classify_content=False).artifact["run_config"]
    assert third["prompts_sha256"] != again["prompts_sha256"]


def test_a_skill_s_own_evals_do_not_make_the_entry_point_run_warn(tmp_path):
    from dokimi.novelty.bundle import load_bundle

    root = tmp_path / "accel-tuning"
    (root / "evals").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "evals" / "cases.md").write_text("word " * 500, encoding="utf-8")

    bundle = load_bundle(f"skill:{root}")
    config = AnalyzeConfig(
        artifact_ref=f"skill:{root}", artifact_kind=bundle.kind, name=bundle.name,
        description=bundle.description, frontmatter=bundle.frontmatter, concurrency=1,
    )
    extractor, answerer, judge = _clients()
    artifact = analyze(bundle.text, config, extractor=extractor, answerer=answerer,
                       judge=judge, artifact_bundle=bundle).artifact

    # 90% of the words on disk are outside SKILL.md, but every one of them is the skill's
    # own answer key: --include-resources would read nothing more, so telling the user to
    # rerun with it is advice that cannot be followed.
    assert artifact["bundle"]["outside_share"] > 0.5
    assert artifact["bundle"]["measurable_outside_share"] == 0.0
    assert not any("--include-resources" in w for w in artifact["warnings"])
    rendered = render_novelty_markdown(artifact)
    assert "rerun with `--include-resources`" not in rendered
    assert "500" in rendered                      # the decision is still stated
    validate_artifact("novelty", dict(artifact, schema_version="novelty.v1"))


def test_a_tier_writes_its_forfeits_into_the_run_it_produced(tmp_path):
    from dokimi.novelty.tiers import FORFEITS, TIERS, resolve_settings

    resolved = resolve_settings("triage", {})
    result = _analyze(tmp_path, **resolved.config_fields())
    # The config the tier produced must be what the artifact reports back.
    assert result.artifact["run_config"]["max_atoms"] == 25
    assert result.artifact["run_config"]["stages"]["classify"] is False

    path = tmp_path / "again" / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1,
                           **resolved.config_fields())
    extractor, answerer, judge = _clients()
    artifact = analyze(body, config, extractor=extractor, answerer=answerer,
                       judge=judge).artifact
    assert artifact["run_config"]["tier"]["name"] == "triage"
    forfeits = [w for w in artifact["warnings"] if w.startswith("tier `triage` forfeits:")]
    # Two atoms under a cap of 25: the run is not sampled, so the sampled-rate forfeits
    # the tier states up front are dropped from what it records.
    assert artifact["atoms"]["n"] < 25
    assert len(forfeits) == len(TIERS["triage"]["forfeits"]) - len(FORFEITS["sampled"])
    assert sorted(artifact["run_config"]["tier"]["forfeits"]) == sorted(
        w.split("forfeits: ", 1)[1] for w in forfeits)
    assert not any("sampled rate" in w for w in forfeits)
    validate_artifact("novelty", dict(artifact, schema_version="novelty.v1"))


def test_skipping_the_faithfulness_check_is_recorded_as_a_warning(tmp_path):
    result = _analyze(tmp_path, verify_faithfulness=False)
    assert result.artifact["atoms"]["faithfulness"] == {}
    assert any("--no-verify" in w for w in result.artifact["warnings"])


def test_a_tier_overridden_by_flags_says_its_forfeits_may_not_be_this_run_s(tmp_path):
    from dokimi.novelty.tiers import resolve_settings

    resolved = resolve_settings("triage", {"max_atoms": 90})
    result = _analyze(tmp_path, **resolved.config_fields())
    assert result.artifact["run_config"]["max_atoms"] == 90
    path = tmp_path / "o" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1,
                           **resolved.config_fields())
    extractor, answerer, judge = _clients()
    artifact = analyze(body, config, extractor=extractor, answerer=answerer,
                       judge=judge).artifact
    assert any("overridden by flags" in w for w in artifact["warnings"])


def test_estimate_brackets_counted_calls(tmp_path):
    import pytest

    from dokimi.novelty.tiers import TIERS, estimate_calls

    for tier, spec in TIERS.items():
        path = tmp_path / tier / "accel-tuning" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(SKILL, encoding="utf-8")
        kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
        config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                               description=description, frontmatter=frontmatter,
                               concurrency=1, **spec["settings"])
        scripted, calls = _scripted("informed"), []

        def counting(prompt, system, scripted=scripted, calls=calls):
            calls.append(prompt)
            return scripted(prompt, system)

        extractor, answerer, judge = (LLMClient(model=m, role=r, transport=counting)
                                      for m, r in (("e", "extractor"), ("a", "answerer"),
                                                   ("j", "judge")))
        artifact = analyze(body, config, extractor=extractor, answerer=answerer,
                           judge=judge).artifact
        estimate = estimate_calls(artifact["atoms"]["n"],
                                  dict(spec["settings"], budgets=config.budgets),
                                  n_arms=len(config.arms))
        if not (estimate["total_calls"] <= len(calls)
                <= estimate["total_calls_if_every_pair_escalates"]):
            pytest.fail(f"{tier}: counted {len(calls)} outside {estimate}")


def _analyze_with(tmp_path, transport):
    path = tmp_path / "s" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL, encoding="utf-8")
    kind, name, description, frontmatter, body = load_artifact_text(f"skill:{path}")
    config = AnalyzeConfig(artifact_ref=f"skill:{path}", artifact_kind=kind, name=name,
                           description=description, frontmatter=frontmatter, concurrency=1)
    _, answerer, judge = _clients()
    extractor = LLMClient(model="extractor-m", role="extractor", transport=transport)
    return analyze(body, config, extractor=extractor, answerer=answerer,
                   judge=judge).artifact


def test_a_failed_adjudication_is_a_warning_and_a_count(tmp_path):
    scripted = _scripted("informed")

    def adjudicator_down(prompt, system):
        if "Can the STATEMENT be recovered" in prompt:
            raise RuntimeError("throttled")
        return scripted(prompt, system)

    artifact = _analyze_with(tmp_path, adjudicator_down)
    errors = artifact["retention"]["llm_errors"]
    assert errors >= 1
    assert any(w.startswith(f"{errors} of {artifact['retention']['llm_escalations']} "
                            "retention adjudications got no usable verdict")
               for w in artifact["warnings"])


def test_the_conflict_check_sends_the_capped_atom_set_in_one_call(tmp_path, monkeypatch):
    # The default batch of 40 under a cap of 120 compared about a third of the pairs.
    from dokimi.novelty import classify

    seen = {}
    real = classify.detect_conflicts

    def spy(atoms, **kwargs):
        seen["batch"] = kwargs.get("batch")
        return real(atoms, **kwargs)

    monkeypatch.setattr(classify, "detect_conflicts", spy)
    _analyze(tmp_path, max_atoms=120)
    assert seen["batch"] == 120


def test_a_negative_or_string_pair_count_fails_the_schema(tmp_path):
    artifact = _analyze(tmp_path).artifact
    for bad in ({"pairs_compared": -1, "pairs_total": 1},
                {"pairs_compared": "1", "pairs_total": 1},
                {"pairs_total": 1},
                {"pairs_compared": 1, "pairs_total": 1, "extra": 0}):
        broken = {**artifact, "conflict_coverage": bad, "schema_version": "novelty.v1"}
        with pytest.raises(ArtifactValidationError):
            validate_artifact("novelty", broken)

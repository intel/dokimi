# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A saved answer key against the bundle as it is now, and shortening a bundle's entry point."""

from __future__ import annotations

import pytest

from dokimi.novelty.bundle import load_bundle
from dokimi.novelty.spans import segment
from tests.test_novelty_bundle import _skill_with_evals

# ── Is a saved answer key still valid for the artifact as it is now? ────────

_KEY_SKILL = """\
---
name: accel-tuning
description: Configure the accelerator
---

# Accelerator tuning

Enable the accelerator with `--accel`.
"""


def _key_skill(tmp_path):
    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(_KEY_SKILL, encoding="utf-8")
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nNever exceed 64 threads.\n", encoding="utf-8")
    return root


def _recorded(root, **kwargs):
    return load_bundle(f"skill:{root}", include_resources=True, **kwargs).summary()


def _verdict(root, record):
    from dokimi.novelty.bundle import check_answer_key

    return check_answer_key(f"skill:{root}", record, source="a.json")


def test_the_file_set_comes_from_the_record_not_from_flags(tmp_path):
    root = _key_skill(tmp_path)
    # A record from before budgets, roles and digests were written.
    verdict = _verdict(root, {"include_resources": True, "n_files": 2,
                              "resource_words_measured": 6,
                              "files": [{"path": "SKILL.md", "role": "entry_point"},
                                        {"path": "references/tuning.md", "role": "reference"}]})
    assert verdict.ok
    # The reference is injected although no flag asked for it: the key was extracted from it.
    assert "Never exceed 64 threads" in verdict.text
    assert any("a.json: 2 files" in note for note in verdict.notes)


def test_a_key_without_provenance_is_probed_against_the_entry_point_and_says_so(tmp_path):
    root = _key_skill(tmp_path)
    verdict = _verdict(root, {})
    assert verdict.ok and verdict.bundle is None
    assert "Never exceed 64 threads" not in verdict.text
    assert any("predates bundle provenance" in note for note in verdict.notes)


def test_a_key_for_an_artifact_that_cannot_be_loaded_is_refused(tmp_path):
    from dokimi.novelty.bundle import check_answer_key

    for record in ({}, {"files": [{"path": "SKILL.md", "role": "entry_point"}]}):
        verdict = check_answer_key(f"skill:{tmp_path / 'missing'}", record)
        assert (verdict.ok, verdict.problem) == (False, "unloadable")



def test_a_key_with_a_recorded_bundle_whose_skill_is_nested_too_deeply_is_refused(tmp_path):
    from dokimi.novelty.bundle import check_answer_key

    root = tmp_path / "deep"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: deep\ndescription: " + "[" * 50000 + "]" * 50000 + "\n---\n\nBody.\n",
        encoding="utf-8")
    record = {"files": [{"path": "SKILL.md", "role": "entry_point", "words": 3, "end": 5}],
              "resource_roles": []}
    verdict = check_answer_key(f"skill:{root}", record, source="a.json")
    assert (verdict.ok, verdict.problem) == (False, "unloadable")
    assert "maximum recursion" in verdict.message


def test_a_key_for_an_entry_point_that_cannot_be_read_is_refused(tmp_path):
    from dokimi.novelty.bundle import check_answer_key

    # An entry point that exists but is not readable (here: a directory) is an OSError
    # other than FileNotFoundError; it must come back as a verdict, not a traceback.
    root = tmp_path / "unreadable"
    (root / "SKILL.md").mkdir(parents=True)
    for record in ({}, {"files": [{"path": "SKILL.md", "role": "entry_point"}]}):
        verdict = check_answer_key(f"skill:{root}", record)
        assert (verdict.ok, verdict.problem) == (False, "unloadable")

def test_a_record_without_usable_file_rows_is_refused(tmp_path):
    root = _key_skill(tmp_path)
    for broken in ({"include_resources": True, "files": []},
                   {"include_resources": True, "files": [{"path": "SKILL.md"}]}):
        verdict = _verdict(root, broken)
        assert verdict.problem == "no_file_rows" and "provenance" in verdict.message


def test_a_removed_file_is_a_changed_file_set(tmp_path):
    root = _key_skill(tmp_path)
    verdict = _verdict(root, {"include_resources": True, "n_files": 3, "files": [
        {"path": "SKILL.md", "role": "entry_point"},
        {"path": "references/tuning.md", "role": "reference"},
        {"path": "references/gone.md", "role": "reference"}]})
    assert verdict.problem == "files_changed"
    assert "references/gone.md" in verdict.recorded
    assert "references/gone.md" not in verdict.rebuilt


def test_the_selection_is_replayed_with_the_recorded_budgets(tmp_path):
    root = _key_skill(tmp_path)
    (root / "references" / "big.md").write_text("word " * 5000, encoding="utf-8")
    bundle = load_bundle(f"skill:{root}", include_resources=True, max_file_words=6000)
    assert _verdict(root, bundle.summary()).text == bundle.text


@pytest.mark.parametrize("edit", [
    "# Tuning\n\nNever exceed 128 threads on driver 1.0.\n",
    # Same path, same word count, different fact: only the digest sees this one.
    "# Tuning\n\nNever exceed 128 threads.\n",
])
def test_an_edited_bundled_file_is_a_stale_key(tmp_path, edit):
    root = _key_skill(tmp_path)
    record = _recorded(root)
    (root / "references" / "tuning.md").write_text(edit, encoding="utf-8")
    verdict = _verdict(root, record)
    assert (verdict.problem, verdict.edited) == ("files_changed", ("references/tuning.md",))


def test_an_edited_entry_point_is_the_re_measure_step_and_says_so(tmp_path):
    root = _key_skill(tmp_path)
    record = _recorded(root)
    (root / "SKILL.md").write_text(
        "---\nname: accel-tuning\ndescription: Configure the accelerator\n---\n\n"
        "# Accelerator tuning\n\nShortened: the flag is `--accel`.\n", encoding="utf-8")
    verdict = _verdict(root, record)
    assert verdict.ok and verdict.entry_point_changed
    assert "Shortened: the flag is" in verdict.text and "Never exceed 64" in verdict.text
    assert any("has changed since these atoms were extracted" in n for n in verdict.notes)


@pytest.mark.parametrize("added", ["notes.md", "scripts/run.sh"])
def test_a_new_file_in_a_role_not_seen_at_extraction_is_a_stale_key(tmp_path, added):
    root = _key_skill(tmp_path)
    record = _recorded(root)
    # The extraction had only a reference, so the rebuild is filtered to that role and a
    # new file in any other role never reached the file-set comparison.
    (root / added).parent.mkdir(parents=True, exist_ok=True)
    (root / added).write_text("Always pass --force.\n", encoding="utf-8")
    verdict = _verdict(root, record)
    assert (verdict.problem, verdict.added) == ("files_changed", (added,))


def test_a_new_file_that_is_not_content_leaves_the_key_valid(tmp_path):
    root = _key_skill(tmp_path)
    record = _recorded(root)
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    # A symlink is recorded, never followed, in every mode, so re-extracting would not
    # measure it either. (It used to count as a change; an image never did.)
    (root / "references" / "shared.md").symlink_to(root / "references" / "tuning.md")
    assert _verdict(root, record).ok


def test_an_answer_key_from_another_skill_is_refused(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\nname: other-skill\ndescription: Tune a database\n---\n\n# Other\n",
        encoding="utf-8")
    root = _key_skill(tmp_path)
    # Both sides are just ["SKILL.md"], so the file-set check could never catch this.
    verdict = _verdict(root, load_bundle(f"skill:{other}").summary())
    assert verdict.problem == "wrong_artifact" and "belongs to one artifact" in verdict.message


def test_a_budget_gap_stays_a_gap_when_replayed(tmp_path):
    """Rebuilding with the selected files' roles relabelled an over-budget script `policy`."""
    root = _key_skill(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "big.py").write_text("x = 1  # w\n" * 6000, encoding="utf-8")
    recorded = load_bundle(f"skill:{root}", include_resources=True)
    assert recorded.coverage().unmeasured_words > 0
    probed = _verdict(root, recorded.summary()).bundle
    assert probed.coverage() == recorded.coverage()
    assert probed.coverage().policy_excluded_words == 0


def test_requested_roles_are_replayed_when_none_was_selected(tmp_path):
    root = _key_skill(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "big.py").write_text("x = 1  # w\n" * 6000, encoding="utf-8")
    recorded = load_bundle(f"skill:{root}", include_resources=True, roles=("script",))
    assert [f.path for f in recorded.files] == ["SKILL.md"]
    assert _verdict(root, recorded.summary()).ok     # the unchanged tree is not "changed"


def test_a_new_eval_case_changes_the_key_only_when_evals_were_requested(tmp_path):
    root = _key_skill(tmp_path)
    by_default = _recorded(root)
    (root / "evals").mkdir()
    (root / "evals" / "a.md").write_text("Q: one?\n", encoding="utf-8")
    # Evals are not measured by default; the text is unchanged.
    assert _verdict(root, by_default).ok
    with_evals = _recorded(root, roles=("reference", "eval"))
    (root / "evals" / "q.md").write_text("Q: two?\n", encoding="utf-8")
    verdict = _verdict(root, with_evals)
    assert verdict.problem == "files_changed" and verdict.added == ("evals/q.md",)


# ── Shortening rewrites one file ─────────────────────────────────────────────

def test_shortening_a_bundle_key_keeps_only_the_entry_point(tmp_path):
    from dokimi.novelty.atoms import Atom
    from dokimi.novelty.bundle import entry_point_only

    root = _key_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    spans = segment(bundle.text)
    entry_end = bundle.files[0].end
    inside = next(s for s in spans if s.start < entry_end and "`--accel`" in s.text)
    outside = next(s for s in spans if s.start >= entry_end and "64" in s.text)
    atoms = [
        Atom(statement="entry", question="q", answer="a", span_ids=(inside.id,)),
        Atom(statement="reference", question="q", answer="a", span_ids=(outside.id,)),
        # Half the evidence cannot be scored against a candidate that holds one file.
        Atom(statement="both", question="q", answer="a", span_ids=(inside.id, outside.id)),
    ]
    kept_spans, kept_atoms, note = entry_point_only(bundle.summary(), spans, atoms)
    assert all(span.start < entry_end for span in kept_spans)
    assert [atom.statement for atom in kept_atoms] == ["entry"]
    assert "bundle spans" in note and "SKILL.md" in note
    # An entry-point key passes through untouched.
    assert entry_point_only(load_bundle(f"skill:{root}").summary(), spans, atoms) == (
        spans, atoms, None)


def test_shortening_refuses_a_bundle_record_it_cannot_slice():
    from dokimi.novelty.bundle import entry_point_only

    with pytest.raises(ValueError, match="file rows"):
        entry_point_only({"include_resources": True, "files": []}, [], [])
    with pytest.raises(ValueError, match="span range"):
        entry_point_only({"include_resources": True, "files": [{"path": "SKILL.md"}]}, [], [])


def test_the_report_names_excluded_files_and_counts_non_text_ones(tmp_path):
    from dokimi.novelty.report import render_novelty_markdown

    from tests.test_novelty_cli import _minimal_artifact
    from tests.test_novelty_gate import _artifact as _gate_artifact

    def _artifact(bundle):
        # The gate's fixture, with the blocks the renderer reads.
        minimal = _minimal_artifact()
        return dict(_gate_artifact(), spans=minimal["spans"], artifact=minimal["artifact"],
                    shortening=[],
                    bundle=bundle)

    root = _skill_with_evals(tmp_path, refs=50)
    (root / "references" / "big.md").write_text("w " * 30, encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "references" / "linked.md").symlink_to(root / "references" / "r.md")
    bundle = load_bundle(f"skill:{root}", include_resources=True, max_file_words=40)
    rendered = render_novelty_markdown(_artifact(bundle=bundle.summary()))
    assert "3 files excluded: `evals/cases.md`" in rendered
    assert "`references/linked.md` (symlink), `references/r.md` (over max_file_words=40)." in rendered
    assert "1 non-text files not read." in rendered
    partial = render_novelty_markdown(_artifact(bundle={"outside_share": 0.99}))
    assert "lacks its word counts" in partial


def test_measured_digest_format_is_pinned(tmp_path):
    """Measured_digest format is stable."""
    from dokimi.novelty.bundle import load_bundle

    # Create a test skill with two files
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: test\ndescription: d\n---\n\nEntry point text.\n",
        encoding="utf-8",
    )
    ref = skill.parent / "references" / "ref.md"
    ref.parent.mkdir()
    ref.write_text("Reference text.", encoding="utf-8")

    bundle = load_bundle(f"skill:{skill.parent}", include_resources=True)
    digest = bundle.measured_digest()

    # Pin the exact literal so edits to measured_digest() cannot silently invalidate stored ref_digest values
    assert digest == "sha256:f0eb5f66f8b343a03bc6f5fbcd233e3bbf591db23ef075502db96bb35ddd5bf5"

    # The digest should be deterministic - same files, same digest
    bundle2 = load_bundle(f"skill:{skill.parent}", include_resources=True)
    assert bundle2.measured_digest() == digest

    # Modifying a file should change the digest
    ref.write_text("Modified reference text.", encoding="utf-8")
    bundle3 = load_bundle(f"skill:{skill.parent}", include_resources=True)
    assert bundle3.measured_digest() != digest


def test_the_record_states_the_share_of_readable_words_this_run_read(tmp_path):
    """100 entry words and 300 measurable reference words: an entry-point run read a quarter."""
    bundle = load_bundle(f"skill:{_skill_with_evals(tmp_path, entry=100, refs=300)}")
    assert bundle.coverage().measured_share == 0.25
    assert bundle.summary()["measured_share"] == 0.25
    assert any(w.startswith("75% ") for w in bundle.coverage_warnings())


def test_exactly_half_the_readable_words_read_is_warned_as_the_analyzer_always_has_been(tmp_path):
    """The warning line is unchanged: a run that read half the readable words still warns."""
    bundle = load_bundle(f"skill:{_skill_with_evals(tmp_path, entry=300, refs=300)}")
    assert bundle.coverage().measured_share == 0.5
    assert any(w.startswith("50% ") for w in bundle.coverage_warnings())


def test_a_bundle_run_that_read_everything_it_could_measures_all_of_it(tmp_path):
    bundle = load_bundle(f"skill:{_skill_with_evals(tmp_path, entry=100, refs=300)}",
                         include_resources=True)
    assert bundle.summary()["measured_share"] == 1.0


def test_the_stored_measured_share_is_rounded_to_four_places(tmp_path):
    # 2 entry words and 7 reference words: 2/9 is 0.2222..., stored as 0.2222, not 0.2222222.
    bundle = load_bundle(f"skill:{_skill_with_evals(tmp_path, entry=2, refs=7)}")
    assert bundle.summary()["measured_share"] == 0.2222


@pytest.mark.parametrize("include_resources", [False, True])
def test_an_artifact_with_no_readable_words_stores_no_share_and_prints_none(
        tmp_path, capsys, include_resources):
    # Only eval words, which are policy-excluded: the schema promises null, not "100% read",
    # and the headline has no share to state, not "a bundled file did not decode".
    from dokimi.commands.novelty import _print_coverage

    root = _skill_with_evals(tmp_path, entry=0, refs=0, evals=4)
    record = load_bundle(f"skill:{root}", include_resources=include_resources).summary()
    assert record["entry_point_words"] == 0 and record["measured_share"] is None
    _print_coverage(record)
    assert "coverage:" not in capsys.readouterr().out

"""Guards of the bundle record that no other test pins: partial counts, empty, ceiling, spans."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from dokimi.gate.novelty_gate import evaluate_novelty
from dokimi.novelty import report
from dokimi.novelty.bundle import Coverage, check_answer_key, entry_point_only, load_bundle

WORDS = " ".join(f"w{i}" for i in range(40))
COUNTS = {"entry_point_words": 100, "resource_words_on_disk": 100, "resource_words_measured": 0}
HEAD = "---\nname: s\ndescription: use when testing\n---\n"


def _skill(tmp_path, files):
    root = tmp_path / "s"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return root


def _coverage_check(record):
    verdict = evaluate_novelty({"bundle": record})
    return next(c for c in verdict.checks if c.name == "artifact_coverage")


@pytest.mark.parametrize("missing", sorted(COUNTS))
def test_a_partial_count_block_has_no_coverage_figure(missing):
    record = {k: v for k, v in COUNTS.items() if k != missing}
    assert Coverage.from_record(record) is None
    check = _coverage_check(record)
    assert check.measured is False and check.value is None
    assert "unknown" in check.message
    assert "lacks its word counts" in " ".join(report._render_bundle({"bundle": record}))


def test_an_artifact_with_no_words_is_not_unmeasured():
    record = {"entry_point_words": 0, "resource_words_on_disk": 0, "resource_words_measured": 0}
    assert Coverage.from_record(record).unmeasured_share == 0.0
    check = _coverage_check(record)
    assert (check.passed, check.value) == (True, 0.0)


def test_the_ceiling_itself_passes():
    check = _coverage_check(dict(COUNTS))      # exactly half of the readable words unread
    assert (check.passed, check.value) == (True, 0.5)
    assert _coverage_check(dict(COUNTS, resource_words_on_disk=102)).passed is False


def test_an_entry_point_run_is_not_warned_about_files_excluded_from_the_measured_text(tmp_path):
    root = _skill(tmp_path, {"SKILL.md": HEAD + WORDS, "references/a.md": WORDS})
    entry = load_bundle(f"skill:{root}")
    assert entry.excluded and not any("excluded from the measured" in w
                                      for w in entry.coverage_warnings())
    over = load_bundle(f"skill:{root}", include_resources=True, max_file_words=10)
    assert any("excluded from the measured" in w for w in over.coverage_warnings())


@pytest.mark.parametrize("newfile, content", [
    ("references/huge.md", " ".join(["w"] * 200)),                   # a gap: over the file budget
    ("references/latin.md", ("caf\xe9 " * 60).encode("latin-1")),    # unknown: does not decode
    ("references/ok.md", WORDS),                                     # selected
])
def test_new_text_since_extraction_invalidates_the_answer_key(tmp_path, newfile, content):
    root = _skill(tmp_path, {"SKILL.md": HEAD + WORDS, "references/a.md": WORDS})
    record = load_bundle(f"skill:{root}", include_resources=True, max_file_words=100).summary()
    _skill(tmp_path, {newfile: content})
    verdict = check_answer_key(f"skill:{root}", record)
    assert verdict.problem == "files_changed" and newfile in verdict.added


def test_entry_point_only_scores_nothing_it_cannot_place():
    spans = [NS(id="a", start=0), NS(id="b", start=10), NS(id="c", start=20)]
    atoms = [NS(span_ids=["a"]), NS(span_ids=[]), NS(span_ids=["a", "c"]), NS(span_ids=["b"])]
    record = {"include_resources": True, "n_files": 2,
              "files": [{"path": "SKILL.md", "start": 0, "end": 20}]}
    kept, kept_atoms, note = entry_point_only(record, spans, atoms)
    assert [s.id for s in kept] == ["a", "b"]           # a span starting AT the end is the next file's
    assert kept_atoms == [atoms[0], atoms[3]]           # no empty evidence, no half-bundle evidence
    assert note and "1 bundle spans" in note


def test_an_unreadable_row_is_listed_by_the_report():
    record = dict(COUNTS, include_resources=True, n_files=1, unreadable_files=1,
                  excluded=[{"path": "references/x.md", "role": "reference",
                             "reason": "unreadable", "kind": "unreadable"}])
    assert "`references/x.md` (unreadable)" in "\n".join(report._render_bundle({"bundle": record}))

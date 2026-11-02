# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""questions.v2 is written, questions.v1 is still read.

The writers are run for real (LLM calls mocked) and their files held to the v2 schema plus the
closed-world key check of ``artifact_fixture``, so the schema documents every key they write.
The golden excerpts under ``tests/fixtures/questions/`` are two verbatim questions from each
curated set of a content checkout (unstamped and stamped v1, ``text`` records) and lock that
real curated sets keep loading and exporting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from dokimi.artifacts import ArtifactValidationError, SCHEMA_VERSIONS, validate_artifact
from dokimi.cli import build_parser
from dokimi.datasets_export import export_artifact
from dokimi.questions.artifact import load_questions, save_questions_artifact, upgrade_questions
from dokimi.questions.normalizer import canonical_question, normalize_question
from tests.artifact_fixtures import artifact_fixture

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "questions"
GOLDEN_EXCERPTS = sorted(GOLDEN.glob("*_golden.excerpt.json"))


def test_questions_are_written_as_v2():
    assert SCHEMA_VERSIONS["questions"] == "questions.v2"


# ── record rename ─────────────────────────────────────────────────────────────

def test_canonical_question_renames_text_in_place_and_keeps_everything_else():
    q = {"id": "q1", "text": "How?", "difficulty": "easy", "metadata": {"k": 1}}
    out = canonical_question(q)
    assert out == {"id": "q1", "question": "How?", "difficulty": "easy", "metadata": {"k": 1}}
    assert list(out) == ["id", "question", "difficulty", "metadata"]
    assert q["text"] == "How?" and "question" not in q  # not mutated


def test_canonical_question_prefers_question_like_the_normalizer():
    q = {"id": "q1", "question": "New?", "text": "Old?"}
    assert canonical_question(q) == {"id": "q1", "question": "New?"}
    assert normalize_question(q)["question"] == "New?"
    assert canonical_question({"id": "q1", "question": "", "text": "Old?"})["question"] == "Old?"


def test_a_v2_record_may_not_carry_text():
    with pytest.raises(ArtifactValidationError, match=r"questions/0/text: 'Q\?' should not be valid"):
        validate_artifact("questions", {"schema_version": "questions.v2",
                                        "questions": [{"id": "q1", "question": "Q?", "text": "Q?"}]})


# ── write path ────────────────────────────────────────────────────────────────

def test_save_questions_artifact_renames_text_and_stamps_v2(tmp_path):
    out = tmp_path / "q.json"
    payload = {"questions": [{"id": "q1", "text": "How?"}, {"id": "q2", "question": "Why?"}]}
    written = save_questions_artifact(payload, out)
    assert json.loads(out.read_text(encoding="utf-8")) == written
    assert written["schema_version"] == "questions.v2"
    assert [q["question"] for q in written["questions"]] == ["How?", "Why?"]
    assert "text" in payload["questions"][0]  # caller's records untouched


def test_save_questions_artifact_refuses_an_invalid_set_and_keeps_it(tmp_path):
    out = tmp_path / "q.json"
    with pytest.raises(ArtifactValidationError, match="'id' is a required property"):
        save_questions_artifact({"questions": [{"text": "no id"}]}, out)
    assert not out.exists()
    assert (tmp_path / "q.json.rejected").exists()


def _generator(content='["How do I use parallel_for with a custom range?", "Why does task_arena deadlock?"]'):
    from dokimi.questions.llm_gen import QuestionGenerator

    llm = Mock()
    llm.invoke.return_value = Mock(content=content)
    with patch("dokimi.questions.llm_gen.ChatOpenAI", Mock(return_value=llm), create=True), \
         patch("dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE", True):
        return QuestionGenerator()


PERSONAS = [{"id": "hpc", "name": "HPC Dev", "skill_level": "advanced", "description": "d",
             "concerns": ["perf"]}]


def _no_embeddings():
    return (patch("dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE", False),
            patch("dokimi.questions.validator.OPENAI_AVAILABLE", False),
            patch("dokimi.questions.validator.LANGCHAIN_AVAILABLE", False),
            patch.dict("os.environ", {}, clear=True))


def test_questions_generate_with_validation_writes_only_declared_keys(tmp_path):
    from dokimi.questions.validator import QuestionValidator

    gen = _generator()
    questions = gen.generate_questions("oneTBB", PERSONAS, ["parallel_for", "task_arena"], 2)
    assert all("text" in q for q in questions)  # the generator's in-memory records are v1-shaped
    a, b, c, d = _no_embeddings()
    with a, b, c, d:
        validator = QuestionValidator(threshold=0)
    with patch.object(validator, "_validate_question", return_value={"aggregate": 80, "relevance": 80}):
        questions, _ = validator.validate_and_dedupe("oneTBB", questions)
    out = tmp_path / "questions" / "oneTBB.json"
    gen.save_questions(questions, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["questions"] and "validation_score" in data["questions"][0]
    artifact_fixture("questions", data)


def test_hybrid_generation_writes_only_declared_keys(tmp_path):
    from dokimi.questions import chunk_gen

    result = chunk_gen.ChunkQuestionResult(questions=[chunk_gen.ChunkQuestion(
        question="What does the grainsize control?", chunk_text="grainsize sets ...",
        chunk_index=3, source_url="https://example.com/ranges")])
    chunk_module = MagicMock(to_question_dicts=chunk_gen.to_question_dicts)
    chunk_module.ChunkBasedQuestionGenerator.return_value.generate.return_value = result
    gen = _generator()
    with patch.dict(sys.modules, {"dokimi.questions.chunk_gen": chunk_module}):
        questions = gen.generate_hybrid("oneTBB", PERSONAS, ["parallel_for", "task_arena"],
                                        "https://example.com", total_questions=4, chunk_ratio=0.5)
    out = tmp_path / "hybrid.json"
    gen.save_questions(questions, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {q.get("question_source") for q in data["questions"]} == {"persona", "chunk"}
    artifact_fixture("questions", data)


def test_the_pipeline_merge_with_a_golden_set_writes_only_declared_keys(tmp_path):
    from dokimi.orchestrator.pipeline import EvaluationPipeline, compute_question_set_hash

    pipe = EvaluationPipeline.__new__(EvaluationPipeline)
    pipe.product = "oneTBB"
    pipe.questions_path = tmp_path / "questions" / "oneTBB.json"
    pipe.custom_questions_path = GOLDEN / "onetbb_golden.excerpt.json"
    gen = _generator()
    generated = [dict(q, source_type="generated")
                 for q in gen.generate_questions("oneTBB", PERSONAS, ["parallel_for"], 2)]
    a, b, c, d = _no_embeddings()
    with a, b, c, d:
        merged = pipe._merge_questions(generated)
    pipe._save_questions_artifact(merged, compute_question_set_hash(merged))
    data = json.loads(pipe.questions_path.read_text(encoding="utf-8"))
    assert data["sources"] == {"generated": 2, "manual": 2}
    assert all("text" not in q for q in data["questions"])
    # The hash is over the text, whichever key carried it, so it survives the rename.
    assert data["question_set_hash"] == compute_question_set_hash(data["questions"])
    artifact_fixture("questions", data)


def test_the_pipeline_merge_gives_an_id_less_custom_question_its_positional_id(tmp_path):
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps([{"text": "How do I use parallel_for?"}]), encoding="utf-8")
    pipe = EvaluationPipeline.__new__(EvaluationPipeline)
    pipe.product = "oneTBB"
    pipe.questions_path = tmp_path / "questions" / "oneTBB.json"
    pipe.custom_questions_path = custom
    a, b, c, d = _no_embeddings()
    with a, b, c, d:
        merged = pipe._merge_questions([])
    assert [(q["id"], q["question"]) for q in merged] == [("q0000", "How do I use parallel_for?")]
    # _merge_questions no longer saves; run() does. Save explicitly for this direct test.
    save_questions_artifact({"questions": merged}, pipe.questions_path)
    assert load_questions(pipe.questions_path).source_version == "questions.v2"


def test_the_pipeline_merge_refuses_a_custom_question_without_text(tmp_path):
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps([{"id": "g1", "text": "Why?"}, {"id": "g2", "text": ""}]),
                      encoding="utf-8")
    pipe = EvaluationPipeline.__new__(EvaluationPipeline)
    pipe.product = "oneTBB"
    pipe.questions_path = tmp_path / "questions" / "oneTBB.json"
    pipe.custom_questions_path = custom
    with pytest.raises(ArtifactValidationError, match=r"record 1 \(id 'g2'\), `question`: '' should be non-empty"):
        pipe._merge_questions([])
    assert not pipe.questions_path.exists()


def test_questions_refine_writes_only_declared_keys(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"questions": [
        {"id": "q1", "text": "How do I tune the grainsize of a blocked_range?", "difficulty": 2},
        {"id": "q2", "text": "Why does my task_arena deadlock under nested parallelism?",
         "difficulty": "hard", "persona": "dev"},
    ]}), encoding="utf-8")
    out = tmp_path / "refined.json"
    args = build_parser().parse_args(["eval", "questions", "refine", "--questions", str(src),
                                      "--product", "oneTBB", "--output", str(out)])
    args.func(args)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [q["question"] for q in data["questions"]][0].startswith("How do I tune")
    assert data["total"] == 2 and data["library"] == "oneTBB"
    artifact_fixture("questions", data)


def test_catalog_trigger_set_questions_writes_only_declared_keys(tmp_path):
    skill = tmp_path / "skills" / "onetbb-quickstart"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: onetbb-quickstart\ndescription: "Parallelize C++ loops with oneTBB."\n---\nbody\n',
        encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text(json.dumps([{"id": "n1", "text": "my cat is sad today"}]), encoding="utf-8")
    out = tmp_path / "trigger_questions.json"
    args = build_parser().parse_args([
        "eval", "triggers", "generate", "--catalog", str(tmp_path / "skills"), "--format", "questions",
        "--questions", f"{GOLDEN / 'onetbb_golden.excerpt.json'}=onetbb-quickstart",
        "--questions", f"{other}=none", "--rewrite", "--model", "m", "--out", str(out)])
    with patch("dokimi.llm.llm_call", return_value="how do I speed up my loop"):
        args.func(args)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "questions.v2"
    assert [q["expected_skill"] for q in data["questions"]] == ["onetbb-quickstart"] * 2 + [None]
    assert all("trigger_lint" in q and "source_text" in q for q in data["questions"])
    assert load_questions(out).source_version == "questions.v2"
    artifact_fixture("questions", data)


# ── read path ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", GOLDEN_EXCERPTS, ids=lambda p: p.name)
def test_a_curated_v1_set_loads_and_upgrades_to_a_valid_v2_set(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw["questions"]) == 2 and all("text" in q for q in raw["questions"])
    loaded = load_questions(path)
    assert loaded.source_version == "questions.v1"
    assert [q["question"] for q in loaded.questions] == [q["text"] for q in raw["questions"]]
    assert [q["id"] for q in loaded.questions] == [q["id"] for q in raw["questions"]]
    # The upgrade is the rename only: what the author wrote (difficulty, metadata, ...) is kept.
    assert [{k: v for k, v in q.items() if k != "question"} for q in loaded.questions] == \
           [{k: v for k, v in q.items() if k != "text"} for q in raw["questions"]]
    artifact_fixture("questions", loaded.artifact)


def test_the_golden_excerpts_cover_both_stamped_and_unstamped_sets():
    stamps = {json.loads(p.read_text(encoding="utf-8")).get("schema_version") for p in GOLDEN_EXCERPTS}
    assert stamps == {None, "questions.v1"}
    skills = {type(q.get("expected_skill", "absent")).__name__
              for p in GOLDEN_EXCERPTS for q in json.loads(p.read_text(encoding="utf-8"))["questions"]}
    assert skills == {"str", "list", "NoneType"}  # every shape `expected_skill` takes in the wild


@pytest.mark.parametrize("path", GOLDEN_EXCERPTS, ids=lambda p: p.name)
def test_an_upgraded_curated_set_round_trips_as_v2(path, tmp_path):
    out = tmp_path / "v2.json"
    save_questions_artifact(load_questions(path).artifact, out)
    again = load_questions(out)
    assert again.source_version == "questions.v2"
    assert again.artifact == load_questions(path).artifact


def test_load_questions_reads_a_v2_file_unchanged(tmp_path):
    out = tmp_path / "q.json"
    written = save_questions_artifact({"questions": [{"id": "q1", "question": "Q?"}]}, out)
    loaded = load_questions(out)
    assert (loaded.source_version, loaded.artifact) == ("questions.v2", written)


@pytest.mark.parametrize("version, text_key", [("questions.v1", "text"), ("questions.v2", "question")])
def test_question_id_0_round_trips_as_id_0(version, text_key, tmp_path):
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"schema_version": version,
                               "questions": [{"id": 0, text_key: "Q0?"}, {"id": 1, text_key: "Q1?"}]}),
                   encoding="utf-8")
    loaded = load_questions(src)
    assert loaded.source_version == version
    assert [q["id"] for q in loaded.questions] == [0, 1]
    out = tmp_path / "v2.json"
    save_questions_artifact(loaded.artifact, out)
    again = load_questions(out)
    assert again.source_version == "questions.v2"
    assert [q["id"] for q in again.questions] == [0, 1]
    assert json.loads(out.read_text(encoding="utf-8"))["questions"][0] == {"id": 0, "question": "Q0?"}
    # The normalizer keeps 0 as the id instead of renaming it to its positional q0000.
    assert [normalize_question(q, i + 5)["id"] for i, q in enumerate(again.questions)] == [0, 1]


def test_load_questions_without_validation_reports_only_the_declared_version(tmp_path):
    out = tmp_path / "q.json"
    out.write_text(json.dumps({"questions": [{"id": "q1", "text": ""}]}), encoding="utf-8")
    loaded = load_questions(out, validate=False)
    assert loaded.source_version is None
    assert loaded.questions == [{"id": "q1", "question": ""}]
    with pytest.raises(ArtifactValidationError, match=r"record 0 \(id 'q1'\), `question`"):
        load_questions(out)


def test_load_questions_reads_a_bare_list_as_a_legacy_set(tmp_path, caplog):
    out = tmp_path / "q.json"
    out.write_text(json.dumps([{"id": "q1", "text": "Q?"}]), encoding="utf-8")
    with caplog.at_level("WARNING", logger="dokimi.questions.artifact"):
        loaded = load_questions(out)
    assert (loaded.source_version, loaded.legacy) == (None, ("a bare list, not an object",))
    assert loaded.artifact == {"schema_version": "questions.v2",
                               "questions": [{"id": "q1", "question": "Q?"}]}
    assert caplog.messages == [f"read {out} in legacy questions format (a bare list, not an "
                               f"object); using it as questions.v2"]


def test_load_questions_refuses_what_is_not_a_list_of_questions(tmp_path):
    out = tmp_path / "q.json"
    for payload, match in [({"questions": {"q1": "Q?"}}, "`questions` is dict"),
                           ({"items": []}, r"no `questions` key \(keys: items\)"),
                           ("Q?", "got str")]:
        out.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ArtifactValidationError, match=f"Expected a list of questions in .*{match}"):
            load_questions(out, validate=False)


def test_load_questions_refuses_an_unknown_version(tmp_path):
    out = tmp_path / "q.json"
    out.write_text(json.dumps({"schema_version": "questions.v9", "questions": []}), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="'questions.v9' is not a questions version"):
        load_questions(out)


def test_upgrade_questions_does_not_mutate_and_keeps_top_level_keys():
    data = {"schema_version": "questions.v1", "library": "demo",
            "questions": [{"id": "q1", "text": "Q?"}]}
    up = upgrade_questions(data)
    assert up == {"schema_version": "questions.v2", "library": "demo",
                  "questions": [{"id": "q1", "question": "Q?"}]}
    assert data["schema_version"] == "questions.v1" and "text" in data["questions"][0]


# ── dataset export ────────────────────────────────────────────────────────────

def _export(src: Path, out: Path, *extra: str) -> list[dict]:
    args = build_parser().parse_args(["dataset", "export", "--kind", "questions",
                                      "--input", str(src), "--out-dir", str(out), *extra])
    args.func(args)
    return [json.loads(line) for line in (out / "questions.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("path", GOLDEN_EXCERPTS, ids=lambda p: p.name)
def test_dataset_export_of_a_curated_v1_set(path, tmp_path):
    rows = _export(path, tmp_path / "out")
    assert len(rows) == 2
    assert all("question" in r and "text" not in r for r in rows)
    assert {r["schema_version"] for r in rows} == {"questions.v2"}
    card = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert "Schema version:** questions.v2" in card
    assert "Source schema version:** questions.v1 (records upgraded to questions.v2" in card


def test_dataset_export_of_a_generated_v2_set(tmp_path):
    src = tmp_path / "q.json"
    gen = _generator()
    gen.save_questions(gen.generate_questions("oneTBB", PERSONAS, ["parallel_for"], 2), src)
    rows = _export(src, tmp_path / "out")
    assert len(rows) == 2 and all(r["question"] and r["model"] == gen.model for r in rows)
    card = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert "(questions.v2)" in card and "Source schema version" not in card


def test_v1_and_v2_exports_of_one_set_have_the_same_columns(tmp_path):
    path = GOLDEN / "onetbb_golden.excerpt.json"
    v2 = tmp_path / "v2.json"
    save_questions_artifact(json.loads(path.read_text(encoding="utf-8")), v2)
    assert _export(path, tmp_path / "a") == _export(v2, tmp_path / "b")


def test_dataset_export_without_validation_of_an_unstamped_invalid_set(tmp_path):
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"questions": [{"id": "q1", "text": ""}]}), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        export_artifact("questions", src, tmp_path / "strict")
    rows = _export(src, tmp_path / "out", "--no-validate")
    assert rows == [{"schema_version": "questions.v2", "id": "q1", "question": ""}]
    card = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert "Source schema version:** unknown" in card


# ── tolerant reader, strict writer, inputs untouched ──────────────────────────
#
# A reused set (pipeline cache, --questions-from, --custom-questions) is read by load_questions
# in whatever shape main wrote or accepted, and the run writes its own v2 copy.

V2_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "artifacts" / "questions.v2.json"
VALID_SETS = [*GOLDEN_EXCERPTS, V2_FIXTURE]


@pytest.mark.parametrize("path", VALID_SETS, ids=lambda p: p.name)
def test_a_valid_set_keeps_its_ids_and_hash_through_load_and_save(path, tmp_path, caplog):
    from dokimi.orchestrator.pipeline import compute_question_set_hash

    raw = json.loads(path.read_text(encoding="utf-8"))["questions"]
    with caplog.at_level("WARNING", logger="dokimi.questions.artifact"):
        loaded = load_questions(path)
        out = tmp_path / "v2.json"
        save_questions_artifact(loaded.artifact, out)
        again = load_questions(out).questions
    assert loaded.legacy == () and "legacy" not in caplog.text
    assert [q["id"] for q in again] == [q["id"] for q in raw]
    assert compute_question_set_hash(again) == compute_question_set_hash(raw)


#: The shapes from the review of #43 that main's pipeline wrote or passed through, which the
#: v2 writer refused on resume: an id-less record, a question_id/question_text record, a list
#: ``metadata``; plus main's bare-list form.
LEGACY_SETS = {
    "id-less": ({"questions": [{"text": "How do I use parallel_for?"}, {"id": "g2", "text": "Why?"}]},
                [("q0000", "How do I use parallel_for?"), ("g2", "Why?")],
                "1 record: no `id` (numbered by position, q0000...)"),
    "question_id-question_text": (
        {"questions": [{"question_id": "g1", "question_text": "How do I use parallel_for?"}]},
        [("g1", "How do I use parallel_for?")],
        "1 record: `question_text` read as `question`; 1 record: `question_id` read as `id`"),
    "list-metadata": ({"questions": [{"id": "g1", "text": "How do I use parallel_for?",
                                      "metadata": ["must_cite", "verified"]}]},
                      [("g1", "How do I use parallel_for?")],
                      '1 record: list `metadata` read as {"items": [...]}'),
    "bare-list": ([{"id": "g1", "question": "How do I use parallel_for?"}],
                  [("g1", "How do I use parallel_for?")], "a bare list, not an object"),
}


def test_a_list_metadata_is_kept_under_items():
    from dokimi.questions.artifact import _read_record
    from collections import Counter

    q = {"id": "g1", "text": "Q?", "metadata": ["a", {"b": 1}]}
    assert _read_record(q, 0, Counter(), set())["metadata"] == {"items": ["a", {"b": 1}]}
    assert q["metadata"] == ["a", {"b": 1}]  # not mutated


def _pipeline(tmp_path, monkeypatch, **kwargs):
    """A pipeline whose model calls fail the test; answers and judging are stubbed."""
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    def no_model_call(*a, **kw):
        raise AssertionError("a model call was made")

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", no_model_call)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", lambda self, personas: [])
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers",
                        lambda self, questions, concurrency=5, question_set_hash=None:
                        [{"question_id": q["id"]} for q in questions])
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", lambda self, *a, **kw: [])
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", lambda self, *a, **kw: "report")
    return EvaluationPipeline(product="oneTBB", output_dir=tmp_path / "run",
                              description="Threading Building Blocks", **kwargs)


def _personas(tmp_path):
    p = tmp_path / "run" / "personas" / "oneTBB.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"personas": []}), encoding="utf-8")


@pytest.mark.parametrize("mode", ["cached", "questions_from", "custom", "force_regen"])
@pytest.mark.parametrize("shape", LEGACY_SETS, ids=str)
def test_the_pipeline_resumes_on_a_legacy_set_and_writes_its_own_v2_copy(
        shape, mode, tmp_path, monkeypatch, caplog):
    payload, expected, notice = LEGACY_SETS[shape]
    _personas(tmp_path)
    cache = tmp_path / "run" / "questions" / "oneTBB.json"
    src = cache if mode == "cached" else tmp_path / "input" / "set.json"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps(payload), encoding="utf-8")
    before = src.read_bytes()
    kwargs: dict = {"cached": {}, "questions_from": {"questions_from": src},
                    "custom": {"custom_questions_path": src},
                    "force_regen": {"custom_questions_path": src, "force_regen": True}}[mode]
    if mode == "force_regen":  # a v2 cache from an earlier run, regenerated over
        save_questions_artifact({"questions": [{"id": "old", "question": "Old?"}]}, cache)
    pipeline = _pipeline(tmp_path, monkeypatch, **kwargs)
    if mode == "force_regen":  # regenerates the personas too
        monkeypatch.setattr(type(pipeline), "_discover_personas", lambda self: {"personas": []})
    a, b, c, d = _no_embeddings()
    with a, b, c, d, caplog.at_level("WARNING", logger="dokimi.questions.artifact"):
        result = pipeline.run(concurrency=1)

    saved = load_questions(cache)
    assert saved.source_version == "questions.v2" and saved.legacy == ()
    assert [(q["id"], q["question"]) for q in saved.questions] == expected
    assert saved.artifact["question_set_id"] == result["question_set_hash"]
    assert [m for m in caplog.messages if "legacy questions format" in m] == [
        f"read {src} in legacy questions format ({notice}); using it as questions.v2"]
    if mode != "cached":  # an input is never rewritten; the cache is the run's own
        assert src.read_bytes() == before
        assert not src.with_name(src.name + ".rejected").exists()


@pytest.mark.parametrize("mode", ["cached", "questions_from", "custom"])
def test_a_set_invalid_after_reading_fails_before_any_model_call(mode, tmp_path, monkeypatch):
    # No personas file: persona discovery would be the first model call.
    cache = tmp_path / "run" / "questions" / "oneTBB.json"
    src = cache if mode == "cached" else tmp_path / "input" / "set.json"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps({"questions": [{"id": "g1", "text": "Why?"}, {"id": "g2", "text": ""}]}),
                   encoding="utf-8")
    before = src.read_bytes()
    kwargs: dict = {"cached": {}, "questions_from": {"questions_from": src},
                    "custom": {"custom_questions_path": src}}[mode]
    pipeline = _pipeline(tmp_path, monkeypatch, **kwargs)
    with pytest.raises(ArtifactValidationError) as exc:
        pipeline.run(concurrency=1)
    msg = str(exc.value)
    assert f"{src} is not a usable question set" in msg
    assert "record 1 (id 'g2'), `question`: '' should be non-empty" in msg
    assert f"Fix in {src}: give each record above a non-empty question" in msg
    assert src.read_bytes() == before
    assert not pipeline.personas_path.exists()


def test_questions_from_the_runs_own_output_is_left_unchanged(tmp_path, monkeypatch):
    _personas(tmp_path)
    own = tmp_path / "run" / "questions" / "oneTBB.json"
    own.parent.mkdir(parents=True)
    own.write_text(json.dumps([{"text": "How do I use parallel_for?"}]), encoding="utf-8")
    before = own.read_bytes()
    result = _pipeline(tmp_path, monkeypatch, questions_from=tmp_path / "run").run(concurrency=1)
    assert own.read_bytes() == before
    assert result["question_set_hash"]


def test_a_stamped_v1_set_with_question_records_is_read_with_a_notice(tmp_path, caplog):
    src = tmp_path / "refined.json"  # what `questions refine` wrote before v2
    src.write_text(json.dumps({"schema_version": "questions.v1",
                               "questions": [{"id": "q1", "question": "Q?"}]}), encoding="utf-8")
    with caplog.at_level("WARNING", logger="dokimi.questions.artifact"):
        loaded = load_questions(src)
    assert (loaded.source_version, loaded.legacy) == (None, ("not valid as the questions.v1 it declares",))
    assert loaded.questions == [{"id": "q1", "question": "Q?"}]
    assert "legacy questions format (not valid as the questions.v1 it declares)" in caplog.text


def test_the_pipeline_merge_without_a_custom_set_keeps_the_generated_questions(tmp_path):
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    pipe = EvaluationPipeline.__new__(EvaluationPipeline)
    pipe.product = "oneTBB"
    pipe.questions_path = tmp_path / "questions" / "oneTBB.json"
    pipe.custom_questions_path = None
    generated = [{"id": "g1", "question": "How do I use parallel_for?", "source_type": "generated"}]
    a, b, c, d = _no_embeddings()
    with a, b, c, d:
        assert pipe._merge_questions(generated) == generated


def test_answers_generate_refuses_an_unusable_set_before_any_call(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "q.json"
    src.write_text(json.dumps([{"id": "q1", "text": ""}]), encoding="utf-8")

    def no_client(*a, **kw):
        raise AssertionError("a doc-source client was made")

    monkeypatch.setattr("dokimi.mcp.factory.create_doc_source_client", no_client)
    args = build_parser().parse_args(["answers", "generate", "--product", "oneTBB",
                                      "--questions", str(src)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "record 0 (id 'q1'), `question`: '' should be non-empty" in capsys.readouterr().err


ONE_EVALUATION = {"evaluations": [{"question_id": "q1", "with_docs": {"aggregate": 80.0},
                                   "without_docs": {"aggregate": 60.0}, "delta": 20.0}]}


def test_report_generate_reads_a_legacy_set(tmp_path, monkeypatch):
    seen: dict = {}

    def generate_report(self, eval_data, questions_data, output_format="markdown"):
        seen.update(questions_data)
        return "report"

    monkeypatch.setattr("dokimi.report.ReportGenerator.generate_report", generate_report)
    ev = tmp_path / "eval.json"
    ev.write_text(json.dumps(ONE_EVALUATION), encoding="utf-8")
    src = tmp_path / "q.json"
    src.write_text(json.dumps([{"text": "How do I use parallel_for?"}]), encoding="utf-8")
    before = src.read_bytes()
    out = tmp_path / "report.md"
    args = build_parser().parse_args(["report", "generate", "--product", "oneTBB", "--eval", str(ev),
                                      "--questions", str(src), "--output", str(out)])
    args.func(args)
    assert seen["questions"] == [{"id": "q0000", "question": "How do I use parallel_for?"}]
    assert out.read_text(encoding="utf-8") == "report" and src.read_bytes() == before


def test_a_v1_set_with_null_optional_fields_loads(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"schema_version": "questions.v1", "questions": [
        {"id": "q1", "text": "How?", "persona_id": None, "source_url": None, "topics": None}]}))
    (record,) = load_questions(path).artifact["questions"]
    assert record["question"] == "How?"
    assert not {"persona_id", "source_url", "topics"} & record.keys()


def test_a_mistyped_optional_field_is_still_refused(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": [
        {"id": "q1", "text": "How?", "topics": "perf", "persona_id": 5}]}))
    with pytest.raises(ArtifactValidationError):
        load_questions(path)


def test_report_generate_refuses_malformed_questions_with_error_exit(tmp_path, monkeypatch, capsys):
    """report generate refuses an invalid questions argument with SystemExit(1) and Error message."""
    monkeypatch.chdir(tmp_path)
    ev = tmp_path / "eval.json"
    ev.write_text(json.dumps(ONE_EVALUATION), encoding="utf-8")
    src = tmp_path / "q.json"
    # Pass a dict where a list is expected: invalid structure
    src.write_text(json.dumps({"questions": {"a": 1}}), encoding="utf-8")
    out = tmp_path / "report.md"
    args = build_parser().parse_args(["report", "generate", "--product", "oneTBB", "--eval", str(ev),
                                      "--questions", str(src), "--output", str(out)])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 2
    assert "Error:" in capsys.readouterr().err


def test_questions_refine_refuses_malformed_questions_with_error_exit(tmp_path, monkeypatch, capsys):
    """questions refine refuses an invalid questions argument with SystemExit(1) and Error message."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "q.json"
    # Pass an empty dict where a list or questions object is expected: invalid structure
    src.write_text(json.dumps({}), encoding="utf-8")
    out = tmp_path / "refined.json"
    args = build_parser().parse_args(["eval", "questions", "refine", "--product", "oneTBB",
                                      "--questions", str(src), "--output", str(out)])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 2
    assert "Error:" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_questions_refine_names_an_unreadable_questions_file(tmp_path, capsys, kind):
    """A missing or directory --questions exits 1 naming the path, not a traceback."""
    src = tmp_path / "q.json"
    if kind == "directory":
        src.mkdir()
    args = build_parser().parse_args(["eval", "questions", "refine", "--product", "oneTBB",
                                      "--questions", str(src), "--output", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 2
    assert f"Error: cannot read {src}" in capsys.readouterr().err


@pytest.mark.parametrize("top, record", [
    pytest.param({"total_questions": None}, {}, id="total_questions"),
    pytest.param({"model": None}, {}, id="model"),
    pytest.param({}, {"source_type": None}, id="source_type"),
    pytest.param({}, {"ground_truth_chunk": None}, id="ground_truth_chunk"),
    pytest.param({}, {"question": None}, id="question-beside-text"),
])
def test_a_null_in_any_key_v1_leaves_open_loads(tmp_path, top, record):
    """Every key v1 does not declare accepts null there; v2's types do not refuse it on read."""
    from dokimi.artifacts import validate_artifact

    data = {"schema_version": "questions.v1", **top,
            "questions": [{"id": "q1", "text": "How?", **record}]}
    validate_artifact("questions", data)  # valid v1, as on main
    path = tmp_path / "q.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_questions(path)
    assert loaded.questions[0] == {"id": "q1", "question": "How?"}
    assert not top.keys() & loaded.artifact.keys()


def test_the_dropped_null_keys_are_the_ones_v1_leaves_open():
    """Read from the schemas: v2-typed keys v1 does not declare; never a key v1 declares."""
    from dokimi.questions.artifact import _v1_open_nulls

    top, record = _v1_open_nulls()
    assert {"persona_id", "source_url", "topics", "metadata", "source_type",
            "ground_truth_chunk"} <= record
    assert {"total_questions", "model"} <= top
    assert not {"id", "text", "persona", "category"} & record



def test_report_generate_prints_an_error_for_an_empty_question(tmp_path, capsys):
    ev = tmp_path / "eval.json"
    ev.write_text(json.dumps(ONE_EVALUATION), encoding="utf-8")
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"questions": [{"id": "q1", "question": ""}]}), encoding="utf-8")
    args = build_parser().parse_args(["report", "generate", "--product", "oneTBB", "--eval", str(ev),
                                      "--questions", str(src), "--output", str(tmp_path / "r.md")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("Error: ")


def _open_null_cases():
    from dokimi.questions.artifact import _v1_open_nulls

    top, record = _v1_open_nulls()
    return ([pytest.param({k: None}, {}, id=f"top-{k}") for k in sorted(top)]
            + [pytest.param({}, {k: None}, id=f"record-{k}") for k in sorted(record)
               if k not in ("id", "text")])


@pytest.mark.parametrize("top, record", _open_null_cases())
def test_every_key_v1_leaves_open_drops_its_null_on_read(tmp_path, top, record):
    data = {"schema_version": "questions.v1", **top,
            "questions": [{"id": "q1", "text": "How?", **record}]}
    validate_artifact("questions", data)
    path = tmp_path / "q.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_questions(path)
    assert loaded.questions[0] == {"id": "q1", "question": "How?"}
    assert not top.keys() & loaded.artifact.keys()


def test_a_null_in_a_key_v1_declares_is_still_refused(tmp_path):
    data = {"schema_version": "questions.v1",
            "questions": [{"id": "a", "text": "hi", "expected_topics": None}]}
    path = tmp_path / "q.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="expected_topics"):
        load_questions(path)


def test_questions_refine_repairs_a_set_that_strict_reading_refuses(tmp_path, monkeypatch):
    """Refine reads without validating: a duplicate that strict reading refuses is dropped, not fatal."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "q.json"
    text = "Why does my task_arena deadlock under nested parallelism?"
    src.write_text(json.dumps({"questions": [
        {"id": "q1", "text": text, "topics": ["arena"]},
        {"id": "q2", "text": text, "topics": "perf"},  # not a list: strict reading refuses the file
    ]}), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="topics"):
        load_questions(src)
    out = tmp_path / "refined.json"
    args = build_parser().parse_args(["eval", "questions", "refine", "--product", "oneTBB",
                                      "--questions", str(src), "--output", str(out)])
    args.func(args)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [q["id"] for q in data["questions"]] == ["q1"]


def test_questions_refine_prints_an_error_when_the_refined_set_is_invalid(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"questions": [
        {"id": "q1", "text": ""},
        {"id": "q2", "text": "Why does my task_arena deadlock under nested parallelism?"},
    ]}), encoding="utf-8")
    out = tmp_path / "refined.json"
    args = build_parser().parse_args(["eval", "questions", "refine", "--product", "oneTBB",
                                      "--questions", str(src), "--output", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "Error:" in capsys.readouterr().err
    assert not out.exists()


def test_questions_refine_fill_gaps_refuses_an_invalid_set_before_any_model_call(
        tmp_path, monkeypatch, capsys):
    from dokimi.questions.refiner import GapFiller

    def fail_fill(self, *a, **k):
        raise AssertionError("GapFiller.fill must not run on an invalid set")

    monkeypatch.setattr(GapFiller, "fill", fail_fill)
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"questions": [
        {"id": "q1", "text": ""},
        {"id": "q2", "text": "Why does my task_arena deadlock under nested parallelism?"},
    ]}), encoding="utf-8")
    out = tmp_path / "refined.json"
    args = build_parser().parse_args(["eval", "questions", "refine", "--product", "oneTBB",
                                      "--questions", str(src), "--output", str(out),
                                      "--fill-gaps"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "Error:" in capsys.readouterr().err
    assert not out.exists()


def test_checklist_points_load_from_a_record_without_question_text(tmp_path):
    from dokimi.eval.checklist import load_points

    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": [{"id": "q1", "expected_topics": ["a point"]}]}),
                    encoding="utf-8")
    assert load_points(str(path)) == {"q1": ["a point"]}

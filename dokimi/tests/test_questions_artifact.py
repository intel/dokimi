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


def test_the_pipeline_refuses_a_merged_set_with_an_id_less_custom_question(tmp_path):
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps([{"text": "How do I use parallel_for?"}]), encoding="utf-8")
    pipe = EvaluationPipeline.__new__(EvaluationPipeline)
    pipe.product = "oneTBB"
    pipe.questions_path = tmp_path / "questions" / "oneTBB.json"
    pipe.custom_questions_path = custom
    a, b, c, d = _no_embeddings()
    with a, b, c, d, pytest.raises(ArtifactValidationError, match="'id' is a required property"):
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
    args = build_parser().parse_args(["questions", "refine", "--questions", str(src),
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
        "catalog", "trigger-set", "--catalog", str(tmp_path / "skills"), "--format", "questions",
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


def test_load_questions_without_validation_reports_only_the_declared_version(tmp_path):
    out = tmp_path / "q.json"
    out.write_text(json.dumps({"questions": [{"text": "no id"}]}), encoding="utf-8")
    loaded = load_questions(out, validate=False)
    assert loaded.source_version is None
    assert loaded.questions == [{"question": "no id"}]
    with pytest.raises(ArtifactValidationError):
        load_questions(out)


def test_load_questions_rejects_a_bare_list_without_validation(tmp_path):
    out = tmp_path / "q.json"
    out.write_text(json.dumps([{"id": "q1", "text": "Q?"}]), encoding="utf-8")
    with pytest.raises(TypeError, match="JSON object"):
        load_questions(out, validate=False)


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
    src.write_text(json.dumps({"questions": [{"text": "no id"}]}), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        export_artifact("questions", src, tmp_path / "strict")
    rows = _export(src, tmp_path / "out", "--no-validate")
    assert rows == [{"schema_version": "questions.v2", "question": "no id"}]
    card = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert "Source schema version:** unknown" in card

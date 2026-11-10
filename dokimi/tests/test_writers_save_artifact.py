# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The JSON of `eval personas discover`, `eval questions review|analyze` and the `report eval`
sidecar goes through save_artifact (#229 P2-6, #277 B1b): `schema_version` first, the run envelope,
listed by `runs list`, and a payload its schema refuses is kept at `<name>.rejected` with exit 1."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi import __version__, artifacts, cli
from dokimi.artifacts import load_artifact
from dokimi.commands.questions import _load_personas
from dokimi.personas import generator as generator_mod
from dokimi.personas.analyzer import PersonaAnalyzer
from dokimi.questions import panel_reviewer, quality_analyzer

RUN = "t35-run"
PERSONA = {"id": "hpc_dev", "name": "HPC Developer", "description": "Writes parallel loops.",
           "skill_level": "advanced", "concerns": ["overhead"], "typical_questions": ["How?"]}
ANALYSIS = {"readme_content": "# lib", "use_cases": ["loops"],
            "issues_analysis": {"common_questions": ["Why?"], "common_labels": ["question"],
                                "sample_issues": [{"title": "Why?", "labels": ["question"], "url": "u"}]},
            "api_patterns": ["lib::run"], "description": "A library", "topics": ["cpp"]}


@pytest.fixture(autouse=True)
def one_run(monkeypatch):
    monkeypatch.setenv(artifacts.RUN_ID_ENV, RUN)


def _cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _saved(path: Path, kind: str) -> dict:
    """The file, after checking it leads with its schema_version and the envelope and loads as ``kind``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data)[:3] == ["schema_version", "run_id", "dokimi_version"]
    assert (data["schema_version"], data["run_id"], data["dokimi_version"]) == \
        (artifacts.SCHEMA_VERSIONS[kind], RUN, __version__)
    assert load_artifact(kind, path) == data
    return data


def _listed(monkeypatch, capsys, root: Path) -> dict[str, tuple[str, str | None]]:
    """`runs list --json` over ``root``: path -> (kind, run_id)."""
    capsys.readouterr()
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(root))
    assert _cli(monkeypatch, "runs", "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)["artifacts"]
    return {row["path"]: (row["kind"], row["run_id"]) for row in rows}


def _rejected(path: Path) -> dict:
    assert not path.exists()
    return json.loads(path.with_name(path.name + ".rejected").read_text(encoding="utf-8"))


# ── eval personas discover ────────────────────────────────────────────────────

@pytest.fixture
def persona_reply(monkeypatch):
    """The generator's model replies with the personas in ``reply["personas"]``."""
    reply = {"personas": [PERSONA]}
    model = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps(reply)))
    monkeypatch.setattr(generator_mod, "LANGCHAIN_AVAILABLE", True)
    monkeypatch.setattr(generator_mod, "LLM", lambda *_a, **_k: None)
    monkeypatch.setattr(generator_mod, "Invoker", lambda _llm: model)
    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository", lambda _self, _repo: dict(ANALYSIS))
    return reply


def _discover(monkeypatch, out: Path, *extra: str) -> int:
    return _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--output", str(out),
                "--provider", "openai", *extra)


def test_discovered_personas_are_a_personas_artifact_that_readers_still_read(
        tmp_path, monkeypatch, capsys, persona_reply):
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--description", "A library") == 0

    data = _saved(out, "personas")
    assert (data["product"], data["provider"], data["personas"]) == ("lib", "openai", [PERSONA])
    assert _listed(monkeypatch, capsys, tmp_path) == {"personas/lib.json": ("personas", RUN)}
    assert _load_personas(str(out)) == [PERSONA]  # `eval questions generate --personas`
    assert _cli(monkeypatch, "eval", "personas", "validate", "--file", str(out)) == 0


def test_the_saved_analysis_is_a_persona_analysis_artifact_of_the_same_run(
        tmp_path, monkeypatch, capsys, persona_reply):
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--repo", "o/r", "--save-analysis") == 0

    assert {k: v for k, v in _saved(tmp_path / "personas" / "lib_analysis.json", "persona_analysis").items()
            if k in ANALYSIS} == ANALYSIS
    assert _listed(monkeypatch, capsys, tmp_path) == {
        "personas/lib.json": ("personas", RUN),
        "personas/lib_analysis.json": ("persona_analysis", RUN),
    }


def test_a_persona_the_schema_refuses_is_kept_as_rejected_and_exits_1(
        tmp_path, monkeypatch, capsys, persona_reply):
    """The generator checks that a persona's fields are there, not their types: an integer id."""
    persona_reply["personas"] = [{**PERSONA, "id": 7}]
    out = tmp_path / "lib.json"
    assert _discover(monkeypatch, out, "--description", "A library") == 1

    assert _rejected(out)["personas"][0]["id"] == 7
    err = capsys.readouterr().err
    assert err.startswith("Error: personas artifact rejected: ") and "personas/0/id" in err


def test_an_analysis_the_schema_refuses_is_kept_as_rejected_and_exits_1(
        tmp_path, monkeypatch, capsys, persona_reply):
    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository",
                        lambda _self, _repo: {**ANALYSIS, "use_cases": "loops"})
    out = tmp_path / "lib.json"
    assert _discover(monkeypatch, out, "--repo", "o/r", "--save-analysis") == 1

    assert _rejected(tmp_path / "lib_analysis.json")["use_cases"] == "loops"
    assert not out.exists()  # the run stops before the paid generator call
    assert capsys.readouterr().err.startswith("Error: persona_analysis artifact rejected: ")


# ── eval questions review ─────────────────────────────────────────────────────

def _questions(tmp_path: Path) -> Path:
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": [{"question": "How do I run a loop?"},
                                              {"question": "Why is it slow?"}]}), encoding="utf-8")
    return path


def _review(monkeypatch, tmp_path: Path, out: Path) -> int:
    reply = {"technical_accuracy": 90, "relevance": 80, "depth": 70, "flags": [], "reasoning": "ok"}
    monkeypatch.setattr(panel_reviewer, "llm_call", lambda *_a, **_k: json.dumps(reply))
    return _cli(monkeypatch, "eval", "questions", "review", "--questions", str(_questions(tmp_path)),
                "--product", "lib", "--reviewers", "domain_expert", "--output", str(out))


def test_the_question_panel_report_is_a_question_panel_artifact(tmp_path, monkeypatch, capsys):
    out = tmp_path / "reports" / "lib_question_panel.json"
    assert _review(monkeypatch, tmp_path, out) == 0

    data = _saved(out, "question_panel")
    assert (data["library_name"], data["total"], data["summary"]["keep"]) == ("lib", 2, 2)
    assert [q["panel_score"] for q in data["questions"]] == [80.0, 80.0]
    assert _listed(monkeypatch, capsys, tmp_path) == {
        "reports/lib_question_panel.json": ("question_panel", RUN)}


def test_each_incremental_panel_save_is_a_whole_artifact(tmp_path, monkeypatch):
    """The report is saved after every question; a run cut short leaves a valid file."""
    out = tmp_path / "panel.json"
    seen: list[dict] = []
    review = panel_reviewer.QuestionPanelReviewer.review_question

    def review_then_read(self, question, library_name):
        if out.exists():
            seen.append(load_artifact("question_panel", out))
        return review(self, question, library_name)

    monkeypatch.setattr(panel_reviewer.QuestionPanelReviewer, "review_question", review_then_read)
    assert _review(monkeypatch, tmp_path, out) == 0
    assert [(d["total"], d["summary"]["keep"]) for d in seen] == [(1, 1)]


def test_an_empty_question_set_still_gets_a_panel_report(tmp_path, monkeypatch):
    """No question means no incremental save; the final one still writes the report."""
    path = tmp_path / "q.json"
    path.write_text('{"questions": []}', encoding="utf-8")
    out = tmp_path / "panel.json"
    assert _cli(monkeypatch, "eval", "questions", "review", "--questions", str(path),
                "--product", "lib", "--output", str(out)) == 0
    data = _saved(out, "question_panel")
    assert (data["total"], data["questions"], data["summary"]["mean_panel_score"]) == (0, [], None)


def test_a_panel_report_the_schema_refuses_is_kept_as_rejected_and_exits_1(tmp_path, monkeypatch, capsys):
    """A recommendation the panel does not make (a writer bug) stops the run at the first save."""
    aggregate = panel_reviewer.QuestionPanelReviewer._aggregate
    monkeypatch.setattr(panel_reviewer.QuestionPanelReviewer, "_aggregate",
                        lambda self, q, votes: replace(aggregate(self, q, votes), recommendation="maybe"))
    out = tmp_path / "panel.json"
    assert _review(monkeypatch, tmp_path, out) == 1

    kept = _rejected(out)
    assert [q["recommendation"] for q in kept["questions"]] == ["maybe"]
    assert capsys.readouterr().err.startswith("Error: question_panel artifact rejected: ")


# ── eval questions analyze ────────────────────────────────────────────────────

def _analyze(monkeypatch, tmp_path: Path, out: Path, difficulty: object = "advanced",
             reason: object = "needs the docs") -> int:
    reply = {"difficulty": difficulty, "trivial": False, "reason": reason}
    monkeypatch.setattr(quality_analyzer, "llm_call", lambda *_a, **_k: json.dumps(reply))
    return _cli(monkeypatch, "eval", "questions", "analyze", "--questions", str(_questions(tmp_path)),
                "--product", "lib", "--output", str(out))


def test_the_question_quality_report_is_a_question_quality_artifact(tmp_path, monkeypatch, capsys):
    out = tmp_path / "reports" / "lib_question_quality.json"
    assert _analyze(monkeypatch, tmp_path, out) == 0

    data = _saved(out, "question_quality")
    assert (data["library_name"], data["total"], data["difficulty_distribution"]) == \
        ("lib", 2, {"beginner": 0, "intermediate": 0, "advanced": 2})
    assert _listed(monkeypatch, capsys, tmp_path) == {
        "reports/lib_question_quality.json": ("question_quality", RUN)}


def test_a_difficulty_that_is_not_text_reads_as_intermediate_and_the_report_is_saved(tmp_path, monkeypatch):
    """A model quirk is not a rejected save: the paid report is written, the reply's fault in `error`."""
    out = tmp_path / "quality.json"
    assert _analyze(monkeypatch, tmp_path, out, difficulty=3, reason=["needs", "docs"]) == 0

    data = _saved(out, "question_quality")
    assert data["difficulty_distribution"] == {"beginner": 0, "intermediate": 2, "advanced": 0}
    assert {(q["difficulty"], q["reason"], q["error"]) for q in data["questions"]} == \
        {("intermediate", "['needs', 'docs']", "the reply's difficulty is not text: 3")}


def test_a_quality_report_the_schema_refuses_is_kept_as_rejected_and_exits_1(tmp_path, monkeypatch, capsys):
    """A recommendation that is not text (a writer bug) is refused after the calls; the payload is kept."""
    monkeypatch.setattr(quality_analyzer.QuestionQualityAnalyzer, "_build_recommendations",
                        staticmethod(lambda *_a: [None]))
    out = tmp_path / "quality.json"
    assert _analyze(monkeypatch, tmp_path, out) == 1

    assert _rejected(out)["recommendations"] == [None]
    err = capsys.readouterr().err
    assert err.startswith("Error: question_quality artifact rejected: ") and "recommendations/0" in err


# ── report eval: the Q&A sidecar ──────────────────────────────────────────────

def _eval_file(tmp_path: Path, question_text: object = "How do I run a loop?") -> Path:
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": [
        {"question_id": "q1", "question_text": question_text, "difficulty": "beginner",
         "with_docs": {"aggregate": 80.0}, "without_docs": {"aggregate": 60.0}, "delta": 20.0}]}),
        encoding="utf-8")
    return path


def test_the_report_qa_sidecar_is_a_report_qa_artifact(tmp_path, monkeypatch, capsys):
    out = tmp_path / "reports" / "lib_full.md"
    assert _cli(monkeypatch, "report", "eval", "--product", "lib", "--eval", str(_eval_file(tmp_path)),
                "--out", str(out)) == 0

    assert out.read_text(encoding="utf-8").startswith("#")  # the markdown report stays as it was
    data = _saved(tmp_path / "reports" / "lib_full.qa.json", "report_qa")
    assert (data["count"], data["report_path"]) == (1, str(out))
    assert data["records"][0] == {
        "question_id": "q1", "question_text": "How do I run a loop?", "difficulty": "beginner",
        "source": "dynamic", "with_docs_score": 80.0, "without_docs_score": 60.0, "delta": 20.0,
        "with_docs_answer": None, "without_docs_answer": None}
    assert _listed(monkeypatch, capsys, tmp_path) == {"reports/lib_full.qa.json": ("report_qa", RUN)}


def test_a_question_text_that_is_not_text_reads_as_none_in_the_sidecar(tmp_path, monkeypatch):
    """The sidecar takes the record's text, not the raw value: a bad eval file is not a rejected save."""
    out = tmp_path / "lib_full.md"
    assert _cli(monkeypatch, "report", "eval", "--product", "lib", "--eval",
                str(_eval_file(tmp_path, question_text=5)), "--out", str(out)) == 0
    assert _saved(tmp_path / "lib_full.qa.json", "report_qa")["records"][0]["question_text"] is None


def test_a_sidecar_the_schema_refuses_is_kept_as_rejected_and_exits_1(tmp_path, monkeypatch, capsys):
    """An answer that is not text (a writer bug) is refused; the markdown report is already written."""
    from dokimi.report import eval_report

    answer = SimpleNamespace(answer=lambda _condition: SimpleNamespace(text=7))
    monkeypatch.setattr(eval_report, "_answers_by_id", lambda _path: {"q1": answer})
    out = tmp_path / "lib_full.md"
    assert _cli(monkeypatch, "report", "eval", "--product", "lib", "--eval", str(_eval_file(tmp_path)),
                "--out", str(out)) == 1

    assert out.exists()
    assert _rejected(tmp_path / "lib_full.qa.json")["records"][0]["with_docs_answer"] == 7
    err = capsys.readouterr().err
    assert err.startswith("Error: report_qa artifact rejected: ") and "records/0/with_docs_answer" in err

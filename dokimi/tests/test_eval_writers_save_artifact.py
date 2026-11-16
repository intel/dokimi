# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The eval writers that bypassed save_artifact (#277 R4) now save through it: each run they write
carries the run envelope (run_id, dokimi_version) and its schema_version."""

from __future__ import annotations

import json
import types

import pytest

from dokimi import artifacts, __version__
from dokimi.cli import build_parser
from dokimi.commands.evaluate import _run_ragas_eval
from dokimi.exit_codes import EXIT_INCONCLUSIVE
from dokimi.eval import ragas_eval
from tests.test_eval_output_write_errors import _JUDGE_COMMANDS, _Report, _stub_judge_commands
from tests.test_judge_validity import _answers

EXPECTED_VERSION = {
    "pairwise": "pairwise.v1",
    "checklist": "checklist.v1",
    "judge-wording": "judge_wording.v1",
    "cross-judge": "cross_judge.v1",
}


@pytest.fixture(autouse=True)
def _run_id(monkeypatch):
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "r4-run")


def _assert_saved_with_envelope(path, version, payload_key):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data)[:3] == ["schema_version", "run_id", "dokimi_version"]
    assert (data["schema_version"], data["run_id"], data["dokimi_version"]) == (
        version,
        "r4-run",
        __version__,
    )
    # the envelope alone passes for a writer that saves an empty report: the payload must be there too
    assert payload_key in data


@pytest.mark.parametrize("command", list(EXPECTED_VERSION))
def test_each_judge_reliability_writer_saves_with_the_envelope(command, tmp_path, monkeypatch):
    import dokimi.eval.cross_judge as cross_judge
    from tests.test_eval_output_write_errors import _Report as StubReport

    _stub_judge_commands(monkeypatch)
    # The report's own payload must reach the file: a writer that drops it still writes the envelope and the
    # command's judge_temperature, so the check reads a key only the report writes.
    monkeypatch.setattr(StubReport, "to_dict", lambda self: {"report_marker": command})
    # The stub report scores nothing; the command reads `unscored()` after it has saved.
    monkeypatch.setattr(
        cross_judge, "CrossJudgeReport", type("Report", (_Report,), {"unscored": lambda self: []})
    )
    out = tmp_path / "out.json"
    args = build_parser().parse_args(
        [*_JUDGE_COMMANDS[command], "--answers", "a.json", "--output", str(out)]
    )
    args.func(args)
    _assert_saved_with_envelope(out, EXPECTED_VERSION[command], "judge_temperature")
    assert json.loads(out.read_text(encoding="utf-8"))["report_marker"] == command


def test_grounding_output_saves_with_the_envelope(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "answers": [
                    {
                        "question_id": "q1",
                        "question_text": "How do I loop?",
                        "with_docs": {
                            "answer": "use parallel_for",
                            "retrieved_docs": [{"snippet": "parallel_for runs tasks"}],
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "grounding.json"
    args = build_parser().parse_args(
        ["eval", "score", "grounding", "--answers", str(answers), "--output", str(out)]
    )
    args.func(args)
    _assert_saved_with_envelope(out, "grounding.v1", "summary")


def test_ragas_output_saves_with_the_envelope(tmp_path, monkeypatch):
    class Evaluator:
        def __init__(self, **kw):
            pass

        def evaluate(self, answers, include_without_docs=True):
            return types.SimpleNamespace(
                to_dict=lambda: {"ragas_summary": {}}, format_summary=lambda: ""
            )

    monkeypatch.setattr(ragas_eval, "RagasEvaluator", Evaluator)
    args = types.SimpleNamespace(ragas_model="m", ragas_provider="anthropic")
    _run_ragas_eval([], tmp_path / "answers.json", args)
    _assert_saved_with_envelope(tmp_path / "answers_ragas.json", "ragas.v1", "ragas_summary")
    # The judge that scored the answers is recorded, so runs list and show can name it.
    saved = json.loads((tmp_path / "answers_ragas.json").read_text(encoding="utf-8"))
    assert saved["ragas_model"] == "anthropic/m"


def test_a_payload_its_schema_refuses_exits_1_and_keeps_it_beside_the_path(tmp_path, capsys):
    from dokimi.commands._output import save_artifact_or_exit

    out = tmp_path / "grounding.json"
    with pytest.raises(SystemExit) as exc:
        save_artifact_or_exit("grounding", {"summary": 5}, out, code=2)
    assert exc.value.code == 1  # a writer bug, not an unwritable path
    assert not out.exists() and (tmp_path / "grounding.json.rejected").exists()
    assert "payload kept at" in capsys.readouterr().err


def test_grounding_command_exits_1_when_its_payload_is_refused(tmp_path, monkeypatch):
    import dokimi.eval.grounding as grounding

    summary = {
        "n_evaluated": 1,
        "grounding_score": {"mean": None, "lo": None, "hi": None},
        "citation_rate": 0.0,
        "threshold": 0.5,
    }
    monkeypatch.setattr(
        grounding,
        "evaluate_grounding",
        lambda answers, threshold: {"summary": summary, "per_question": 5},
    )
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")
    out = tmp_path / "grounding.json"
    args = build_parser().parse_args(
        ["eval", "score", "grounding", "--answers", str(answers), "--output", str(out)]
    )
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert not out.exists() and (tmp_path / "grounding.json.rejected").exists()


def test_validity_report_is_saved_with_the_envelope(tmp_path, monkeypatch):
    # Every eval report goes through save_artifact, so runs list and show can read it.
    import dokimi.eval.judge as judge_mod
    import dokimi.eval.judge_validity as jv

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_answers()))
    out = tmp_path / "validity.json"
    monkeypatch.setattr(judge_mod, "Judge", lambda **kw: None)
    monkeypatch.setattr(jv, "judge_scorer", lambda judge: lambda lib, q, a: float(len(a)))
    args = build_parser().parse_args(["eval", "judge", "validate", "--answers", str(answers),
                                      "--variants", "truncate_20", "--output", str(out)])
    with pytest.raises(SystemExit) as exc:  # an inconclusive sample: the report is saved, then the command exits 3 (§2.6)
        args.func(args)
    assert exc.value.code == EXIT_INCONCLUSIVE
    saved = json.loads(out.read_text())
    assert saved["schema_version"] == "judge_validity.v1"
    assert saved["run_id"] and saved["dokimi_version"]
    # A report saved as only its summary passes the envelope checks, so the rows and the gate's measures are pinned too.
    assert saved["rows"], "the report's rows are not in the file"
    assert {"judge", "n_pairs"} <= set(saved["summary"])
    assert saved["summary"]["n_pairs"] >= 1


def test_the_validity_schema_declares_the_rows_a_report_writes():
    # the closed-key check: `rows` is what ValidityReport.to_dict() writes, so the schema must name it
    from tests.artifact_fixtures import artifact_fixture

    fixture = artifact_fixture("judge_validity", {"summary": {"n_pairs": 1, "judge": "openai/m"}, "rows": []})
    assert fixture["rows"] == []


def test_a_refused_payload_whose_rejected_sibling_cannot_be_written_still_exits_1(tmp_path, capsys):
    # the documented exit for a refused payload is 1, whether or not the .rejected copy could be kept
    from dokimi.commands._output import save_artifact_or_exit

    out = tmp_path / "grounding.json"
    (tmp_path / "grounding.json.rejected").mkdir()
    with pytest.raises(SystemExit) as exc:
        save_artifact_or_exit("grounding", {"summary": 5}, out, code=2)
    assert exc.value.code == 1
    assert not out.exists()
    assert "grounding.json" in capsys.readouterr().err

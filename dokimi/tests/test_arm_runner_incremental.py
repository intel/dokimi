# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An arms run keeps what it has paid for when the process dies before its save (#228 step 4).

After each answered question the run rewrites ``<out stem>.partial.json``, an ``arms.v1`` file with
a ``partial`` block; the whole run's save removes it. The readers refuse the partial file.
The contract: docs/evaluating-treatments.md, the paragraph "While it answers ..." under Quick start.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
import dokimi.eval.arms_run as arms_run
from dokimi import records
from dokimi.artifacts import load_artifact, stamp, validate_artifact
from dokimi.cli import build_parser
from dokimi.treatments import BaselineTreatment
from dokimi.treatments.base import AgentConfig, Treatment
from dokimi.utils import InputError
from tests.cli_refusal import refusal

PROJECT = Path(__file__).resolve().parents[1]
QUESTIONS = [{"id": f"q{i}", "question": f"How do I do thing {i}?"} for i in range(4)]
ARMS = "baseline,profile:" + str(PROJECT / "data" / "agent_profiles" / "concise_expert.md")

# The child answers through a fake model and dies (no cleanup, no save) on the call after `limit`.
_KILLED_CHILD = """
import os, sys
import dokimi.eval.arm_runner as arm_runner
calls, limit = [], int(sys.argv[1])
def fake(prompt, model, provider="openai", **kw):
    calls.append(prompt)
    if len(calls) > limit:
        os._exit(137)
    return "answer %d" % len(calls), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
arm_runner.llm_call_with_usage = fake
from dokimi.cli import main
sys.argv = ["cli.py", *sys.argv[2:]]
main()
"""


def _questions(tmp_path: Path) -> Path:
    path = tmp_path / "q.json"
    path.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    return path


def _arms_argv(tmp_path: Path, *extra: str) -> list[str]:
    return ["arms", "run", "--product", "onedal", "--questions", str(_questions(tmp_path)),
            "--arms", ARMS, "--concurrency", "1",
            "--out", str(tmp_path / "arms.json"), "--out-md", str(tmp_path / "arms.md"), *extra]


def _fake_llm(monkeypatch) -> list:
    calls = []

    def fake(prompt, model, provider="openai", **kw):
        calls.append(prompt)
        return f"answer {len(calls)}", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    return calls


class _FakeJudge:
    model = "fake-judge"
    provider = "fake-provider"
    temperature = None
    prompt_sha256 = "sha256:fake"

    def score_answer(self, library_name, question, answer, context="", ground_truth=None):
        return {"aggregate": 50 + len(answer) % 7, "correctness": 3}


def _run(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_a_killed_run_leaves_its_answered_questions_in_a_valid_partial_file(tmp_path):
    # Killed (os._exit, as SIGKILL or the OOM killer) on the first call of the third question:
    # two questions of two arms were paid for.
    finished = tmp_path / "arms.json"
    finished.write_text('{"an earlier": "finished run"}', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(PROJECT)}
    proc = subprocess.run([sys.executable, "-c", _KILLED_CHILD, "4", *_arms_argv(tmp_path)],
                          cwd=PROJECT, env=env, capture_output=True, text=True, timeout=120)

    assert proc.returncode == 137, proc.stderr
    assert finished.read_text(encoding="utf-8") == '{"an earlier": "finished run"}'  # never replaced
    partial = tmp_path / "arms.partial.json"
    data = json.loads(partial.read_text(encoding="utf-8"))
    validate_artifact("arms", data)
    assert data["partial"] == {"questions_requested": 4, "questions_recorded": 2}
    assert [a["question_id"] for a in data["answers"]] == ["q0", "q1"]
    assert [a["arms"]["baseline"]["answer"] for a in data["answers"]] == ["answer 1", "answer 3"]
    assert data["cost_summary"]["baseline"]["total_tokens"] == 30  # the spend so far
    assert "evaluations" not in data and "summary" not in data


def _partial_file(tmp_path: Path) -> Path:
    path = tmp_path / "arms.partial.json"
    path.write_text(json.dumps({
        "schema_version": "arms.v1", "arms": ["baseline"], "baseline_arm": "baseline",
        "partial": {"questions_requested": 4, "questions_recorded": 1},
        "answers": [{"question_id": "q0", "question_text": "?", "arms": {"baseline": {"answer": "a"}}}],
    }), encoding="utf-8")
    return path


REFUSAL = "is the partial file of a run that did not finish: 1 of 4 questions answered"


def test_the_record_reader_refuses_a_partial_file(tmp_path):
    with pytest.raises(records.RecordError, match=REFUSAL):
        records.load(_partial_file(tmp_path))


def test_load_artifact_refuses_a_partial_arms_file(tmp_path):
    with pytest.raises(InputError, match=REFUSAL):
        load_artifact("arms", _partial_file(tmp_path))


def test_headroom_and_plugin_delta_exit_2_on_a_partial_file(tmp_path, capsys):
    partial = str(_partial_file(tmp_path))
    with refusal() as exc:
        _run(["eval", "headroom", partial, partial])
    assert exc.code == 2
    with refusal() as exc:
        _run(["arms", "plugin-delta", "--baseline-json", partial, "--plugin-json", partial])
    assert exc.code == 2
    assert capsys.readouterr().err.count(REFUSAL) == 2


def test_a_crash_mid_run_keeps_the_questions_answered_in_the_partial_file(tmp_path, monkeypatch):
    # A non-RunStopped exception (a bug, say) on the third question ends the run before its save;
    # the question queued behind it is still answered, as on main, and now kept.
    calls = _fake_llm(monkeypatch)
    real_record = arm_runner_mod.question_record

    def record(library_name, q, qid, *a, **k):
        if qid == "q2":
            raise RuntimeError("boom")
        return real_record(library_name, q, qid, *a, **k)

    monkeypatch.setattr(arm_runner_mod, "question_record", record)
    with pytest.raises(RuntimeError, match="boom"):
        _run(_arms_argv(tmp_path))

    assert len(calls) == 8
    assert not (tmp_path / "arms.json").exists()
    data = json.loads((tmp_path / "arms.partial.json").read_text(encoding="utf-8"))
    assert data["partial"] == {"questions_requested": 4, "questions_recorded": 3}
    assert [a["question_id"] for a in data["answers"]] == ["q0", "q1", "q3"]


def _normalized(data: dict) -> dict:
    """*data* without the fields a second run cannot reproduce: the save time and the arms' timings."""
    data = json.loads(json.dumps(data))
    data.pop("generated_at")
    for rec in data["answers"]:
        for cell in rec["arms"].values():
            cell.pop("elapsed_sec")
            cell["metrics"].pop("latency_sec", None)
    return data


def test_a_finished_run_saves_the_same_file_as_without_partial_saves(tmp_path, monkeypatch):
    # The characterization: the whole run's file is what run_prepared returns with no partial
    # saves (main's path), byte for byte past the timings; the partial file is gone.
    monkeypatch.setattr(arms_run, "_build_judge", lambda config: _FakeJudge())
    _fake_llm(monkeypatch)
    judged = ["--judge", "--judge-provider", "openai", "--judge-model", "gpt-4o"]
    saves = []
    real_save = arms_run.partial_output

    def counted(runner, config, done):
        saves.append(len(done))
        return real_save(runner, config, done)

    monkeypatch.setattr(arms_run, "partial_output", counted)
    _run(_arms_argv(tmp_path, *judged))

    assert saves == [1, 2, 3, 4]
    assert not (tmp_path / "arms.partial.json").exists()
    saved = json.loads((tmp_path / "arms.json").read_text(encoding="utf-8"))

    from dokimi.commands.arms import _parse_arm_specs, run_config_from_args

    args = build_parser().parse_args(_arms_argv(tmp_path, *judged))
    config = run_config_from_args(args)
    prepared = arms_run.prepare_arms(config, _parse_arm_specs(args.arms), QUESTIONS)
    _fake_llm(monkeypatch)
    before = stamp("arms", arms_run.run_prepared(prepared, config, QUESTIONS))
    assert "partial" not in saved and saved["evaluations"]
    assert json.dumps(_normalized(saved), indent=2) == json.dumps(_normalized(before), indent=2)


def test_an_unwritable_out_exits_2_before_any_answer_is_paid_for(tmp_path, monkeypatch, capsys):
    calls = _fake_llm(monkeypatch)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    argv = _arms_argv(tmp_path)
    argv[argv.index("--out") + 1] = str(blocker / "arms.json")
    with refusal() as exc:
        _run(argv)
    assert exc.code == 2
    assert calls == []
    assert f"Error: cannot write {blocker / 'arms.json'}" in capsys.readouterr().err


def test_a_judge_failure_names_the_partial_file_it_kept(tmp_path, monkeypatch, capsys):
    _fake_llm(monkeypatch)

    def judge(*a, **k):
        raise ValueError("judge broke")

    monkeypatch.setattr(arms_run, "_build_judge", lambda config: _FakeJudge())
    monkeypatch.setattr(arms_run, "_judge_records", judge)
    with refusal() as exc:
        _run(_arms_argv(tmp_path, "--judge", "--judge-provider", "openai", "--judge-model", "gpt-4o"))
    assert exc.code == 1
    partial = tmp_path / "arms.partial.json"
    assert f"judge broke (4 answered questions are kept in {partial})" in capsys.readouterr().err
    assert json.loads(partial.read_text(encoding="utf-8"))["partial"]["questions_recorded"] == 4


def test_a_matrix_cell_keeps_its_partial_file_until_its_save(tmp_path, monkeypatch, capsys):
    calls = _fake_llm(monkeypatch)
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [
        {"name": "c1", "model": "m", "provider": "openai", "harness": "arms-runner"},
        {"name": "c2", "model": "m", "provider": "openai", "harness": "arms-runner"},
    ]}), encoding="utf-8")
    real_record = arm_runner_mod.question_record

    def record(library_name, q, qid, *a, **k):
        if len(calls) > 10:  # c2's second question: c1 is saved whole, c2 has one question
            raise RuntimeError("boom")
        return real_record(library_name, q, qid, *a, **k)

    monkeypatch.setattr(arm_runner_mod, "question_record", record)
    out_dir = tmp_path / "out"
    with pytest.raises(RuntimeError, match="boom"):
        _run(["arms", "matrix-run", "--product", "onedal", "--questions", str(_questions(tmp_path)),
              "--arms", ARMS, "--matrix-cells", str(cells), "--concurrency", "1",
              "--out-dir", str(out_dir)])

    names = sorted(p.name for p in (out_dir / "cells").iterdir())
    assert names == ["c1.json", "c1.md", "c2.partial.json"]
    data = json.loads((out_dir / "cells" / "c2.partial.json").read_text(encoding="utf-8"))
    assert data["partial"] == {"questions_requested": 4, "questions_recorded": 1}
    assert data["matrix_cell"] == "c2"


def test_a_partial_file_that_cannot_be_written_stops_the_run_with_one_message(tmp_path, monkeypatch, capsys):
    # Three questions are in flight when the first save fails: none after them is asked, and the
    # two that finish after the failure add no second message.
    import threading

    from dokimi import artifacts

    in_flight = threading.Barrier(3, timeout=10)
    calls = []

    def fake(prompt, model, provider="openai", **kw):
        calls.append(prompt)
        in_flight.wait()
        return "answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def save(kind, data, path, **kw):
        raise PermissionError(13, "denied", str(path))

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    monkeypatch.setattr(artifacts, "save_artifact", save)
    argv = _arms_argv(tmp_path)
    argv[argv.index("--arms") + 1] = "baseline"
    argv[argv.index("--concurrency") + 1] = "3"
    with refusal() as exc:
        _run(argv)
    assert exc.code == 2
    assert len(calls) == 3
    assert capsys.readouterr().err.count(f"Error: cannot write {tmp_path / 'arms.partial.json'}") == 1


class _RetrievalArm(Treatment):
    """Context that differs per question, as a docs arm retrieves it."""

    name = "docs"
    arm_kind = "docs"

    def prepare(self, question_text, library_name, library_id=None) -> AgentConfig:
        return AgentConfig(injected_context=[{"content": f"doc about {question_text}", "source": "d"}])


def test_a_partial_save_leaves_the_run_records_as_they_were(monkeypatch):
    # Without ids both questions share the judge-context key, so after the second the material is
    # ambiguous and cited for neither; a partial save after the first must not leave it cited.
    from dokimi.eval.arms_run import ArmsRunConfig, PreparedArms

    questions = [{"question": "How do I do a?"}, {"question": "How do I do b?"}]
    config = ArmsRunConfig(product="p", concurrency=1)
    outputs = []
    for on_partial in (None, lambda out: None):
        _fake_llm(monkeypatch)
        runner = arm_runner_mod.ArmRunner([BaselineTreatment(), _RetrievalArm()])
        outputs.append(arms_run.run_prepared(PreparedArms(runner, None, None), config, questions, on_partial))
    without, with_saves = (_normalized(out) for out in outputs)
    assert all("judge_context_sha256" not in rec["arms"]["docs"] for rec in without["answers"])
    assert with_saves == without


def test_a_failure_before_the_first_question_is_done_names_no_partial_file(tmp_path, monkeypatch, capsys):
    _fake_llm(monkeypatch)

    def record(*a, **k):
        raise ValueError("bad record")

    monkeypatch.setattr(arm_runner_mod, "question_record", record)
    with refusal() as exc:
        _run(_arms_argv(tmp_path))
    assert exc.code == 1
    err = capsys.readouterr().err
    assert "Error running arms: bad record" in err and "kept in" not in err
    assert not (tmp_path / "arms.partial.json").exists()


def test_a_stopped_matrix_cell_is_saved_whole_and_its_message_names_no_partial_file(tmp_path, monkeypatch, capsys):
    # A usage limit (run-wide) on the third call: the cell is saved with its `terminated` block and
    # the matrix stops; the partial file is gone, so the message does not point at it.
    from dokimi import llm_cli

    calls = []

    def fake(prompt, model, provider="openai", **kw):
        calls.append(prompt)
        if len(calls) == 3:
            raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)
        return "answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [{"name": "c1", "model": "m", "provider": "openai",
                                            "harness": "arms-runner"}]}), encoding="utf-8")
    out_dir = tmp_path / "out"
    with refusal() as exc:
        _run(["arms", "matrix-run", "--product", "onedal", "--questions", str(_questions(tmp_path)),
              "--arms", ARMS, "--matrix-cells", str(cells), "--concurrency", "1", "--out-dir", str(out_dir)])
    assert exc.code == 1
    err = capsys.readouterr().err
    assert "Error running matrix cell 'c1': usage limit exceeded" in err and "kept in" not in err
    assert sorted(p.name for p in (out_dir / "cells").iterdir()) == ["c1.json", "c1.md"]
    assert json.loads((out_dir / "cells" / "c1.json").read_text(encoding="utf-8"))["terminated"]["questions_recorded"] == 2


def test_a_run_that_kept_nothing_leaves_an_earlier_runs_partial_file_alone(tmp_path, monkeypatch):
    # An empty question set saves no partial file, so the one an earlier, killed run left is its own.
    _fake_llm(monkeypatch)
    earlier = tmp_path / "arms.partial.json"
    earlier.write_text('{"partial": {"questions_requested": 9, "questions_recorded": 3}}', encoding="utf-8")
    argv = _arms_argv(tmp_path)
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    argv[argv.index("--questions") + 1] = str(empty)
    _run(argv)
    assert json.loads((tmp_path / "arms.json").read_text(encoding="utf-8"))["answers"] == []
    assert json.loads(earlier.read_text(encoding="utf-8"))["partial"]["questions_recorded"] == 3


def test_a_cell_named_like_another_cells_partial_file_keeps_its_finished_output(tmp_path, monkeypatch):
    # Cell `x.partial` finishes cells/x.partial.json before cell `x` runs: x's partial file is
    # named when x first saves, so it passes over that finished file instead of replacing it.
    _fake_llm(monkeypatch)
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [
        {"name": name, "model": "m", "provider": "openai", "harness": "arms-runner"} for name in ("x.partial", "x")
    ]}), encoding="utf-8")
    out_dir = tmp_path / "out"
    _run(["arms", "matrix-run", "--product", "onedal", "--questions", str(_questions(tmp_path)),
          "--arms", ARMS, "--matrix-cells", str(cells), "--concurrency", "1", "--out-dir", str(out_dir)])

    cells_dir = out_dir / "cells"
    assert sorted(p.name for p in cells_dir.iterdir()) == ["x.json", "x.md", "x.partial.json", "x.partial.md"]
    finished = json.loads((cells_dir / "x.partial.json").read_text(encoding="utf-8"))
    assert finished["matrix_cell"] == "x.partial" and "partial" not in finished
    assert len(finished["answers"]) == len(QUESTIONS)


def test_a_failed_final_save_names_the_partial_file_with_every_answer(tmp_path, monkeypatch, capsys):
    # --out is a directory: every question is answered and kept, then the whole run's save fails.
    _fake_llm(monkeypatch)
    out = tmp_path / "arms.json"
    out.mkdir()
    with refusal() as exc:
        _run(_arms_argv(tmp_path))
    assert exc.code == 2
    partial = tmp_path / "arms.partial.json"
    err = capsys.readouterr().err
    assert f"Error: cannot write {out}" in err
    assert f"(4 answered questions are kept in {partial})" in err
    assert json.loads(partial.read_text(encoding="utf-8"))["partial"]["questions_recorded"] == 4


def test_a_novelty_stop_record_is_not_refused_as_an_arms_partial_file():
    # novelty/analyze_partial.py writes its own `partial` block; records.read reads it as before.
    stop = {"schema_version": "novelty.v1",
            "partial": {"reason": "spend_cap", "stage": "measurement", "message": "m"}}
    assert len(records.read(stop, section="evaluations", required=False)) == 0


def test_a_partial_file_that_cannot_be_removed_only_warns(tmp_path, monkeypatch, capsys):
    # The run is saved whole, so a partial file that cannot be removed is a warning, not a failure.
    _fake_llm(monkeypatch)
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "arms.partial.json":
            raise PermissionError(13, "denied", str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    _run(_arms_argv(tmp_path))
    assert "partial" not in json.loads((tmp_path / "arms.json").read_text(encoding="utf-8"))
    assert (tmp_path / "arms.md").exists()
    assert f"Warning: could not remove {tmp_path / 'arms.partial.json'}" in capsys.readouterr().err
    assert (tmp_path / "arms.partial.json").exists()


def test_a_run_wide_stop_halts_the_other_questions_before_its_partial_save(monkeypatch):
    # q1's usage limit is saved while q2 is between its arms: q2 must see the stop at its next arm,
    # not after the save returns. The save waits until q2 has made that check.
    import threading

    from dokimi import llm_cli
    from dokimi.eval import arm_stop

    stopping, q2_checked, q2_asked = threading.Event(), threading.Event(), threading.Event()
    q2 = {"thread": None, "checks": 0, "calls": 0}
    real_halt = arm_stop.halt_if_stopped

    def fake(prompt, model, provider="openai", **kw):
        if "first" in prompt:  # once q2 is in its first arm, so it is between its arms when the stop lands
            assert q2_asked.wait(10)
            raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)
        q2["calls"] += 1
        q2_asked.set()
        assert stopping.wait(10)
        return "answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def halt(stop):
        if threading.current_thread() is q2["thread"]:
            q2["checks"] += 1
        try:
            real_halt(stop)
        finally:
            if q2["checks"] == 2:
                q2_checked.set()

    def record(done):
        if runner.stop_error is not None:
            stopping.set()
            assert q2_checked.wait(10)

    real_answer = arm_runner_mod.ArmRunner._answer_question

    def answer(self, library_name, library_id, q, stop=None):
        if q["id"] == "q2":
            q2["thread"] = threading.current_thread()
        return real_answer(self, library_name, library_id, q, stop)

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    monkeypatch.setattr(arm_runner_mod, "halt_if_stopped", halt)
    monkeypatch.setattr(arm_runner_mod.ArmRunner, "_answer_question", answer)
    runner = arm_runner_mod.ArmRunner([BaselineTreatment(), BaselineTreatment(name="other")])
    questions = [{"id": "q1", "question": "the first"}, {"id": "q2", "question": "the second"}]
    records = runner.run("lib", questions, concurrency=2, on_record=record)

    assert q2["calls"] == 1  # q2's second arm is never asked
    assert records[1]["arms"]["other"]["error"].startswith("not_asked")

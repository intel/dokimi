# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""--judge-temperature reaches the request and the output of every judge entry point."""

import json
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser

GOOD = ('{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}')

ANSWERS = [{"question_id": "Q1", "question_text": "q", "question": "q",
            "with_docs": {"answer": "with docs"}, "without_docs": {"answer": "without docs"}}]


@pytest.fixture
def requests(monkeypatch):
    import litellm

    sent = []

    def completion(**kw):
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))],
                               usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    return sent


def _answers(tmp_path):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(ANSWERS), encoding="utf-8")
    return str(path)


def _run(argv):
    args = build_parser().parse_args(argv)
    args.func(args)


def _exit_code(argv):
    """Run the CLI and return its exit code: None when it returns, else the SystemExit code."""
    try:
        _run(argv)
    except SystemExit as exc:
        return exc.code
    return None


@pytest.mark.parametrize("flag, expected", [(["--judge-temperature", "0"], 0.0), ([], None)])
def test_eval_score_forwards_and_records_judge_temperature(tmp_path, requests, flag, expected):
    out = tmp_path / "eval.json"
    _run(["eval", "score", "pointwise", "--product", "L", "--answers", _answers(tmp_path), "--output", str(out),
          "--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.x",
          "--concurrency", "1", *flag])
    assert len(requests) == 2
    assert [r.get("temperature") for r in requests] == [expected, expected]
    assert ("temperature" in requests[0]) is (expected is not None)
    assert json.loads(out.read_text())["judge_temperature"] == expected


def test_eval_panel_score_forwards_judge_temperature_to_every_judge(tmp_path, requests):
    out = tmp_path / "panel.json"
    _run(["eval", "score", "panel", "--product", "L", "--answers", _answers(tmp_path),
          "--output", str(out), "--provider", "amazon-bedrock", "--model", "us.anthropic.x",
          "--roles", "technical_expert,doc_reviewer", "--concurrency", "1",
          "--judge-temperature", "0"])
    assert len(requests) == 4
    assert all(r["temperature"] == 0.0 for r in requests)
    assert json.loads(out.read_text())["judge_temperature"] == 0.0


def test_eval_panel_score_judge_configs_also_take_judge_temperature(tmp_path, requests):
    out = tmp_path / "panel.json"
    _run(["eval", "score", "panel", "--product", "L", "--answers", _answers(tmp_path),
          "--output", str(out), "--judge-configs",
          "technical_expert:us.anthropic.x:amazon-bedrock", "--concurrency", "1",
          "--judge-temperature", "0"])
    assert requests and all(r["temperature"] == 0.0 for r in requests)


def test_evaluate_pipeline_forwards_and_records_judge_temperature(tmp_path, requests, monkeypatch):
    from dokimi.orchestrator import EvaluationPipeline

    def run(self, **kw):
        self._evaluate_answers(ANSWERS)
        raise RuntimeError("stop after the judge step")

    monkeypatch.setattr(EvaluationPipeline, "run", run)
    with pytest.raises(SystemExit):
        _run(["evaluate", "--product", "L", "--description", "d", "--output-dir", str(tmp_path),
              "--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.x",
              "--judge-temperature", "0"])
    assert len(requests) == 2 and all(r["temperature"] == 0.0 for r in requests)
    assert json.loads((tmp_path / "eval" / "L.json").read_text())["judge_temperature"] == 0.0


ARMS = {"answers": [{"question_id": f"Q{i}", "question_text": f"q{i}",
                     "arms": {"baseline": {"answer": f"base {i}"}, "skill": {"answer": f"skill {i}"}}}
                    for i in range(3)]}

PROBE = "Reply with OK."


@pytest.fixture
def routed(monkeypatch):
    """Records every request; replies so each command's parser accepts the reply."""
    import litellm

    from dokimi.eval.arms_run import probe_sampling

    probe_sampling.cache_clear()
    sent = []

    def completion(**kw):
        sent.append(kw)
        text = json.dumps(kw.get("messages"))
        if "only-t1" in kw["model"] and kw.get("temperature") not in (None, 1):
            raise ValueError("this model only supports temperature=1")
        reply = "A" if "Answer A:" in text else "YES" if "cover this" in text else GOOD
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                               usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    yield sent
    probe_sampling.cache_clear()


def _is_probe(request):
    return PROBE in json.dumps(request.get("messages"))


def _reliability_argv(command, tmp_path, out):
    from dokimi.eval.judge_wording import WORDINGS

    from tests.test_judge_validity import _answers as validity_answers

    arms = tmp_path / "run.json"
    arms.write_text(json.dumps(ARMS), encoding="utf-8")
    judge = ["--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.x"]
    if command == "pairwise":
        return ["eval", "score", "pairwise", "--answers", str(arms), *judge, "--concurrency", "1",
                "--output", str(out)]
    if command == "checklist":
        questions = tmp_path / "q.json"
        questions.write_text(json.dumps({"questions": [{"id": f"Q{i}", "expected_topics": ["p"]}
                                                       for i in range(3)]}), encoding="utf-8")
        return ["eval", "score", "checklist", "--answers", str(arms), "--questions", str(questions), *judge,
                "--concurrency", "1", "--output", str(out)]
    if command == "judge-wording":
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps(validity_answers()), encoding="utf-8")
        return ["eval", "judge", "wording", "--answers", str(answers), "--bases", "2",
                "--variants", "truncate_20", "--wordings", next(iter(WORDINGS)), "--redraws", "0",
                *judge, "--concurrency", "1", "--output", str(out)]
    if command == "judge-validity-pairwise":
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps(validity_answers()), encoding="utf-8")
        return ["eval", "judge", "validate", "--answers", str(answers), "--bases", "2",
                "--variants", "truncate_20", "--mode", "pairwise", *judge, "--concurrency", "1",
                "--output", str(out)]
    return ["eval", "judge", "agreement", "--answers", str(arms), "--no-stored",
            "--judges", "amazon-bedrock/us.anthropic.x", "--concurrency", "1", "--output", str(out)]


RELIABILITY_COMMANDS = ["pairwise", "checklist", "judge-wording", "cross-judge", "judge-validity-pairwise"]


@pytest.mark.parametrize("command", RELIABILITY_COMMANDS)
def test_judge_reliability_commands_forward_and_record_judge_temperature(tmp_path, routed, command):
    out = tmp_path / "out.json"
    assert _exit_code([*_reliability_argv(command, tmp_path, out), "--judge-temperature", "0"]) in (0, 1, None)
    judged = [r for r in routed if not _is_probe(r)]
    assert judged and all(r["temperature"] == 0.0 for r in judged)
    assert len(routed) - len(judged) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["judge_temperature"] == 0.0


@pytest.mark.parametrize("command", RELIABILITY_COMMANDS)
def test_judge_reliability_commands_send_no_sampling_without_the_flag(tmp_path, routed, command):
    out = tmp_path / "out.json"
    assert _exit_code(_reliability_argv(command, tmp_path, out)) in (0, 1, None)
    assert routed and not any(_is_probe(r) for r in routed)
    assert not any("temperature" in r or "seed" in r for r in routed)
    assert json.loads(out.read_text(encoding="utf-8"))["judge_temperature"] is None


@pytest.mark.parametrize("extra", [
    ["--escalate-model", "us.anthropic.only-t1"],
    ["--judges", "amazon-bedrock/us.anthropic.x,amazon-bedrock/us.anthropic.only-t1"],
], ids=["pairwise-escalation", "cross-judge"])
def test_a_judge_whose_sampling_probe_fails_exits_1_before_any_judgement(tmp_path, routed, capsys, extra):
    # The probe is one real call; its failure is a run failure (1), the same code arms run gives.
    command = "pairwise" if extra[0] == "--escalate-model" else "cross-judge"
    argv = _reliability_argv(command, tmp_path, tmp_path / "out.json")
    if command == "cross-judge":
        argv = argv[:argv.index("--judges")] + argv[argv.index("--judges") + 2:]
    with pytest.raises(SystemExit) as exc:
        _run([*argv, *extra, "--judge-temperature", "0"])
    assert exc.value.code == 1
    assert routed and all(_is_probe(r) for r in routed)
    assert "only-t1" in capsys.readouterr().err


def test_a_judge_the_provider_table_refuses_exits_2_with_no_call(tmp_path, routed, monkeypatch, capsys):
    # A refusal from the static table is an input refusal: no request is sent, so exit 2.
    monkeypatch.setattr("dokimi.llm.unsupported_sampling_params", lambda *a, **k: ["temperature"])
    with pytest.raises(SystemExit) as exc:
        _run([*_reliability_argv("cross-judge", tmp_path, tmp_path / "out.json"), "--judge-temperature", "0"])
    assert exc.value.code == 2
    assert routed == []
    assert "rejected by judge" in capsys.readouterr().err


def test_arms_run_judge_whose_sampling_probe_fails_exits_1_before_any_answer(tmp_path, routed, monkeypatch, capsys):
    # Parity with the eval commands above: the same real probe failure, through arms run, is 1.
    from tests.test_exit_codes_boundaries import _questions

    answered = []
    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run",
                        lambda self, *a, **k: answered.append(1) or [])
    with pytest.raises(SystemExit) as exc:
        _run(["arms", "run", "--product", "onedal", "--questions", _questions(tmp_path),
              "--arms", "baseline", "--out-json", str(tmp_path / "a.json"), "--out-md", str(tmp_path / "a.md"),
              "--judge", "--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.only-t1",
              "--judge-temperature", "0"])
    assert exc.value.code == 1
    assert answered == []
    assert routed and all(_is_probe(r) for r in routed)
    assert "only-t1" in capsys.readouterr().err


@pytest.fixture
def client_refused(monkeypatch):
    """litellm refuses temperature != 1 for a 'temp1-only' model itself, before any request."""
    import litellm

    from dokimi.eval.arms_run import probe_sampling

    probe_sampling.cache_clear()
    sent = []

    def completion(**kw):
        if "temp1-only" in kw["model"] and kw.get("temperature") not in (None, 1):
            raise litellm.UnsupportedParamsError("temp1-only does not support temperature=0.0. "
                                                 "Only temperature=1 is supported", llm_provider="anthropic")
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))], usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    yield sent
    probe_sampling.cache_clear()


def test_a_judge_temperature_litellm_refuses_before_any_call_exits_2(tmp_path, client_refused, capsys):
    # litellm's own parameter table refuses the value without sending a request: an input refusal.
    argv = _reliability_argv("pairwise", tmp_path, tmp_path / "out.json")
    argv = argv[:argv.index("--judge-model") + 1] + ["us.anthropic.temp1-only"] + argv[argv.index("--judge-model") + 2:]
    assert _exit_code([*argv, "--judge-temperature", "0"]) == 2
    assert client_refused == []
    assert not (tmp_path / "out.json").exists()  # refused before judging, not after every judgement failed
    assert "rejected by judge" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [
    ["--judge", "--judge-provider", "anthropic", "--judge-model", "temp1-only", "--judge-temperature", "0"],
    ["--provider", "anthropic", "--model", "temp1-only", "--temperature", "0"],
], ids=["judge", "answer"])
def test_arms_run_sampling_litellm_refuses_before_any_call_exits_2(tmp_path, client_refused, capsys, flags):
    from tests.test_exit_codes_boundaries import _questions

    assert _exit_code(["arms", "run", "--product", "onedal", "--questions", _questions(tmp_path),
                       "--arms", "baseline", "--out-json", str(tmp_path / "a.json"),
                       "--out-md", str(tmp_path / "a.md"), *flags]) == 2
    assert client_refused == []
    assert "Error building arms/plugins" in capsys.readouterr().err

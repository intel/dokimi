# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Codex and Copilot CLIs as answering providers, and the report of a one-condition (knowledge) run.

The CLI replies are hand-written JSONL and text fixtures in the shape the CLIs print
(``codex exec --json`` event names, ``copilot -p -s`` text); no model is called.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from dokimi import llm_cli
from dokimi.eval.diagnoser import DIAGNOSIS_LABELS, INSUFFICIENT_DATA, SINGLE_CONDITION, diagnose
from dokimi.process import FakeRunner, reply
from dokimi.records import is_single_condition, knowledge_only
from dokimi.records import records as to_records
from dokimi.report.single_condition_report import write_eval_report

CODEX_OK = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "t1"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "thinking"}}),
    json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "PONG"}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000,
                                                     "output_tokens": 7, "reasoning_output_tokens": 3}}),
]) + "\n"
COPILOT_OK = "PONG\n"


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(llm_cli, "cli_version", lambda provider: "test-version")
    return tmp_path


@pytest.fixture
def run(monkeypatch, cache):
    """Every CLI call answers from ``run.answers`` (a list, consumed in order)."""
    fake = FakeRunner(lambda call: fake.answers.pop(0))
    fake.answers = []  # type: ignore[attr-defined]
    monkeypatch.setattr(llm_cli.process, "run", fake)
    return fake


def _env_of(call) -> dict:
    return call.env


# ── codex-cli ────────────────────────────────────────────────────────────────


def test_codex_runs_read_only_with_no_user_config_and_the_prompt_on_stdin(run, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("CODEX_THREAD_ID", "outer-session")
    monkeypatch.setenv("CODEX_HOME", "/home/me/.codex")
    run.answers.append(reply(CODEX_OK))
    out = llm_cli.call("codex-cli", model="gpt-5.5", prompt="What is parallel_for?", system="Be terse.")
    argv = run.calls[0].argv
    assert argv[:2] == ("codex", "exec")
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--json"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == "gpt-5.5"
    assert "project_doc_max_bytes=0" in argv  # no AGENTS.md from the work dir
    assert argv[-1] == "-"
    assert run.calls[0].input == b"What is parallel_for?"  # the system text goes by file, not stdin
    env = _env_of(run.calls[0])
    assert "OPENAI_API_KEY" not in env and "CODEX_THREAD_ID" not in env
    assert env.get("CODEX_HOME") == "/home/me/.codex"  # the login stays reachable
    assert out.text == "PONG"


def test_codex_system_text_goes_by_file_as_base_instructions(run):
    seen = []

    def answer(call):
        override = [a for a in call.argv if a.startswith("model_instructions_file=")]
        assert len(override) == 1
        path = Path(json.loads(override[0].split("=", 1)[1]))
        seen.append((path.name, path.read_text()))
        return reply(CODEX_OK)

    run.handler = answer
    llm_cli.call("codex-cli", model="gpt-5.5", prompt="q", system="Only this.")
    assert seen == [("system-prompt.txt", "Only this.")]  # the call's temp file, removed after the call


@pytest.mark.parametrize("text", ["", "   ", None])
def test_codex_blank_agent_message_is_a_failed_call_not_an_answer(run, text):
    blank = "\n".join([
        json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": text}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 0}}),
    ])
    run.answers.append(reply(blank))
    with pytest.raises(llm_cli.CliCallError, match="no reply text") as info:
        llm_cli.call("codex-cli", model="gpt-5.5", prompt="q")
    assert info.value.retryable is False


def test_codex_runs_in_its_fixed_empty_work_dir_outside_any_repository(run, cache):
    run.answers.append(reply(CODEX_OK))
    llm_cli.call("codex-cli", model="gpt-5.5", prompt="q")
    cwd = run.calls[0].cwd
    assert cwd == cache / "cache" / "dokimi" / "cli" / "codex-cli"
    assert run.calls[0].env.get("GIT_CEILING_DIRECTORIES") == str(cwd.parent)


def test_codex_reads_text_tokens_and_usage_from_the_event_stream(run):
    run.answers.append(reply(CODEX_OK))
    out = llm_cli.call("codex-cli", model="gpt-5.5", prompt="q")
    u = out.usage
    assert out.text == "PONG"
    assert (u.prompt_tokens, u.completion_tokens, u.total_tokens) == (1200, 7, 1207)
    assert u.cache_read_tokens == 1000
    assert u.cost_usd is None and out.list_cost_usd is None  # a login is not billed per call
    assert (u.provider, u.model) == ("codex-cli", "gpt-5.5")


def test_codex_usage_limit_is_run_wide_and_never_retried(run):
    failed = json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}})
    run.answers.append(reply(failed + "\n", returncode=1))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("codex-cli", model="gpt-5.5", prompt="q")
    assert info.value.run_wide and not info.value.retryable


def test_codex_rate_limit_is_retried_and_a_missing_reply_is_not(run):
    run.answers.append(reply(json.dumps({"type": "turn.failed", "error": {"message": "429 rate limit exceeded"}}),
                             returncode=1))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("codex-cli", model="gpt-5.5", prompt="q")
    assert info.value.retryable and not info.value.run_wide
    run.answers.append(reply(json.dumps({"type": "turn.started"}) + "\n"))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("codex-cli", model="gpt-5.5", prompt="q")
    assert not info.value.retryable  # rc 0 with no agent message: nothing to retry into


def test_codex_timeout_is_retryable(run):
    run.answers.append(reply("", timed_out=True))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("codex-cli", model="gpt-5.5", prompt="q", timeout=5)
    assert info.value.retryable


def test_codex_detect_reads_the_login_status_exit_code(monkeypatch):
    def runner_for(rc: int, text: str):
        def fake(argv, **_):
            if argv[-1] == "--version":
                return reply("codex-cli 0.166.0\n")
            return reply(text, returncode=rc)
        return fake

    signed_in = llm_cli.detect("codex-cli", runner=runner_for(0, "Logged in using ChatGPT\n"))
    assert (signed_in.installed, signed_in.authenticated) == (True, True)
    assert signed_in.method == "Logged in using ChatGPT"
    out = llm_cli.detect("codex-cli", runner=runner_for(1, "Not logged in\n"))
    assert out.authenticated is False and out.hint == "codex login"


def test_codex_model_names_are_the_cli_catalogues_to_pass_on():
    assert llm_cli.accepts_model("codex-cli", "gpt-5.5")
    assert llm_cli.accepts_model("copilot-cli", "claude-sonnet-4.5")
    assert not llm_cli.accepts_model("codex-cli", "  ")
    assert llm_cli.accepts_model("claude-cli", "sonnet")
    assert not llm_cli.accepts_model("claude-cli", "gpt-5.5")


# ── copilot-cli ──────────────────────────────────────────────────────────────


def test_copilot_runs_with_no_tools_no_custom_instructions_and_the_prompt_in_argv(run, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_should_not_leak")
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "https://byok.example")
    monkeypatch.setenv("COPILOT_HOME", "/home/me/.copilot")
    run.answers.append(reply(COPILOT_OK))
    out = llm_cli.call("copilot-cli", model="gpt-5", prompt="What is parallel_for?", system="Be terse.")
    call = run.calls[0]
    argv = call.argv
    assert argv[:2] == ("copilot", "-p") and argv[2] == "Be terse.\n\nWhat is parallel_for?"
    assert call.input is None  # Copilot reads no prompt from stdin
    for flag in ("--no-custom-instructions", "--disable-builtin-mcps", "--no-remote", "--no-auto-update",
                 "--disallow-temp-dir", "-s"):
        assert flag in argv
    assert argv[argv.index("--available-tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "gpt-5"
    env = _env_of(call)
    assert "GH_TOKEN" not in env and "COPILOT_PROVIDER_BASE_URL" not in env
    assert env.get("COPILOT_HOME") == "/home/me/.copilot"
    assert out.text == "PONG" and out.usage.cost_usd is None


def test_copilot_refuses_a_prompt_too_long_for_one_argument_before_any_call(run):
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("copilot-cli", model="gpt-5", prompt="x" * 200_000)
    assert not info.value.retryable
    assert run.calls == []


def test_copilot_error_text_is_run_wide_on_no_login_and_retry_on_rate_limit(run):
    run.answers.append(reply("", stderr="error: not logged in, run copilot login\n", returncode=1))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("copilot-cli", model="gpt-5", prompt="q")
    assert info.value.run_wide and not info.value.retryable
    run.answers.append(reply("", stderr="HTTP 429: rate limit\n", returncode=1))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("copilot-cli", model="gpt-5", prompt="q")
    assert info.value.retryable


def test_copilot_has_no_status_command_so_detect_cannot_say_signed_in(monkeypatch):
    status = llm_cli.detect("copilot-cli", runner=lambda argv, **_: reply("GitHub Copilot CLI 1.0.91\n"))
    assert status.installed is True and status.authenticated is None


# ── real process, fake CLI binaries on PATH ───────────────────────────────────

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="fake CLIs are POSIX shell scripts")


def _fake_cli(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@POSIX_ONLY
def test_codex_call_through_a_fake_binary_on_path(cache, monkeypatch):
    bin_dir = cache / "bin"
    seen = cache / "argv.txt"
    _fake_cli(bin_dir, "codex", f'cat > /dev/null\nprintf "%s\\n" "$@" > "{seen}"\ncat <<\'EOF\'\n'
              + CODEX_OK + "EOF\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    out = llm_cli.call("codex-cli", model="gpt-5.5", prompt="ping", timeout=30)
    assert out.text == "PONG"
    assert "--ignore-user-config" in seen.read_text(encoding="utf-8").splitlines()


@POSIX_ONLY
def test_copilot_call_through_a_fake_binary_on_path(cache, monkeypatch):
    bin_dir = cache / "bin"
    _fake_cli(bin_dir, "copilot", "printf 'PONG\\n'\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    out = llm_cli.call("copilot-cli", model="gpt-5", prompt="ping", timeout=30)
    assert out.text == "PONG"


@POSIX_ONLY
def test_a_fake_codex_that_hangs_times_out_as_a_retryable_error(cache, monkeypatch):
    bin_dir = cache / "bin"
    _fake_cli(bin_dir, "codex", "sleep 30\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("codex-cli", model="gpt-5.5", prompt="ping", timeout=1)
    assert info.value.retryable


# ── one-condition (knowledge) runs: the diagnosis and the report ──────────────


def _answers_record(qid: str, *, baseline: bool = True, error: str | None = None) -> dict:
    rec = {"question_id": qid, "question_text": f"Question {qid}?", "with_docs": None,
           "without_docs": {"answer": "an answer", "model": "m", "token_usage": {}} if baseline else None}
    if error:
        rec["error"] = error
    return rec


def test_a_knowledge_question_is_one_condition_and_an_errored_one_is_not():
    (knowledge,) = to_records([_answers_record("k1")], kind="answers")
    (errored,) = to_records([_answers_record("e1", baseline=False, error="boom")], kind="answers")
    assert knowledge_only(knowledge) and not knowledge_only(errored)
    assert is_single_condition([knowledge]) and not is_single_condition([errored])


def test_a_run_with_a_context_answer_is_not_one_condition():
    paired = {"question_id": "p1", "with_docs": {"answer": "x", "retrieved_docs": [], "doc_source": "context7"},
              "without_docs": {"answer": "y"}}
    recs = to_records([paired], kind="answers")
    assert not is_single_condition(recs) and not knowledge_only(recs[0])


def test_a_knowledge_question_has_single_condition_diagnosis_not_insufficient_data():
    result = diagnose(None, None, context_scored=False, single_condition=True)
    assert result["label"] == SINGLE_CONDITION
    assert SINGLE_CONDITION in DIAGNOSIS_LABELS
    # A paired question still missing its context answer keeps the old label.
    assert diagnose(None, None, context_scored=False)["label"] == INSUFFICIENT_DATA


def _eval_doc(scores: dict[str, float | None], *, with_context: bool = False) -> dict:
    evaluations = []
    for qid, score in scores.items():
        item = {"question_id": qid, "question_text": f"Question {qid}?", "difficulty": "easy",
                "with_docs": None, "delta": None,
                "without_docs": None if score is None else {"answer": "an answer", "aggregate": score}}
        if with_context:
            item["with_docs"] = {"answer": "ctx", "aggregate": 50.0}
            item["delta"] = (score or 0) - 50.0
        evaluations.append(item)
    return {"judge_model": "judge", "run_metadata": {"answer_model": "m", "answer_provider": "p"},
            "evaluations": evaluations}


def test_report_eval_on_a_knowledge_run_shows_one_condition_table(tmp_path):
    eval_path = tmp_path / "eval" / "demo.json"
    eval_path.parent.mkdir()
    eval_path.write_text(json.dumps(_eval_doc({"q1": 80.0, "q2": 40.0, "q3": None})), encoding="utf-8")
    out = tmp_path / "report" / "demo.md"
    write_eval_report(str(eval_path), str(out))
    text = out.read_text(encoding="utf-8")
    assert "No complete pairs" not in text and "Baseline" not in text.split("## Summary")[1].splitlines()[1]
    assert "| `without_docs` (knowledge) | 3 | 2 | 60.0 | 40.0 | 80.0 |" in text
    assert "## Lowest-scoring questions" in text and "| q2 |" in text.split("## Lowest-scoring")[1]
    assert "1 question(s) have no score" in text
    assert "no delta" in text.lower()


def test_report_eval_on_a_paired_run_still_uses_the_paired_report(tmp_path):
    eval_path = tmp_path / "eval" / "paired.json"
    eval_path.parent.mkdir()
    eval_path.write_text(json.dumps(_eval_doc({"q1": 80.0}, with_context=True)), encoding="utf-8")
    out = tmp_path / "report" / "paired.md"
    write_eval_report(str(eval_path), str(out))
    text = out.read_text(encoding="utf-8")
    assert "## Lowest-scoring questions" not in text and "## Summary" in text

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for dokimi.llm_cli: a logged-in coding-agent CLI as an LLM provider.

The payloads are trimmed from real ``claude -p --output-format json`` replies
(Claude Code 2.1.3xx): a success, an unknown model, a failed login.
"""

from __future__ import annotations

import argparse
import json

import pytest

from dokimi import llm, llm_cli, process
from dokimi.process import FakeRunner, reply


def _ok(text: str = "PONG", **over) -> str:
    payload = {
        "type": "result", "is_error": False, "result": text, "stop_reason": "end_turn",
        "total_cost_usd": 0.005394,
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1340,
                  "cache_read_input_tokens": 10, "output_tokens": 3},
        "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.005394, "costBasis": "list"}},
    }
    payload.update(over)
    return json.dumps(payload)


def _err(result: str, status) -> str:
    return json.dumps({"type": "result", "is_error": True, "result": result,
                       "api_error_status": status, "usage": {}, "modelUsage": {}})


UNKNOWN_MODEL = _err("There's an issue with the selected model (claude-nope-1). "
                     "It may not exist or you may not have access to it.", 404)
NO_LOGIN = _err("Authentication error · This may be a temporary network issue, please try again", None)


@pytest.fixture
def runner(monkeypatch, tmp_path):
    """Every CLI call answers from ``runner.answers`` (a list, consumed in order)."""
    monkeypatch.setattr(llm_cli.tempfile, "gettempdir", lambda: str(tmp_path))
    fake = FakeRunner(lambda call: fake.answers.pop(0))
    fake.answers = []  # type: ignore[attr-defined]
    monkeypatch.setattr(llm_cli.process, "run", fake)
    return fake


def test_a_reply_reads_text_tokens_and_model_and_leaves_cost_unknown(runner):
    runner.answers.append(reply(_ok()))
    out = llm_cli.call("claude-cli", model="sonnet", prompt="ping", system="Be terse.")
    assert out.text == "PONG"
    assert out.model_id == "claude-sonnet-5-5"
    u = out.usage
    assert (u.prompt_tokens, u.completion_tokens, u.total_tokens) == (1352, 3, 1355)
    assert (u.cache_write_tokens, u.cache_read_tokens) == (1340, 10)
    # A subscription is not billed per call: the CLI's figure is list price, kept apart.
    assert u.cost_usd is None and u.cost_known_calls == 0
    assert out.list_cost_usd == pytest.approx(0.005394)
    assert (u.model, u.provider) == ("sonnet", "claude-cli")


def test_the_call_is_isolated_and_the_prompt_goes_on_stdin(runner, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("DOKIMI_TEST_KEEP", "kept")
    runner.answers.extend([reply(_ok()), reply(_ok())])
    llm_cli.call("claude-cli", model="haiku", prompt="x" * 300_000, system="Grade it.")
    llm_cli.call("claude-cli", model="haiku", prompt="again")
    first, second = runner.calls
    argv = list(first.argv)
    assert argv[:2] == ["claude", "-p"] and "--bare" not in argv
    for flag in ("--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == "Grade it."
    assert first.input == b"x" * 300_000 and "x" * 100 not in " ".join(argv)
    assert "CLAUDECODE" not in first.env and "CLAUDE_CODE_SESSION_ID" not in first.env
    assert first.env["DOKIMI_TEST_KEEP"] == "kept"
    # One fixed, empty directory: the cwd the CLI states is the same in every call.
    assert first.cwd == second.cwd and list(first.cwd.iterdir()) == []
    # No system prompt is still a system prompt: never the CLI's own agent prompt.
    assert "--system-prompt" in second.argv


@pytest.mark.parametrize("answer, retryable, words", [
    (reply(UNKNOWN_MODEL, returncode=1), False, "selected model"),
    (reply(NO_LOGIN, returncode=1), False, "setup-token"),
    (reply(_err("Rate limit reached", 429), returncode=1), True, "Rate limit"),
    (reply(_err("Overloaded", 529), returncode=1), True, "Overloaded"),
    (reply("", timed_out=True), True, "no reply"),
    (reply("segfault", stderr="boom", returncode=139), False, "no JSON reply"),
])
def test_a_failed_call_says_whether_another_attempt_can_help(runner, answer, retryable, words):
    runner.answers.append(answer)
    with pytest.raises(llm_cli.CliCallError, match=words) as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is retryable
    assert llm.is_retryable(info.value) is retryable


def test_a_missing_binary_is_a_permanent_error(monkeypatch):
    def missing(call):
        raise FileNotFoundError("claude")
    monkeypatch.setattr(llm_cli.process, "run", FakeRunner(missing))
    with pytest.raises(llm_cli.CliCallError, match="not installed") as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is False


def test_llm_routes_a_cli_provider_retries_it_and_meters_it(runner):
    runner.answers.extend([reply(_err("Rate limit reached", 429), returncode=1), reply(_ok("4"))])
    model = llm.LLM("sonnet", provider="claude-cli", max_tokens=50, retry_delay=0)
    out = model.complete("2+2?", system="Answer with a number.")
    assert out.text == "4" and len(runner.calls) == 2
    assert runner.calls[1].input == b"2+2?"
    assert model.metrics().completion_tokens == 3 and model.metrics().cost_usd is None


def test_tools_or_a_conversation_are_refused_before_any_call(runner):
    model = llm.LLM("sonnet", provider="claude-cli")
    with pytest.raises(llm_cli.CliCallError, match="one prompt"):
        model.chat([{"role": "user", "content": "hi"}],
                   tools=[{"type": "function", "function": {"name": "f"}}])
    with pytest.raises(llm_cli.CliCallError, match="one prompt"):
        model.chat([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                    {"role": "user", "content": "c"}])
    assert runner.calls == []


def test_sampling_the_cli_cannot_honour_is_named():
    assert llm.unsupported_sampling_params("sonnet", "claude-cli", temperature=0.0, seed=7) == [
        "temperature", "seed"]
    assert llm.unsupported_sampling_params("sonnet", "claude-cli") == []


def test_judge_and_get_llm_reach_the_cli_backend(runner):
    from dokimi.eval.judge import JUDGE_PROVIDERS, Judge
    from dokimi.utils import get_llm

    assert "claude-cli" in JUDGE_PROVIDERS and "claude-cli" in llm.ANSWER_PROVIDERS
    runner.answers.extend([reply(_ok("judged")), reply(_ok("generated"))])
    assert Judge(model="sonnet", provider="claude-cli").llm.invoke("grade").content == "judged"
    resp = get_llm("claude-cli", "haiku").invoke("write")
    assert resp.content == "generated" and resp.usage.provider == "claude-cli"


def _auth_runner(status: dict | None, version: str = "2.1.304 (Claude Code)"):
    def handle(call):
        if call.argv[-1] == "--version":
            if status is None:
                raise FileNotFoundError("claude")
            return reply(version + "\n")
        return reply(json.dumps(status))
    return FakeRunner(handle)


def test_detect_reports_install_login_and_method_without_a_model_call():
    fake = _auth_runner({"loggedIn": True, "authMethod": "oauth_token"})
    got = llm_cli.detect("claude-cli", runner=fake)
    assert (got.installed, got.authenticated, got.method, got.version) == (
        True, True, "oauth_token", "2.1.304 (Claude Code)")
    assert got.hint == "" and [c.argv[1:] for c in fake.calls] == [("--version",), ("auth", "status")]
    out = llm_cli.detect("claude-cli", runner=_auth_runner({"loggedIn": False}))
    assert out.authenticated is False and "setup-token" in out.hint
    gone = llm_cli.detect("claude-cli", runner=_auth_runner(None))
    assert gone.installed is False and gone.authenticated is None


def test_backends_command_prints_one_row_per_backend(monkeypatch, capsys):
    from dokimi.commands.backends import cmd_backends

    monkeypatch.setattr(process, "run", _auth_runner(
        {"loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock"}))
    cmd_backends(argparse.Namespace(json=False))
    text = capsys.readouterr().out
    assert "| claude-cli | claude | 2.1.304 (Claude Code) | yes | third_party (bedrock) |" in text
    cmd_backends(argparse.Namespace(json=True))
    (row,) = json.loads(capsys.readouterr().out)
    assert row["backend"] == "claude-cli" and row["authenticated"] is True


def test_benchmark_preflight_checks_the_login_not_a_key(monkeypatch):
    from dokimi.commands.benchmark import _credential_error

    monkeypatch.setattr(process, "run", _auth_runner({"loggedIn": False}))
    assert "not signed in" in _credential_error("claude-cli")
    monkeypatch.setattr(process, "run", _auth_runner({"loggedIn": True}))
    assert _credential_error("claude-cli") is None
    monkeypatch.setattr(process, "run", _auth_runner(None))
    assert "not installed" in _credential_error("claude-cli")


def test_the_default_judge_runs_on_the_cli():
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, default_judge_model

    assert default_judge_model("claude-cli") == DEFAULT_JUDGE_MODEL

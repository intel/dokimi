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
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
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
    for name in llm_cli.CLAUDE_ENV_DROP:
        monkeypatch.setenv(name, "parent")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill")
    monkeypatch.setenv("DOKIMI_TEST_KEEP", "kept")
    runner.answers.extend([reply(_ok()), reply(_ok())])
    llm_cli.call("claude-cli", model="haiku", prompt="x" * 300_000, system="Grade it.")
    llm_cli.call("claude-cli", model="haiku", prompt="again")
    first, second = runner.calls
    argv = list(first.argv)
    assert argv[:2] == ["claude", "-p"] and "--bare" not in argv
    for flag in ("--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == "Grade it."
    assert first.input == b"x" * 300_000 and "x" * 100 not in " ".join(argv)
    assert not llm_cli.CLAUDE_ENV_DROP & set(first.env)
    assert {"CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID"} <= llm_cli.CLAUDE_ENV_DROP
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


# The status and word lists are written out here, not read from llm_cli: dropping a
# member from the module must fail a test, not drop the test with it.
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_each_retryable_status_is_retried_whatever_the_message(runner, status):
    runner.answers.append(reply(_err("something went wrong", status), returncode=1))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is True


@pytest.mark.parametrize("word", ["rate limit", "rate_limit", "overloaded", "timed out"])
def test_each_retryable_word_is_retried_without_a_status(runner, word):
    runner.answers.append(reply(_err(f"API said: {word}", None), returncode=1))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is True


@pytest.mark.parametrize("status", [None, 429])
def test_a_subscription_usage_limit_is_not_retried(runner, status):
    # It resets after hours: three more CLI starts would only delay the same failure.
    runner.answers.append(reply(_err("Claude AI usage limit reached|1761660000", status), returncode=1))
    with pytest.raises(llm_cli.CliCallError, match="usage limit") as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is False


@pytest.mark.parametrize("answer", [
    reply(_ok("bad", is_error=True), returncode=0),  # is_error alone, exit 0
    reply(_ok("boom"), returncode=1),                # exit 1 alone, is_error false
])
def test_is_error_and_the_exit_code_each_mark_a_failure(runner, answer):
    runner.answers.append(answer)
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is False


def test_an_unusable_work_dir_is_a_permanent_cli_error(runner, monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    with pytest.raises(llm_cli.CliCallError, match="work dir") as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert info.value.retryable is False and runner.calls == []


def test_a_cli_provider_never_reads_an_api_key_file(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", f"file:{tmp_path / 'missing'}")
    assert llm.resolve_api_key("claude-cli") is None
    runner.answers.append(reply(_ok("4")))
    assert llm.LLM("sonnet", provider="claude-cli", max_tokens=50).complete("2+2?").text == "4"


def test_a_missing_binary_is_a_permanent_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

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
    from dokimi.eval.judge import Judge
    from dokimi.eval.panel import JudgePanel
    for sampling in ({"temperature": 0.0}, {"seed": 7}):  # --judge-temperature / --seed: before the run
        with pytest.raises(ValueError, match="temperature or seed"):
            Judge(model="sonnet", provider="claude-cli", **sampling)
        with pytest.raises(ValueError, match="temperature or seed"):  # eval panel-score
            JudgePanel(default_provider="claude-cli", **sampling)
    JudgePanel(default_provider="claude-cli")
    with pytest.raises(llm_cli.CliCallError, match="temperature"):
        llm.LLM("sonnet", provider="claude-cli", seed=1).complete("x")
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
    from dokimi.commands.benchmark import _provider_checks

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # no key anywhere: the CLI login is what counts
    monkeypatch.setattr(process, "run", _auth_runner({"loggedIn": False}))
    errors, _ = _provider_checks(["claude-cli"])
    assert any("not signed in" in e for e in errors)
    monkeypatch.setattr(process, "run", _auth_runner({"loggedIn": True}))
    assert _provider_checks(["claude-cli"]) == ([], [])
    monkeypatch.setattr(process, "run", _auth_runner(None))
    errors, _ = _provider_checks(["claude-cli"])
    assert any("not installed" in e for e in errors)
    monkeypatch.setattr(process, "run", _auth_runner({"loggedIn": True}))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    errors, _ = _provider_checks(["openai"])
    assert any("missing OPENAI_API_KEY" in e for e in errors)


def test_a_judge_panel_refuses_sampling_on_the_cli_with_explicit_judges():
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    with pytest.raises(ValueError, match="takes no temperature"):
        JudgePanel(judges=[JudgeConfig(role="a", model="sonnet", provider="claude-cli", temperature=0.0)])


def test_an_unanswered_login_check_is_a_warning_not_a_pass(monkeypatch):
    from dokimi.commands.benchmark import _login_warning, _provider_checks

    monkeypatch.setattr(process, "run", _auth_runner("no answer"))
    assert _provider_checks(["claude-cli"]) == ([], [
        "claude-cli: could not check the login ('claude auth status' gave no answer)"])
    assert "could not check the login" in _login_warning("claude-cli")
    monkeypatch.setattr(process, "run", _auth_runner({"loggedIn": True}))
    assert _login_warning("claude-cli") is None


def test_the_default_judge_runs_on_the_cli():
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, default_judge_model

    assert default_judge_model("claude-cli") == DEFAULT_JUDGE_MODEL


def test_a_cli_provider_needs_a_claude_model_on_the_command_line():
    from dokimi.cli import parse_args

    base = ["answers", "generate", "--product", "p", "--questions", "q.json", "--provider", "claude-cli"]
    with pytest.raises(SystemExit):
        parse_args(base)  # the default answer model is not a Claude model
    assert parse_args([*base, "--model", "haiku"]).model == "haiku"
    assert parse_args([*base, "--model", "claude-sonnet-5-5"]).model == "claude-sonnet-5-5"


def test_answerer_and_generators_accept_the_cli_provider(runner):
    from dokimi.eval.answerer import Answerer
    from dokimi.personas.generator import PersonaGenerator
    from dokimi.questions.llm_gen import QuestionGenerator

    Answerer(model="haiku", provider="claude-cli")
    runner.answers.extend([reply(_ok("persona")), reply(_ok("question"))])
    assert PersonaGenerator(model="haiku", provider="claude-cli").llm.invoke("p").content == "persona"
    assert QuestionGenerator(model="haiku", provider="claude-cli").llm.invoke("q").content == "question"


def test_the_child_env_drops_billing_and_session_variables_by_prefix(runner, monkeypatch):
    # Named in review: an endpoint, a model alias override and a provider switch each
    # change who is billed; a messaging socket joins the parent's session.
    for name, value in {
        "ANTHROPIC_BASE_URL": "https://gateway.example", "ANTHROPIC_DEFAULT_SONNET_MODEL": "other",
        "ANTHROPIC_CUSTOM_HEADERS": "x: y", "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/sock", "CLAUDE_CODE_SSE_PORT": "1234",
        "CLAUDE_CODE_OAUTH_TOKEN": "headless-token", "CLAUDE_CONFIG_DIR": "/accounts/work",
    }.items():
        monkeypatch.setenv(name, value)
    runner.answers.append(reply(_ok()))
    llm_cli.call("claude-cli", model="sonnet", prompt="p")
    env = runner.calls[0].env
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_CUSTOM_HEADERS",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_SSE_PORT"):
        assert name not in env, name
    # The login is kept: the headless token and the account's config dir.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "headless-token"
    assert env["CLAUDE_CONFIG_DIR"] == "/accounts/work"


def test_the_work_dir_is_under_the_users_cache_not_the_shared_temp_dir(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "shared-tmp"))
    path = llm_cli.work_dir("claude-cli")
    assert path == tmp_path / "cache" / "dokimi" / "cli" / "claude-cli" and path.is_dir()
    assert not (tmp_path / "shared-tmp").exists()


def test_a_reply_names_the_main_model_the_cli_resolved(runner):
    # modelUsage lists a helper model too; the main one has the output tokens.
    payload = json.loads(_ok(modelUsage={"claude-haiku-5-5": {"outputTokens": 9},
                                         "claude-sonnet-5-5": {"outputTokens": 3}}))
    runner.answers.append(reply(json.dumps(payload)))
    out = llm_cli.call("claude-cli", model="sonnet", prompt="p")
    assert out.model_id == "claude-sonnet-5-5"


def test_the_llm_reply_carries_the_resolved_model_id(runner):
    runner.answers.append(reply(_ok()))
    request = llm.Request(model="sonnet", provider="claude-cli", litellm_model="sonnet",
                          messages=[{"role": "user", "content": "ping"}], max_tokens=50)
    assert llm._cli_reply(request).model_id == "claude-sonnet-5-5"


def test_an_alias_and_its_full_id_are_one_model_for_the_identity_checks():
    same = llm_cli.same_model
    assert same("claude-cli", "sonnet", "claude-cli", "claude-sonnet-5-5")
    assert same("anthropic", "claude-sonnet-5-5", "claude-cli", "sonnet")
    assert not same("claude-cli", "sonnet", "claude-cli", "claude-opus-5-5")
    assert not same("claude-cli", "claude-sonnet-5-5", "claude-cli", "claude-sonnet-4-5")
    assert not same("openai", "gpt-6.1-sol", "claude-cli", "sonnet")


def test_the_independence_warning_fires_for_an_alias_against_its_full_id(capsys):
    from dokimi.commands.evaluate import _warn_judge_independence

    assert _warn_judge_independence("claude-cli", "sonnet", "claude-cli", "claude-sonnet-5-5")
    assert "independence warning" in capsys.readouterr().err
    assert not _warn_judge_independence("claude-cli", "sonnet", "claude-cli", "claude-opus-5-5")


def test_the_novelty_default_judge_on_the_cli_is_another_family():
    from dokimi.novelty.client import default_judge

    judge, provider = default_judge("sonnet", "claude-cli")
    assert provider == "claude-cli" and not llm_cli.same_model("claude-cli", judge, "claude-cli", "sonnet")


def test_a_novelty_judge_of_the_answerers_family_counts_as_the_answerer():
    # run_measurement refuses a judge that same_client matches with the answerer, unless allowed.
    from dokimi.novelty.client import LLMClient, same_client

    judge = LLMClient(model="claude-sonnet-5-5", provider="anthropic")
    answerer = LLMClient(model="sonnet", provider="claude-cli")
    assert same_client(judge, answerer) and same_client(answerer, judge)
    assert not same_client(LLMClient(model="opus", provider="claude-cli"), answerer)


def test_an_agentic_arm_or_a_session_is_refused_on_the_cli_before_any_answer():
    from dokimi.commands import arms_refusals
    from dokimi.treatments import create_treatments

    plain = create_treatments(["baseline", "docs"], top_k=3, rerank_threshold=0.3)
    agentic = create_treatments(["baseline", "agent"], top_k=3, rerank_threshold=0.3)
    cli = argparse.Namespace(provider="claude-cli", session_turns=None)
    arms_refusals.refuse_cli_arms(cli, plain)
    with pytest.raises(ValueError, match="agentic arm"):
        arms_refusals.refuse_cli_arms(cli, agentic)
    with pytest.raises(ValueError, match="--session-turns"):
        arms_refusals.refuse_cli_arms(argparse.Namespace(provider="claude-cli", session_turns=3), plain)
    arms_refusals.refuse_cli_arms(argparse.Namespace(provider="openai", session_turns=3), agentic)


def test_a_cli_judge_given_a_temperature_stops_the_full_pipeline_before_any_work(monkeypatch, capsys):
    from dokimi.commands import orchestrate
    from dokimi import orchestrator

    def pipeline_must_not_start(*args, **kwargs):
        raise AssertionError("the pipeline started before the sampling refusal")

    monkeypatch.setattr(orchestrator, "EvaluationPipeline", pipeline_must_not_start)
    args = argparse.Namespace(repo="owner/repo", description=None, product="demo",
                              judge_provider="claude-cli", judge_temperature=0.0)
    with pytest.raises(SystemExit):
        orchestrate.cmd_evaluate(args)
    assert "takes no temperature" in capsys.readouterr().err


def test_every_command_with_a_hidden_or_default_model_refuses_the_cli_without_a_claude_model(capsys):
    from dokimi.cli import parse_args

    cases = [
        ["questions", "generate", "--product", "p", "--personas", "p.json", "--provider", "claude-cli"],
        ["questions", "analyze", "--questions", "q.json", "--product", "p", "--provider", "claude-cli"],
        ["questions", "panel-review", "--questions", "q.json", "--product", "p", "--provider", "claude-cli"],
        ["personas", "discover", "--product", "p", "--provider", "claude-cli"],
        ["subjects", "run", "subject.toml", "--provider", "claude-cli"],
    ]
    for argv in cases:
        with pytest.raises(SystemExit):
            parse_args(argv)
        # the refusal is the model gate, not an unrelated argparse error
        assert "takes a Claude model" in capsys.readouterr().err, argv
    assert parse_args(["subjects", "run", "subject.toml", "--provider", "claude-cli",
                       "--model", "haiku"]).model == "haiku"


def test_every_agentic_arm_kind_is_refused_on_the_cli_including_wrapped_and_combined(demo_skill):
    from dokimi.skills import load_skill

    from dokimi.commands import arms_refusals
    from dokimi.plugins import PluginWrappedTreatment
    from dokimi.treatments.arms import (
        BaselineTreatment,
        CombinedTreatment,
        MCPAgentTreatment,
        SkillAgentTreatment,
        SkillCatalogTreatment,
    )

    cli = argparse.Namespace(provider="claude-cli", session_turns=None)
    skill = load_skill(demo_skill)
    base = BaselineTreatment()
    agentic = {
        "mcp_agent": MCPAgentTreatment(mcp_client=None),
        "skill_agent": SkillAgentTreatment(skill),
        "skill_catalog": SkillCatalogTreatment([skill]),
        "combined_with_agent": CombinedTreatment([base, SkillAgentTreatment(skill)]),
        "plugin_wrapped_agent": PluginWrappedTreatment(SkillAgentTreatment(skill), []),
    }
    for kind, arm in agentic.items():
        with pytest.raises(ValueError, match="agentic arm"):
            arms_refusals.refuse_cli_arms(cli, [arm])
    arms_refusals.refuse_cli_arms(
        cli, [base, CombinedTreatment([base, base]), PluginWrappedTreatment(base, [])])


def test_the_arms_entry_point_refuses_a_cli_agentic_arm_before_any_answer(monkeypatch):
    from dokimi.commands import arms

    def answer_must_not_run(*args, **kwargs):
        raise AssertionError("an answer was paid for before the CLI refusal")

    monkeypatch.setattr(arms, "_resolve_doc_library_id", lambda *a, **k: None)
    monkeypatch.setattr(arms, "_validate_expected_skills", lambda *a, **k: None)
    args = argparse.Namespace(provider="claude-cli", model="sonnet", judge=False, session_turns=None,
                              top_k=3, rerank_threshold=0.3, product="p", context7_id=None,
                              plugins="", harness="arms-runner", matrix_cell=None, judge_provider=None)
    with pytest.raises(ValueError, match="agentic arm"):
        arms._build_arms_output(args, ["baseline", "agent"], [])


def test_an_alias_and_a_bedrock_or_prefixed_full_id_are_one_model_for_the_identity_checks():
    assert llm_cli.same_model("claude-cli", "sonnet", "anthropic", "us.anthropic.claude-sonnet-5-5-v1:0")
    assert llm_cli.same_model("claude-cli", "sonnet", "litellm", "anthropic/claude-sonnet-5-5")
    assert not llm_cli.same_model("claude-cli", "sonnet", "anthropic", "us.anthropic.claude-opus-5-1-v1:0")


def test_the_main_model_falls_back_to_most_output_when_the_requested_family_is_unknown():
    usage = {"helper-a": {"outputTokens": 5}, "helper-b": {"outputTokens": 500}}
    assert llm_cli._main_model(usage, "claude-fable-5-1") == "helper-b"


def test_the_login_check_runs_with_the_same_reduced_env_as_the_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    fake = _auth_runner({"loggedIn": True, "authMethod": "oauth_token"})
    llm_cli.detect("claude-cli", runner=fake)
    assert fake.calls
    for call in fake.calls:
        assert "ANTHROPIC_BASE_URL" not in call.env

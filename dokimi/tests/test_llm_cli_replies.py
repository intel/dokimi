# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A CLI reply that is not an answer (a failed or blank exit), the run-wide words that stop a run, and the
parse-time refusal of a CLI answerer without ``--model``."""

from __future__ import annotations

import pytest

from dokimi import llm_cli, process


@pytest.mark.parametrize(("parse", "provider", "done"), [
    ("_codex_parse", "codex-cli", {"returncode": 1, "stdout_bytes": b'{"type":"item.completed","item":{"type":"agent_message","text":"an answer"}}\n'}),
    ("_copilot_parse", "copilot-cli", {"returncode": 1, "stdout_bytes": b"an answer"}),
    ("_copilot_parse", "copilot-cli", {"returncode": 0, "stdout_bytes": b"  \n"}),
])
def test_a_failed_or_blank_cli_reply_is_never_an_answer(parse, provider, done):
    with pytest.raises(llm_cli.CliCallError):
        getattr(llm_cli, parse)(process.Completed(argv=("cli",), **done), "m", provider)


@pytest.mark.parametrize("message", ["not logged in", "authentication failed", "unknown model: x", "usage limit exceeded"])
def test_a_copilot_run_wide_message_stops_the_run_and_is_not_retried(message):
    done = process.Completed(argv=("copilot",), returncode=1, stderr_bytes=message.encode())
    with pytest.raises(llm_cli.CliCallError) as exc:
        llm_cli._copilot_parse(done, "m", "copilot-cli")
    assert exc.value.run_wide is True and exc.value.retryable is False


_ARGV_MARKERS = ("unexpected argument", "unknown option", "was removed")
_CODEX_MARKERS = ("usage_limit", "usage limit", "not logged in", "login", "authenticat", "unauthorized",
                  "unknown model", "model not found") + _ARGV_MARKERS
_COPILOT_MARKERS = ("not logged in", "login", "authenticat", "unknown model", "usage limit") + _ARGV_MARKERS


def _run_wide_error(parse: str, provider: str, message: str) -> llm_cli.CliCallError:
    done = process.Completed(argv=("cli",), returncode=1, stderr_bytes=message.encode())
    with pytest.raises(llm_cli.CliCallError) as exc:
        getattr(llm_cli, parse)(done, "m", provider)
    return exc.value


@pytest.mark.parametrize(("parse", "provider", "marker"), (
    [("_codex_parse", "codex-cli", m) for m in _CODEX_MARKERS]
    + [("_copilot_parse", "copilot-cli", m) for m in _COPILOT_MARKERS]
))
def test_every_run_wide_marker_stops_the_run_even_when_the_text_names_a_retryable_status(parse, provider, marker):
    error = _run_wide_error(parse, provider, f"{marker} (status 429)")
    assert error.run_wide is True and error.retryable is False


@pytest.mark.parametrize(("parse", "provider"), [("_codex_parse", "codex-cli"), ("_copilot_parse", "copilot-cli")])
def test_a_usage_limit_error_is_run_wide_and_not_retried_though_it_names_a_rate_limit(parse, provider):
    error = _run_wide_error(parse, provider, "usage limit reached: rate limit, try again later")
    assert error.run_wide is True and error.retryable is False


@pytest.mark.parametrize("provider", ["codex-cli", "copilot-cli"])
def test_a_cli_answerer_left_without_model_is_refused_at_parse(provider, capsys):
    from dokimi import cli

    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["answers", "generate", "--product", "p", "--questions", "q.json", "--provider", provider])
    assert exc.value.code == 2 and "needs --model" in capsys.readouterr().err
    assert cli.parse_args(["answers", "generate", "--product", "p", "--questions", "q.json", "--provider", provider,
                           "--model", "gpt-5"]).model == "gpt-5"

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


@pytest.mark.parametrize("provider", ["codex-cli", "copilot-cli"])
def test_a_cli_answerer_left_without_model_is_refused_at_parse(provider, capsys):
    from dokimi import cli

    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["answers", "generate", "--product", "p", "--questions", "q.json", "--provider", provider])
    assert exc.value.code == 2 and "needs --model" in capsys.readouterr().err
    assert cli.parse_args(["answers", "generate", "--product", "p", "--questions", "q.json", "--provider", provider,
                           "--model", "gpt-5"]).model == "gpt-5"

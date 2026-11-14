# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The routing estimate's allowances (#676) and the stop at a permanent error (#677)."""
from __future__ import annotations

import argparse
import json

import pytest

from dokimi.novelty.all_vs_all import all_vs_all
from dokimi.process import reply
from dokimi.novelty.routing import Candidate, model_routing
from dokimi.novelty.routing_estimate import selection_estimate

FIELD = [Candidate("perf", "profile a slow binary with perf"),
         Candidate("db", "tune a database index")]
QUERIES = ["profile my binary", "index a table", "tune the database"]


class AuthenticationError(Exception):
    """Named as litellm's, which is_retryable reads by class name."""


class PermissionDeniedError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ContentPolicyViolationError(Exception):
    pass


class Stub:
    model = "m"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    def text(self, prompt, *, system=None, accept=None, draw=0):
        self.calls += 1
        return self.behaviour(self.calls)


def test_the_estimate_counts_characters_over_the_documented_ratio():
    sent: list[str] = []

    class Recorder(Stub):
        def text(self, prompt, **kwargs):
            sent.append(prompt)
            return '{"pick": 1}'

    model_routing(FIELD, QUERIES[:1], client=Recorder(None))
    estimate = selection_estimate(FIELD, QUERIES[:1], repeats=2)
    assert estimate["chars_per_input_token"] == 2.6
    assert estimate["input_tokens"] == 2 * round(len(sent[0]) / 2.6)
    assert estimate["output_tokens_per_call"] == 150
    assert estimate["output_tokens"] == 2 * 150


def test_the_estimate_line_names_both_allowances(capsys):
    from dokimi.commands.novelty_routing_report import _print_estimate

    args = argparse.Namespace(model="m", provider=None, artifact="a", repeats=1)
    _print_estimate(args, FIELD, QUERIES, selection_estimate(FIELD, QUERIES))
    captured = capsys.readouterr()
    assert json.loads(captured.out)["chars_per_input_token"] == 2.6
    assert "2.6 characters per token" in captured.err
    assert "150 tokens a call" in captured.err
    assert "1.55x below" in captured.err


def test_an_authentication_error_stops_at_the_first_call_and_keeps_the_rows_before():
    def behaviour(n):
        if n <= 2:
            return '{"pick": 1}'
        raise AuthenticationError("Unable to locate credentials")

    stub = Stub(behaviour)
    result = model_routing(FIELD, QUERIES, client=stub, repeats=2)
    assert stub.calls == 3
    assert result["n_calls"] == 2 and result["n_failed"] == 0
    assert "Unable to locate credentials" in result["cap_stop"]
    assert result["partial"]["calls_done"] == 2 and result["partial"]["calls_planned"] == 6
    assert result["partial"]["reason"] == "permanent_error"
    assert result["partial"]["message"] == result["cap_stop"]


@pytest.mark.parametrize("exc", [PermissionDeniedError("denied"), NotFoundError("no such model"),
                                 RuntimeError("Missing credentials for the provider")])
def test_each_run_wide_error_stops_the_run(exc):
    def behaviour(n):
        if n == 2:
            raise exc
        return '{"pick": 1}'

    stub = Stub(behaviour)
    result = model_routing(FIELD, QUERIES, client=stub)
    assert stub.calls == 2 and result["n_calls"] == 1
    assert result["partial"]["reason"] == "permanent_error"


def test_all_vs_all_stops_at_a_run_wide_error_and_counts_the_calls_of_the_run():
    def behaviour(n):
        if n == 4:
            raise AuthenticationError("bad key")
        return '{"pick": 1}'

    stub = Stub(behaviour)
    queries = {"perf": ["profile my binary", "profile a slow binary"],
               "db": ["tune the database", "index a table"]}
    arm = all_vs_all(FIELD, queries, client=stub)["model"]
    assert stub.calls == 4
    assert arm["partial"]["calls_done"] == 3 and arm["partial"]["calls_planned"] == 4
    assert arm["partial"]["reason"] == "permanent_error"
    assert arm["partial"]["message"] == "permanent error: bad key"


def test_a_first_call_permanent_error_makes_one_call():
    stub = Stub(lambda n: (_ for _ in ()).throw(AuthenticationError("no credentials")))
    result = model_routing(FIELD, QUERIES, client=stub, repeats=3)
    assert stub.calls == 1 and result["per_query"] == []
    assert "permanent error: no credentials" in result["cap_stop"]


def test_an_error_of_one_query_is_a_row_and_the_loop_goes_on():
    def behaviour(n):
        if n == 2:
            raise ContentPolicyViolationError("refused")
        if n == 3:
            raise ConnectionError("connection reset by peer")  # retryable, out of retries
        return '{"pick": 1}'

    stub = Stub(behaviour)
    result = model_routing(FIELD, QUERIES + ["tune the index"], client=stub)
    assert stub.calls == 4
    assert result["n_failed"] == 2 and "cap_stop" not in result and "partial" not in result


class BadRequestError(Exception):
    """A context-length rejection of one oversized query."""


class CliCallError(Exception):
    retryable = False


@pytest.mark.parametrize("exc", [
    BadRequestError("maximum context length is 8192 tokens"),
    IndexError("reply has no choices"),
    CliCallError("claude exited 1"),
    RuntimeError("boom"),
])
def test_an_error_that_may_belong_to_one_query_is_a_row_and_a_later_query_is_asked(exc):
    def behaviour(n):
        if n == 1:
            raise exc
        return '{"pick": 1}'

    stub = Stub(behaviour)
    result = model_routing(FIELD, QUERIES, client=stub)
    assert stub.calls == 3 and result["n_calls"] == 3 and result["n_failed"] == 1
    assert "cap_stop" not in result and "partial" not in result


def _cli_reply(result: str, status) -> str:
    return json.dumps({"type": "result", "is_error": True, "result": result,
                       "api_error_status": status, "usage": {}, "modelUsage": {}})


def _cli_error(monkeypatch, tmp_path, answer):
    """The error a real ``claude -p`` failure raises, through llm_cli's own classification."""
    from dokimi import llm_cli
    from dokimi.process import FakeRunner

    def run(call):
        if answer is FileNotFoundError:
            raise FileNotFoundError("claude")
        return answer

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    with pytest.raises(llm_cli.CliCallError) as info:
        llm_cli.call("claude-cli", model="sonnet", prompt="p", runner=FakeRunner(run))
    return info.value


def _first_call_raises(exc):
    stub = Stub(lambda n: (_ for _ in ()).throw(exc))
    return stub, model_routing(FIELD, QUERIES, client=stub)


@pytest.mark.parametrize("answer", [
    reply(_cli_reply("Not logged in · Please run /login", None), returncode=1),
    reply(_cli_reply("Authentication error · This may be a temporary network issue, "
                     "please try again", None), returncode=1),
    reply(_cli_reply("Claude AI usage limit reached|1761660000", 429), returncode=1),
    reply("", stderr="Claude AI usage limit reached. Error 429", returncode=1),
    reply(_cli_reply("There's an issue with the selected model (claude-nope-1). It may not "
                     "exist or you may not have access to it.", 404), returncode=1),
    FileNotFoundError,
], ids=["not-logged-in", "auth", "usage-limit", "usage-limit-no-json", "unknown-model", "no-binary"])
def test_a_claude_cli_failure_every_call_meets_stops_the_run(monkeypatch, tmp_path, answer):
    stub, result = _first_call_raises(_cli_error(monkeypatch, tmp_path, answer))
    assert stub.calls == 1 and result["per_query"] == []
    assert result["partial"]["reason"] == "permanent_error"


@pytest.mark.parametrize("answer", [
    reply(_cli_reply("API Error: 400 Bad Request: invalid request", 400), returncode=1),
    reply("segfault", stderr="boom", returncode=139),
    reply("", timed_out=True),
], ids=["bad-request", "no-json", "timeout"])
def test_a_claude_cli_failure_of_one_call_is_a_row(monkeypatch, tmp_path, answer):
    stub, result = _first_call_raises(_cli_error(monkeypatch, tmp_path, answer))
    assert stub.calls == 3 and result["n_failed"] == 3 and "partial" not in result

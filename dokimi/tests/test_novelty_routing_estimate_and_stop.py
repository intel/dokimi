# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The routing estimate's allowances (#676) and the stop at a permanent error (#677)."""
from __future__ import annotations

import argparse
import json

from dokimi.novelty.routing import Candidate, model_routing
from dokimi.novelty.routing_estimate import selection_estimate

FIELD = [Candidate("perf", "profile a slow binary with perf"),
         Candidate("db", "tune a database index")]
QUERIES = ["profile my binary", "index a table", "tune the database"]


class AuthenticationError(Exception):
    """Named as litellm's, which is_retryable reads by class name."""


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
    assert "stopped at call 3; 2 earlier call(s) kept" in result["cap_stop"]
    assert result["partial"]["calls_done"] == 2 and result["partial"]["calls_planned"] == 6


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

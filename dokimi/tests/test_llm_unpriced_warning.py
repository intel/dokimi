# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""When cost calculation fails, log one warning per model and keep its cost null."""

from __future__ import annotations

import logging

import pytest

from dokimi import llm


@pytest.fixture(autouse=True)
def fresh_once_per_model_log(monkeypatch):
    monkeypatch.setattr(llm, "_COST_UNKNOWN_LOGGED", set())


def test_an_unpriced_route_logs_one_warning_and_its_cost_is_null(caplog):
    with caplog.at_level(logging.DEBUG, logger="dokimi.llm"):
        first = llm._safe_completion_cost(object(), "m-unpriced", litellm_model="nosuchprov/m-unpriced")
        second = llm._safe_completion_cost(object(), "m-unpriced", litellm_model="nosuchprov/m-unpriced")
    assert first is None and second is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "m-unpriced" in r.getMessage()]
    assert len(warnings) == 1
    assert "cost_usd will be null" in warnings[0].getMessage()


def test_a_failed_cost_call_for_a_priced_model_logs_the_actual_error(caplog, monkeypatch):
    from types import SimpleNamespace

    model = "claude-sonnet-5-5"

    def failed(**kwargs):
        raise RuntimeError("connection reset while reading usage")

    monkeypatch.setattr(llm, "_litellm", lambda: SimpleNamespace(
        model_cost={model: {"input_cost_per_token": 0.000003}}, completion_cost=failed,
    ))
    with caplog.at_level(logging.WARNING, logger="dokimi.llm"):
        cost = llm._safe_completion_cost(object(), model)
    assert cost is None
    [warning] = [r.getMessage() for r in caplog.records if r.name == "dokimi.llm" and r.levelno == logging.WARNING]
    assert warning == "Cost not computed for model 'claude-sonnet-5-5'; cost_usd will be null (connection reset while reading usage)"

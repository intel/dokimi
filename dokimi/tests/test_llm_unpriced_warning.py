# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A route litellm has no price for logs one warning per model and its cost stays null."""

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

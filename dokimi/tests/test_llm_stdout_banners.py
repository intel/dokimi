# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""litellm's "Provider List" and "Give Feedback" banners stay off stdout, where they break ``--json`` output:
every path of ``dokimi.llm`` that reaches litellm, with litellm itself failing offline."""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import litellm
import pytest

from dokimi import llm, llm_pricing

USER = [{"role": "user", "content": "q"}]


@pytest.fixture(autouse=True)
def banners_on(monkeypatch):
    """litellm as a fresh process has it: an earlier test's call through ``dokimi.llm`` turned the banners off."""
    monkeypatch.setattr(litellm, "suppress_debug_info", False)
    monkeypatch.setattr(llm, "_COST_UNKNOWN_LOGGED", set())  # the run's once-per-model log stays as it was
    for name in ("https_proxy", "http_proxy", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_a_call_to_a_provider_litellm_does_not_know_prints_nothing_to_stdout(capsys):
    with pytest.raises(litellm.BadRequestError, match="LLM Provider NOT provided"):
        llm.LiteLLMAdapter()(llm.Request("m", "nosuchprov", "nosuchprov/m", USER, api_key="k"))
    assert capsys.readouterr().out == ""


def test_a_call_whose_provider_cannot_be_reached_prints_nothing_to_stdout(monkeypatch, capsys):
    with socket.socket() as s:  # a loopback port nothing listens on: the connection is refused
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("ANTHROPIC_API_BASE", f"http://127.0.0.1:{port}")
    with pytest.raises(litellm.InternalServerError, match="Connection refused"):
        llm.LiteLLMAdapter()(llm.Request("claude-x", "anthropic", "anthropic/claude-x", USER, api_key="k"))
    assert capsys.readouterr().out == ""


def _price() -> bool:
    """``cost_per_token`` raises for a provider litellm does not know, as litellm's own does."""
    with pytest.raises(litellm.BadRequestError, match="LLM Provider NOT provided"):
        llm.cost_per_token(model="nosuchprov/m", prompt_tokens=1, completion_tokens=1)
    return True


@pytest.mark.parametrize("call", [
    lambda: llm.unsupported_sampling_params("m", "nosuchprov", temperature=0.0) == [],  # the check cannot run
    lambda: llm._safe_completion_cost(object(), "m", litellm_model="nosuchprov/m") is None,  # no price
    _price,
    lambda: llm_pricing.list_cost_usd("m", "nosuchprov", 1, 1) is None,  # an --estimate-only line
], ids=["sampling check", "reply cost", "cost per token", "list price"])
def test_the_checks_and_pricing_of_an_unknown_provider_print_nothing_to_stdout(capsys, call):
    assert call()
    assert capsys.readouterr().out == ""


def _litellm_loads() -> set[str]:
    """Each place the package imports litellm, as ``<file>:<innermost enclosing function>``."""
    root = Path(llm.__file__).parent
    loads = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scope = {child: fn.name for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                 for child in ast.walk(fn)}  # breadth-first: an inner function's name comes last and wins
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            if any(n == "litellm" or n.startswith("litellm.") for n in names):
                loads.add(f"{path.relative_to(root).as_posix()}:{scope.get(node, '<module>')}")
    return loads


def test_the_package_imports_litellm_only_where_the_banners_are_turned_off():
    # The flag is process-wide: a load elsewhere would pass the tests above whenever an earlier one ran first.
    assert _litellm_loads() == {"llm.py:_litellm"}

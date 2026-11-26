# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval personas discover --dry-run` prices the one call and builds no client; `--max-usd` gates it.

Part of #94 (F1b, spend + dry-run on the first paid leaf of the quickstart).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from dokimi import cli, llm_pricing
from dokimi.commands import personas as personas_cmd
from dokimi.exit_codes import EXIT_INCONCLUSIVE
from dokimi.defaults import DEFAULT_GENERATOR_MODEL
from dokimi.personas import generator as generator_mod
from dokimi.personas.analyzer import PersonaAnalyzer

PERSONA = {"id": "hpc_dev", "name": "HPC Developer", "description": "Writes parallel loops.",
           "skill_level": "advanced", "concerns": ["overhead"], "typical_questions": ["How?"]}


def _cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _discover(monkeypatch, out, *extra: str) -> int:
    return _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--output", str(out),
                "--model", f"openai/{DEFAULT_GENERATOR_MODEL}", "--description", "A library", *extra)


@pytest.fixture
def no_client(monkeypatch):
    """Any client construction or model call fails the test: a dry run and a refused cap must make neither."""

    def refuse(*_a, **_k):
        raise AssertionError("a model client was built")

    monkeypatch.setattr(personas_cmd, "PersonaGenerator", refuse)
    monkeypatch.setattr(generator_mod, "LLM", refuse)
    monkeypatch.setattr(generator_mod, "Invoker", refuse)
    return refuse


@pytest.fixture
def priced(monkeypatch):
    """List prices come from ``state["price"]`` (None: unpriced); each pricing call is recorded."""
    state: dict[str, Any] = {"price": 0.01, "calls": []}

    def list_cost_usd(model, provider, prompt_tokens, completion_tokens):
        state["calls"].append((model, provider, prompt_tokens, completion_tokens))
        return state["price"]

    monkeypatch.setattr(llm_pricing, "list_cost_usd", list_cost_usd)
    return state


@pytest.fixture
def reply(monkeypatch):
    """A model that answers with one persona, for the runs that do make the call."""
    model = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps({"personas": [PERSONA]})))
    monkeypatch.setattr(generator_mod, "LANGCHAIN_AVAILABLE", True)
    monkeypatch.setattr(generator_mod, "LLM", lambda *_a, **_k: None)
    monkeypatch.setattr(generator_mod, "Invoker", lambda _llm: model)
    return model


def test_dry_run_builds_no_client_writes_nothing_and_prints_the_estimate(tmp_path, monkeypatch, capsys,
                                                                         no_client, priced):
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--dry-run") == 0

    text = capsys.readouterr().out
    assert "Estimate: one generation call" in text and "~$0.010000 at list prices" in text
    assert "Dry run: no model was called" in text
    assert not out.exists() and not out.parent.exists()
    assert len(priced["calls"]) == 1  # priced once, from the prompt's size and the reply's


def test_dry_run_on_a_repo_reads_the_repo_but_saves_no_analysis(tmp_path, monkeypatch, capsys, no_client,
                                                                priced):
    reads: list[str] = []

    def read_repo(_self, repo):
        reads.append(repo)
        return {"description": "x" * 500}

    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository", read_repo)
    out = tmp_path / "personas" / "lib.json"
    code = _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--repo", "o/r",
                "--output", str(out), "--save-analysis", "--dry-run")
    assert code == 0 and reads == ["o/r"]
    assert not out.parent.exists()  # neither the personas file nor the analysis next to it
    assert "Dry run" in capsys.readouterr().out
    repo_tokens = priced["calls"][-1][2]
    assert _discover(monkeypatch, tmp_path / "lib.json", "--dry-run") == 0
    assert repo_tokens > priced["calls"][-1][2]  # the repo's analysis is priced, not an empty one


def test_the_prompt_is_priced_at_four_characters_a_token_and_the_reply_budget(tmp_path, monkeypatch, priced):
    est = generator_mod.estimate_generation("lib", {"description": "A library"}, "gpt-x", "openai")
    prompt = generator_mod.PersonaGenerator.build_prompt("lib", {"description": "A library"})
    assert est == {"prompt_tokens": len(prompt) // 4 + 1, "reply_tokens": generator_mod.REPLY_TOKENS,
                   "usd": 0.01}
    assert priced["calls"][-1] == ("gpt-x", "openai", len(prompt) // 4 + 1, generator_mod.REPLY_TOKENS)


def test_a_cap_below_the_estimate_refuses_the_call_with_exit_3(tmp_path, monkeypatch, capsys, no_client,
                                                               priced):
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--max-usd", "0.001") == EXIT_INCONCLUSIVE
    err = capsys.readouterr().err
    assert "--max-usd 0.001 stops it before the call" in err and "no call was made" in err
    assert not out.exists()


@pytest.mark.parametrize("cap", ["0.01", "1"])
def test_a_cap_at_or_above_the_estimate_lets_the_call_run(tmp_path, monkeypatch, reply, priced, cap):
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--max-usd", cap) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["personas"][0]["id"] == "hpc_dev"


def test_a_cap_on_a_model_with_no_list_price_refuses_the_call(tmp_path, monkeypatch, capsys, no_client, priced):
    priced["price"] = None
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--max-usd", "5") == EXIT_INCONCLUSIVE
    assert "the model has no list price" in capsys.readouterr().err


def test_no_cap_and_no_price_still_makes_the_call(tmp_path, monkeypatch, reply, priced):
    priced["price"] = None
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out) == 0
    assert out.exists()
    assert priced["calls"] == []  # a plain run makes no pricing lookup, so a lookup failure cannot break it


def test_max_usd_refuses_zero_and_negative_values(tmp_path, monkeypatch, capsys, no_client, priced):
    """A cap of 0 or less would disable the cap: positive_usd refuses it with the usage exit 2."""
    for bad in ("0", "-1", "nan"):
        assert _discover(monkeypatch, tmp_path / "p.json", "--max-usd", bad) == 2
        assert "must be a positive number of USD" in capsys.readouterr().err


def test_a_refused_cap_on_a_repo_writes_no_analysis(tmp_path, monkeypatch, no_client, priced):
    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository", lambda _self, _repo: {"description": "x"})
    out = tmp_path / "personas" / "lib.json"
    code = _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--repo", "o/r",
                "--output", str(out), "--save-analysis", "--max-usd", "0.001")
    assert code == EXIT_INCONCLUSIVE
    assert not out.parent.exists()  # no analysis file either: the cap refused before anything was written


def test_a_dry_run_with_a_cap_says_whether_the_real_run_would_be_refused(tmp_path, monkeypatch, capsys,
                                                                         no_client, priced):
    assert _discover(monkeypatch, tmp_path / "lib.json", "--dry-run", "--max-usd", "0.001") == 0
    assert "Real run: --max-usd 0.001 stops it before the call" in capsys.readouterr().out


def test_a_capped_run_that_may_call_builds_the_generator_before_writing_the_analysis(tmp_path, monkeypatch, priced):
    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository", lambda _self, _repo: {"description": "x"})

    def missing_llm(*_a, **_k):
        raise ImportError("langchain is not installed")

    monkeypatch.setattr(personas_cmd, "PersonaGenerator", missing_llm)
    out = tmp_path / "personas" / "lib.json"
    code = _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--repo", "o/r",
                "--output", str(out), "--save-analysis", "--max-usd", "1")
    assert code == 5 and not out.parent.exists()  # the cap let the call through; nothing is left behind


def test_a_dry_run_fails_where_the_real_run_would_without_the_llm_dependency(tmp_path, monkeypatch, no_client,
                                                                          priced):
    """The dry run runs the real run's dependency check first: exit 5 and no repo read, not a clean estimate."""
    reads: list[str] = []

    def read_repo(_self, repo):
        reads.append(repo)
        return {}

    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository", read_repo)
    monkeypatch.setattr(generator_mod, "LANGCHAIN_AVAILABLE", False)
    code = _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--repo", "o/r",
                "--output", str(tmp_path / "lib.json"), "--dry-run")
    assert code == 5 and reads == [] and not (tmp_path / "lib.json").exists()


def test_a_plain_run_builds_the_generator_before_any_repo_read(tmp_path, monkeypatch):
    """A missing LLM dependency fails a plain --repo run before GitHub is read or a file is written, as on base."""
    reads: list[str] = []

    def read_repo(_self, _repo):
        reads.append(_repo)
        return {"description": "x"}

    def missing_llm(*_a, **_k):
        raise ImportError("langchain is not installed")

    monkeypatch.setattr(PersonaAnalyzer, "analyze_repository", read_repo)
    monkeypatch.setattr(personas_cmd, "PersonaGenerator", missing_llm)
    code = _cli(monkeypatch, "eval", "personas", "discover", "--product", "lib", "--repo", "o/r",
                "--output", str(tmp_path / "personas" / "lib.json"), "--save-analysis")
    assert code == 5 and reads == []  # the crash exit; GitHub was not read and no analysis file was written
    assert not (tmp_path / "personas").exists()


def test_a_tiny_estimate_is_not_shown_as_zero(tmp_path, monkeypatch, capsys, no_client, priced):
    priced["price"] = 0.00002
    assert _discover(monkeypatch, tmp_path / "lib.json", "--dry-run") == 0
    assert "~$2.00e-05 at list prices" in capsys.readouterr().out


def test_save_analysis_with_a_description_writes_no_analysis(tmp_path, monkeypatch, capsys, reply):
    out = tmp_path / "personas" / "lib.json"
    assert _discover(monkeypatch, out, "--save-analysis") == 0
    assert "--save-analysis is ignored in description-only mode" in capsys.readouterr().err
    assert out.exists() and not (out.parent / "lib_analysis.json").exists()


def test_discover_help_says_when_the_output_path_is_checked(monkeypatch, capsys):
    assert _cli(monkeypatch, "eval", "personas", "discover", "--help") == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "Output paths are checked when writing files" in help_text

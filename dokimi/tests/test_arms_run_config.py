# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``ArmsRunConfig``: its defaults are the ``arms run`` flags' defaults, and a run it cannot
name a library id for stops with the same message from every entry point.

What each entry point hands the runner is pinned in ``test_arms_run_config_parity.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import parse_args
from dokimi.commands.arms import run_config_from_args
from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.arms_run import ArmsRunConfig


ARMS_RUN = ["arms", "run", "--product", "demo", "--questions", "q.json", "--arms", "baseline"]


@pytest.mark.parametrize("judge_provider", [[], ["--judge-provider", "openai"]])
def test_the_config_defaults_are_what_arms_run_runs_without_a_flag(judge_provider):
    # A command without a flag (subjects run has no --temperature) and a caller building the
    # config both run these defaults, so they must not drift from the flags'; see #270.
    args = parse_args([*ARMS_RUN, *judge_provider])
    config = ArmsRunConfig(product="demo", judge_provider=args.judge_provider)

    assert run_config_from_args(args) == config


def test_a_judge_provider_with_no_default_judge_needs_a_judge_model(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*ARMS_RUN, "--judge-provider", "openrouter"])
    assert exc.value.code == 2
    assert "--judge-provider openrouter has no default judge model" in capsys.readouterr().err

    with pytest.raises(ValueError, match="'openrouter' has no default judge model"):
        ArmsRunConfig(product="demo", judge_provider="openrouter")
    assert ArmsRunConfig(product="demo", judge_provider="openrouter",
                         judge_model="m").judge_model == "m"


@pytest.fixture
def unresolvable(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """A doc arm on a product no library id can be named for, and a guard on answering."""
    def refuse(product, client, registry=None):
        raise ValueError(f"cannot name a library id for {product!r}")

    def answer(self, *a, **kw):
        raise AssertionError("no answer may be paid for")

    monkeypatch.setattr("dokimi.mcp.factory.resolve_library_id_for_product", refuse)
    monkeypatch.setattr(ArmRunner, "run", answer)
    docs = tmp_path / "docs.md"
    docs.write_text("# Demo\n", encoding="utf-8")
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"matrix": {"cells": [
        {"id": "only", "model": "m", "provider": "openai", "harness": "arms-runner"},
    ]}}), encoding="utf-8")
    return {"docs": docs, "questions": questions, "cells": cells, "out": tmp_path / "out"}


@pytest.mark.parametrize("command", [
    ["arms", "run", "--out-json", "{out}/a.json", "--out-md", "{out}/a.md"],
    ["arms", "matrix-run", "--matrix-cells", "{cells}", "--out-dir", "{out}"],
])
def test_a_doc_arm_without_a_library_id_stops_the_run_before_any_answer(command, unresolvable,
                                                                       capsys):
    argv = [a.format(**unresolvable) for a in command]
    args = parse_args([*argv, "--product", "demo", "--questions", str(unresolvable["questions"]),
                       "--arms", f"baseline,docs:local:{unresolvable['docs']}"])

    with pytest.raises(SystemExit) as exc:
        args.func(args)

    assert exc.value.code == 1
    # Not under "Error building arms/plugins" or "Error running matrix cell": the id is the
    # product's, not an arm's or a cell's.
    assert capsys.readouterr().err == "Error: cannot name a library id for 'demo'\n"


def test_a_failing_matrix_cell_is_named_and_the_cells_before_it_are_kept(tmp_path, provider_calls,
                                                                        capsys):
    provider_calls.fake.respond = lambda request: "Use parallel_for."
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"matrix": {"cells": [
        {"id": "ok", "model": "m", "provider": "openai", "harness": "arms-runner"},
        {"id": "bad", "model": "m", "provider": "openai", "harness": "arms-runner",
         "plugins": ["plugin:no-such-plugin"]},
    ]}}), encoding="utf-8")
    out = tmp_path / "out"
    args = parse_args(["arms", "matrix-run", "--product", "demo", "--questions", str(questions),
                       "--arms", "baseline", "--matrix-cells", str(cells), "--out-dir", str(out)])

    with pytest.raises(SystemExit) as exc:
        args.func(args)

    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith(
        "Error running matrix cell 'bad': Unknown plugin spec: 'plugin:no-such-plugin'")
    assert sorted(p.name for p in (out / "cells").iterdir()) == ["ok.json", "ok.md"]


def test_run_matrix_checks_every_cell_before_the_first_is_paid_for(monkeypatch, provider_calls):
    from dokimi.eval.arms_run import run_matrix
    from dokimi.eval.cells import MatrixCellDescriptor

    monkeypatch.setattr("dokimi.llm.unsupported_sampling_params",
                        lambda model, provider, **kw: ["seed"] if model == "m-bad" else [])
    monkeypatch.setattr("dokimi.eval.arms_run.probe_sampling", lambda *a: None)
    cells = [MatrixCellDescriptor("ok", "m-ok", "openai", "arms-runner"),
             MatrixCellDescriptor("bad", "m-bad", "openai", "arms-runner")]
    finished = []

    with pytest.raises(ValueError, match="does not support seed for model 'm-bad'"):
        run_matrix(ArmsRunConfig(product="demo", seed=1), cells, ["baseline"],
                   [{"id": "q1", "question": "How?"}], on_cell=lambda cell, out: finished.append(cell))

    assert finished == [] and provider_calls.calls == []


def test_check_matrix_names_the_first_cell_whose_sampling_the_provider_refuses(monkeypatch):
    from dokimi.eval.arms_run import check_matrix
    from dokimi.eval.cells import MatrixCellDescriptor

    monkeypatch.setattr("dokimi.llm.unsupported_sampling_params",
                        lambda model, provider, **kw: ["seed"] if model == "m-bad" else [])
    monkeypatch.setattr("dokimi.eval.arms_run.probe_sampling", lambda *a: None)
    cells = [MatrixCellDescriptor("ok", "m-ok", "openai", "arms-runner"),
             MatrixCellDescriptor("bad", "m-bad", "openai", "arms-runner")]

    with pytest.raises(ValueError, match="^matrix cell 'bad': Provider 'openai' does not support"):
        check_matrix(ArmsRunConfig(product="demo", seed=1), cells)


def test_check_matrix_names_the_first_cell_with_an_unsupported_harness():
    from dokimi.eval.arms_run import check_matrix
    from dokimi.eval.cells import MatrixCellDescriptor

    cells = [MatrixCellDescriptor("ok", "m", "openai", "arms-runner"),
             MatrixCellDescriptor("odd", "m", "openai", "other-runner")]

    with pytest.raises(ValueError, match="^matrix cell 'odd': matrix cells only support"):
        check_matrix(ArmsRunConfig(product="demo"), cells)

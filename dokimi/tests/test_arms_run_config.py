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


def test_the_config_defaults_are_what_arms_run_runs_without_a_flag():
    # A command without some flag (subjects run has no --temperature) and a caller building
    # the config itself both run with these defaults, so they must not drift from the flags'.
    args = parse_args(["arms", "run", "--product", "demo", "--questions", "q.json",
                       "--arms", "baseline"])

    assert run_config_from_args(args) == ArmsRunConfig(product="demo")


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

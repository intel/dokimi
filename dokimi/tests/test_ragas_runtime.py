# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The RAGAS path imports from the installed dependencies, not from mocks.

tests/test_eval_ragas.py mocks ragas out, and other test modules put stub
``langchain_*`` modules into ``sys.modules``, so the real import runs in a
fresh interpreter. Every ragas release imports
``langchain_community.chat_models.vertexai``; langchain-community 0.4.2 removed
it, and nothing else noticed that ``eval ragas`` could not start.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_PROBE = """
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
from dokimi.eval.ragas_eval import RagasEvaluator

for provider in ("openai", "anthropic"):
    RagasEvaluator(
        provider=provider,
        api_key="offline-dummy",
        metrics=["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
    )
"""


def test_ragas_imports_and_evaluator_builds_from_installed_deps():
    # The lock leaves the `[ragas]` extra out; test-latest installs it and sets
    # DOKIMI_REQUIRE_RAGAS=1, so there a missing ragas fails instead of skipping.
    if importlib.util.find_spec("ragas") is None:
        if os.environ.get("DOKIMI_REQUIRE_RAGAS") == "1":
            pytest.fail("DOKIMI_REQUIRE_RAGAS=1 but ragas is not installed")
        pytest.skip("the [ragas] extra is not installed")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO,
        env={**os.environ, "RAGAS_DO_NOT_TRACK": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


def test_ragas_without_the_extra_exits_with_the_install_command(monkeypatch, tmp_path):
    from dokimi.commands.evaluate import _run_ragas_eval

    monkeypatch.setitem(sys.modules, "ragas", None)
    monkeypatch.setitem(sys.modules, "ragas.llms", None)
    with pytest.raises(SystemExit) as exc:
        _run_ragas_eval([], tmp_path / "answers.json", object())
    assert "pip install 'dokimi[ragas]'" in str(exc.value.code)
    assert not (tmp_path / "answers_ragas.json").exists()


@pytest.mark.parametrize("command", ["eval_ragas", "answers_generate"])
def test_ragas_commands_exit_before_any_work_without_the_extra(monkeypatch, tmp_path, command):
    from dokimi.commands.answers import cmd_answers_generate
    from dokimi.commands.evaluate import cmd_eval_ragas

    monkeypatch.setitem(sys.modules, "ragas", None)
    missing = tmp_path / "missing.json"  # reading it would raise FileNotFoundError
    args = argparse.Namespace(answers=str(missing), questions=str(missing), ragas=True)
    run = cmd_eval_ragas if command == "eval_ragas" else cmd_answers_generate
    with pytest.raises(SystemExit) as exc:
        run(args)
    assert "pip install 'dokimi[ragas]'" in str(exc.value.code)

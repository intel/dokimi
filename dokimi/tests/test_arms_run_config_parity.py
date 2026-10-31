# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What each arms entry point hands the runner, for every run-configuration field.

``arms run``, ``arms matrix-run`` and ``subjects run`` all end in an arms run, each from its
own flags: ``matrix-run`` puts each cell's model, provider, harness and plugins in, and
``subjects run`` has only some of the flags. A field one path drops is indistinguishable from
a user who chose the default, so this pins, per entry point, what reaches the runner when
every field it takes is set away from its default and when every field is left out.

The argv goes through ``dokimi.cli.parse_args`` (what ``main`` calls) to the command's own
handler. The boundary is the ``ArmRunner`` constructor and its ``run``/``judge``/
``build_output`` calls, recorded on the class, so the path in between can be restructured
without editing these tests. Every LLM call is answered at the ``dokimi.llm`` adapter seam
(``provider_calls``).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from dokimi.cli import parse_args
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)
from dokimi.eval import JudgePanel
from dokimi.eval.arm_runner import ArmRunner
from tests.provider_calls import JUDGE_REPLY

#: Constructor arguments recorded as they arrived (the runner's own default when omitted).
_INIT_FIELDS = (
    "model", "provider", "harness", "matrix_cell", "max_iterations", "max_tokens",
    "grounded_from_topics", "stats_correction", "temperature", "seed", "session_turns",
    "turn_reminder",
)


class _RunnerCalls:
    """One row per ``ArmRunner`` built, in build order: what its constructor and calls got."""

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self._by_runner: dict[int, dict] = {}

    def start(self, runner: ArmRunner, row: dict) -> None:
        self._by_runner[id(runner)] = row
        self.runs.append(row)

    def of(self, runner: ArmRunner) -> dict:
        return self._by_runner[id(runner)]


def _judge_row(judge) -> dict:
    if isinstance(judge, JudgePanel):
        panel = [(j.role, j.model, j.provider, j.temperature, j.seed) for j in judge.judges]
        return {"judge": None, "panel": panel}
    return {"judge": (judge.model, judge.provider, judge.temperature, judge.seed), "panel": None}


@pytest.fixture
def runner_calls(monkeypatch, provider_calls):
    calls = _RunnerCalls()
    names = ("__init__", "run", "judge", "build_output")
    originals = {name: getattr(ArmRunner, name) for name in names}
    signatures = {name: inspect.signature(fn) for name, fn in originals.items()}

    def arguments(name, *a, **kw) -> dict:
        bound = signatures[name].bind(*a, **kw)
        bound.apply_defaults()
        return bound.arguments

    def init(self, *a, **kw):
        got = arguments("__init__", self, *a, **kw)
        unwrapped = [getattr(t, "inner", t) for t in got["treatments"]]  # past any plugin
        docs = [t for t in unwrapped if hasattr(t, "top_k")]
        calls.start(self, {
            "arms": [t.name for t in got["treatments"]],
            **{field: got[field] for field in _INIT_FIELDS},
            "plugin_set": got["plugin_set"]["plugin_set"],
            "doc_top_k": [t.top_k for t in docs],
            "doc_rerank_threshold": [t.reranker.threshold for t in docs],
            # Set by the judge call below; a run that is not judged leaves them None.
            "judge": None, "panel": None, "judge_library_name": None, "judge_baseline_arm": None,
            "judge_concurrency": None,
        })
        originals["__init__"](self, *a, **kw)

    def run(self, *a, **kw):
        got = arguments("run", self, *a, **kw)
        calls.of(self).update(library_name=got["library_name"], library_id=got["library_id"],
                              concurrency=got["concurrency"])
        return originals["run"](self, *a, **kw)

    def judge(self, *a, **kw):
        got = arguments("judge", self, *a, **kw)
        calls.of(self).update(_judge_row(got["judge"]), judge_library_name=got["library_name"],
                              judge_baseline_arm=got["baseline_arm"],
                              judge_concurrency=got["concurrency"])
        return originals["judge"](self, *a, **kw)

    def build_output(self, *a, **kw):
        got = arguments("build_output", self, *a, **kw)
        calls.of(self).update(output_baseline_arm=got["baseline_arm"],
                              output_judged=got["judge"] is not None)
        return originals["build_output"](self, *a, **kw)

    for name, fn in zip(names, (init, run, judge, build_output), strict=True):
        monkeypatch.setattr(ArmRunner, name, fn)
    # One reply for every request: it parses as a judge and a panel verdict, and as an answer or
    # the sampling probe's reply it is just text.
    provider_calls.fake.respond = lambda request: JUDGE_REPLY
    return calls


@pytest.fixture
def io(tmp_path: Path) -> dict[str, Path]:
    docs = tmp_path / "docs.md"
    docs.write_text("# Demo\nUse parallel_for for loops.\n", encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{
        "id": "q1", "question": "What API runs loops?", "follow_ups": ["And for reductions?"],
    }]), encoding="utf-8")
    cells = tmp_path / "matrix.cells.json"
    cells.write_text(json.dumps({"matrix": {"cells": [
        {"id": "cell-a", "model": "cell-model-a", "provider": "openrouter",
         "harness": "arms-runner", "plugins": []},
        {"id": "cell-b", "model": "cell-model-b", "provider": "openai",
         "harness": "arms-runner", "plugins": ["plugin:truncate:800"]},
    ]}}), encoding="utf-8")
    profile = tmp_path / "plain.md"
    profile.write_text("---\nid: plain\nname: Plain\ndescription: d\n---\nAnswer plainly.\n",
                       encoding="utf-8")
    subject = tmp_path / "subject.toml"
    subject.write_text(f"""
baseline = "profile:{profile.as_posix()}"

[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:{docs.as_posix()}"

[suite]
products = ["demo"]
questions = ["{questions.as_posix()}"]
""", encoding="utf-8")
    return {"docs": docs, "questions": questions, "cells": cells, "subject": subject,
            "out": tmp_path / "out"}


def _arms_run(io, *flags: str) -> list[str]:
    return ["arms", "run", "--product", "demo", "--questions", str(io["questions"]),
            "--arms", f"baseline,docs:local:{io['docs']}",
            "--out-json", str(io["out"] / "arms.json"), "--out-md", str(io["out"] / "arms.md"),
            *flags]


def _matrix_run(io, *flags: str) -> list[str]:
    return ["arms", "matrix-run", "--product", "demo", "--questions", str(io["questions"]),
            "--arms", f"baseline,docs:local:{io['docs']}", "--matrix-cells", str(io["cells"]),
            "--out-dir", str(io["out"]), *flags]


def _subjects_run(io, *flags: str) -> list[str]:
    return ["subjects", "run", str(io["subject"]), "--out-dir", str(io["out"]), *flags]


def _run(argv: list[str]) -> None:
    args = parse_args(argv)
    args.func(args)


#: Every flag the three entry points share (``subjects run`` takes only these), away from its
#: default. ``--judge-model`` is left to ``--judge-provider`` in the panel case below.
_SHARED_FLAGS = (
    "--model", "answer-model", "--provider", "openrouter", "--context7-id", "/demo/docs",
    "--judge", "--judge-model", "judge-model", "--judge-provider", "openai",
    "--top-k", "7", "--rerank-threshold", "0.55", "--max-iterations", "3",
    "--max-tokens", "32000", "--grounded-from-topics", "--concurrency", "2",
)
#: The flags only ``arms run`` and ``arms matrix-run`` take, away from their default.
_ARMS_FLAGS = (
    "--temperature", "0.25", "--seed", "11", "--judge-temperature", "0.75",
    "--stats-correction", "bh", "--baseline-arm", "docs",
)


def _defaults(io) -> dict:
    """What the runner gets from ``arms run`` with ``--judge`` and nothing else."""
    return {
        "arms": ["baseline", "docs"],
        "model": DEFAULT_ANSWER_MODEL, "provider": DEFAULT_ANSWER_PROVIDER,
        "harness": "arms-runner", "matrix_cell": None, "plugin_set": "none",
        "max_iterations": 6, "max_tokens": None, "grounded_from_topics": False,
        "stats_correction": "holm", "temperature": None, "seed": None,
        "session_turns": None, "turn_reminder": None,
        "doc_top_k": [5], "doc_rerank_threshold": [0.3],
        "library_name": "demo", "library_id": f"local:{io['docs']}", "concurrency": 5,
        "judge": (DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER, None, None), "panel": None,
        "judge_library_name": "demo", "judge_baseline_arm": "baseline", "judge_concurrency": 5,
        "output_baseline_arm": "baseline", "output_judged": True,
    }


def _shared_set(io) -> dict:
    """``_defaults`` with every ``_SHARED_FLAGS`` value in place."""
    return {
        **_defaults(io),
        "model": "answer-model", "provider": "openrouter", "library_id": "/demo/docs",
        "judge": ("judge-model", "openai", None, None),
        "doc_top_k": [7], "doc_rerank_threshold": [0.55], "max_iterations": 3,
        "max_tokens": 32000, "grounded_from_topics": True, "concurrency": 2, "judge_concurrency": 2,
    }


def _arms_set(io) -> dict:
    """``_shared_set`` with every ``_ARMS_FLAGS`` value in place too."""
    return {
        **_shared_set(io),
        "temperature": 0.25, "seed": 11, "stats_correction": "bh",
        "judge": ("judge-model", "openai", 0.75, None),
        "judge_baseline_arm": "docs", "output_baseline_arm": "docs",
    }


def _cells(row: dict) -> list[dict]:
    """*row* once per cell of the ``io`` descriptor: each sets its name, model, provider, plugins."""
    return [
        {**row, "matrix_cell": "cell-a", "model": "cell-model-a", "provider": "openrouter"},
        {**row, "matrix_cell": "cell-b", "model": "cell-model-b", "provider": "openai",
         "plugin_set": "truncate:800"},
    ]


def _subject(row: dict) -> dict:
    """*row* as ``subjects run`` gets it: the descriptor's arms, a profile as the baseline."""
    return {**row, "arms": ["profile:plain", "docs"], "judge_baseline_arm": "profile:plain",
            "output_baseline_arm": "profile:plain"}


def test_arms_run_hands_every_field_to_the_runner(runner_calls, io):
    _run(_arms_run(io, *_SHARED_FLAGS, *_ARMS_FLAGS,
                   "--harness", "single-shot", "--plugins", "plugin:truncate:800"))

    assert runner_calls.runs == [
        {**_arms_set(io), "harness": "single-shot", "plugin_set": "truncate:800"},
    ]


def test_arms_run_with_nothing_set_hands_the_defaults(runner_calls, io):
    _run(_arms_run(io, "--judge"))

    assert runner_calls.runs == [_defaults(io)]


def test_arms_run_without_judge_never_calls_it(runner_calls, io):
    _run(_arms_run(io))

    assert runner_calls.runs == [{**_defaults(io), "judge": None, "judge_library_name": None,
                                  "judge_baseline_arm": None, "judge_concurrency": None,
                                  "output_judged": False}]


def test_arms_run_hands_a_panel_its_roles_and_the_judge_settings(runner_calls, io):
    # --judge-model left out: parse_args puts the --judge-provider default judge in its place.
    _run(_arms_run(io, "--judge", "--judge-panel", "technical_expert,doc_reviewer",
                   "--judge-provider", "openai", "--judge-temperature", "0.5"))

    assert runner_calls.runs == [{
        **_defaults(io), "judge": None,
        "panel": [("technical_expert", DEFAULT_GENERATOR_MODEL, "openai", 0.5, None),
                  ("doc_reviewer", DEFAULT_GENERATOR_MODEL, "openai", 0.5, None)],
    }]


def test_arms_run_hands_the_session_fields_to_the_runner(runner_calls, io):
    _run(_arms_run(io, "--session-turns", "2", "--turn-reminder", "Stay on topic."))

    assert runner_calls.runs == [{
        **_defaults(io), "session_turns": 2, "turn_reminder": "Stay on topic.",
        "judge": None, "judge_library_name": None, "judge_baseline_arm": None,
        "judge_concurrency": None, "output_judged": False,
    }]


def test_arms_run_with_a_named_cell_takes_the_cell_over_the_flags(runner_calls, io):
    _run(_arms_run(io, "--judge", "--model", "flag-model", "--provider", "openai",
                   "--plugins", "plugin:truncate:400",
                   "--matrix-cells", str(io["cells"]), "--matrix-cell", "cell-b"))

    assert runner_calls.runs == [_cells(_defaults(io))[1]]


def test_matrix_run_hands_every_field_to_each_cell(runner_calls, io):
    # The cells name model, provider and plugins, so the --model/--provider flags do not reach
    # the runner; matrix-run takes no --session-turns/--turn-reminder.
    _run(_matrix_run(io, *_SHARED_FLAGS, *_ARMS_FLAGS))

    assert runner_calls.runs == _cells(_arms_set(io))


def test_matrix_run_with_nothing_set_hands_each_cell_the_defaults(runner_calls, io):
    _run(_matrix_run(io, "--judge"))

    assert runner_calls.runs == _cells(_defaults(io))


def test_matrix_run_hands_a_panel_its_roles_in_each_cell(runner_calls, io):
    _run(_matrix_run(io, "--judge", "--judge-panel", "technical_expert",
                     "--judge-provider", "openai", "--judge-temperature", "0.5"))

    assert runner_calls.runs == _cells({
        **_defaults(io), "judge": None,
        "panel": [("technical_expert", DEFAULT_GENERATOR_MODEL, "openai", 0.5, None)],
    })


def test_subjects_run_hands_every_field_it_takes_to_the_runner(runner_calls, io):
    # subjects run takes none of _ARMS_FLAGS (nor the session flags): those reach the runner
    # as their defaults. With no [[matrix.cells]] its one cell is --model/--provider/--harness.
    _run(_subjects_run(io, *_SHARED_FLAGS))

    assert runner_calls.runs == [_subject({**_shared_set(io), "matrix_cell": "arms-runner"})]


def test_subjects_run_with_nothing_set_hands_the_defaults(runner_calls, io):
    _run(_subjects_run(io, "--judge"))

    assert runner_calls.runs == [_subject({**_defaults(io), "matrix_cell": "arms-runner"})]

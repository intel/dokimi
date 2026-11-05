# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Each input refusal that PR #393 converts to InputError is pinned by a test that reaches it.

A refusal is exit 2 with its message through `cli.main`. Each test runs the command through
`refusal()`, the same way, and fails if the refusal is removed (the command then exits 0 or 1).
"""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from tests.cli_refusal import refusal


def _arms(tmp_path, n=2, *, baseline_only=False):
    """An arms run: every question has a baseline answer, and a skill answer unless baseline_only."""
    def arms_of(i):
        arms = {"baseline": {"answer": f"b{i}", "metadata": {"arm_kind": "baseline"}}}
        if not baseline_only:
            arms["skill"] = {"answer": f"s{i}", "metadata": {"arm_kind": "skill"}}
        return arms

    data = {"library_name": "L", "baseline_arm": "baseline", "arms": ["baseline", "skill"],
            "answers": [{"question_id": f"Q{i}", "question_text": f"question {i}?", "arms": arms_of(i)}
                        for i in range(n)]}
    path = tmp_path / "arms.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(argv, expect_code=2):
    args = build_parser().parse_args(argv)
    with refusal() as outcome:
        args.func(args)
    assert outcome.code == expect_code, outcome.err
    return outcome


@pytest.fixture
def no_judge(monkeypatch):
    """Judge construction is offline: the refusal fires before any call, but the command builds one."""
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            self.llm = None

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)


def test_pairwise_refuses_when_no_question_has_a_baseline_and_another_arm(tmp_path, no_judge):
    path = _arms(tmp_path, baseline_only=True)
    out = _run(["eval", "score", "pairwise", "--answers", str(path)])
    assert "no question has both" in out.err

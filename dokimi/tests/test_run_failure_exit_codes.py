# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run in which the judge failed on every comparison exits 1: a runtime failure, not bad input."""

from __future__ import annotations

import json

import pytest

from dokimi import cli
from dokimi.commands import judge_reliability as jr
from dokimi.eval import pairwise as pw


class _AllFailed:
    """A pairwise report whose every comparison errored: no verdicts, two errors."""

    verdicts: list = []
    errors = ["RuntimeError: no key", "RuntimeError: no key"]

    def to_dict(self):
        return {"summary": {}}

    def format_markdown(self):
        return "# report\n"


def test_pairwise_with_every_comparison_failed_exits_1_after_writing_the_report(tmp_path, monkeypatch, capsys):
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps({"library_name": "L", "answers": []}), encoding="utf-8")
    monkeypatch.setattr(jr, "_load_answers", lambda path: ({"library_name": "L"}, [{"question_id": "Q0"}]))
    monkeypatch.setattr(jr, "_require_arms", lambda *a, **k: None)
    monkeypatch.setattr(jr, "pairwise_judge", lambda args: (object(), "judge-label"))
    monkeypatch.setattr(pw, "pairwise_jobs", lambda *a, **k: None)
    monkeypatch.setattr(pw, "run_arm_pairwise", lambda *a, **k: _AllFailed())
    out = tmp_path / "pairwise.json"
    args = cli.build_parser().parse_args(
        ["eval", "score", "pairwise", "--answers", str(answers), "--output", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert out.exists()  # the reasons are written before the exit
    assert "Error: all 2 comparison(s) failed" in capsys.readouterr().err

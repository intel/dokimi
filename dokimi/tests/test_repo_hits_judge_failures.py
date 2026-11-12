# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo hits judge --json` when the run fails after its paid calls: the cost of those calls is reported, not $0.

The helpers and the judge fixture are those of test_repo_hits_judge.py."""

from __future__ import annotations

import json

from dokimi.artifacts import schema_errors
from tests.test_repo_hits_judge import JUDGE, _batch, _f, _put, _run, judge_llm  # noqa: F401 (the fixture)


def test_a_crash_after_the_judge_calls_with_json_prints_an_infra_document_with_their_cost(tmp_path, capsys, judge_llm,  # noqa: F811 (the fixture)
                                                                                         monkeypatch):
    # a crash outside the judge's own exits (here in summary) still reports the calls it paid for, not $0
    import dokimi.repos.hits_judge as hits_judge

    batch = _batch(tmp_path)
    _put(batch, "guidance:raw", 3, [_f(30, "mul adds")])

    def broken(*args, **kwargs):
        raise RuntimeError("summary broke")

    monkeypatch.setattr(hits_judge, "summary", broken)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--json")
    doc = json.loads(out)  # exactly one document, with the crash's infra error and the cost of the calls made before it
    assert code == 5 and (doc["status"], doc["error"]["code"], doc["error"]["retryable"]) == ("failed", "infra", True)
    assert doc["cost"]["calls"] > 0 and doc["cost"]["priced"] is True and doc["cost"]["spent_usd"] > 0
    assert schema_errors("cli_output.v1", doc) == []


def test_an_input_error_after_the_judge_calls_with_json_prints_an_input_document_with_their_cost(tmp_path, capsys,
                                                                                               judge_llm, monkeypatch):  # noqa: F811
    # an InputError raised after the paid calls (here in summary) exits 2 with its document, and the cost of the calls
    import dokimi.repos.hits_judge as hits_judge
    from dokimi.utils import InputError

    batch = _batch(tmp_path)
    _put(batch, "guidance:raw", 3, [_f(30, "mul adds")])

    def refused(*args, **kwargs):
        raise InputError("labels file is not a list")

    monkeypatch.setattr(hits_judge, "summary", refused)
    code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--json")
    doc = json.loads(out)
    assert code == 2 and (doc["status"], doc["error"]["code"], doc["error"]["retryable"]) == ("failed", "input", False)
    assert doc["error"]["message"] == "labels file is not a list"
    assert doc["cost"]["calls"] > 0 and doc["cost"]["priced"] is True
    assert schema_errors("cli_output.v1", doc) == []

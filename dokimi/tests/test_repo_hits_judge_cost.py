# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judge's --json cost under --max-usd, split out of test_repo_hits_judge.py (the file is at its line cap)."""

from __future__ import annotations

import json

from dokimi import caching, llm
from dokimi.artifacts import schema_errors
from tests.test_repo_hits_judge import UNPRICED_STOP, JUDGE, _batch, _reply, _run


def test_an_unpriced_judge_under_max_usd_json_has_no_remaining_budget_and_gives_the_stop_as_reason(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    batch = _batch(tmp_path)
    with llm.use_adapter(llm.FakeAdapter(respond=_reply)):  # no cost_usd: the judge has no known price
        code, out, _ = _run(capsys, "judge", str(batch), "--judge", JUDGE, "--max-usd", "0.5", "--json")
    doc = json.loads(out)
    assert code == 1 and (doc["status"], doc["reason"]) == ("budget_stopped", UNPRICED_STOP)
    assert (doc["cost"]["remaining_usd"], doc["cost"]["priced"]) == (None, False)  # not "the full budget left"
    assert schema_errors("cli_output.v1", doc) == []

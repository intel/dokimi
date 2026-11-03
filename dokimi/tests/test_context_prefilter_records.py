# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context prefilter`: which rows are claims, which records are the user's words, and when user_overrides fires."""

from __future__ import annotations

import pytest

from tests.test_context_prefilter import CI, _prompt, _report, _row, _session



@pytest.mark.parametrize(("corrected", "plain", "flagged"),
                         [(1, 0, False), (1, 1, True), (1, 2, False), (2, 0, True), (2, 2, True)])
def test_user_overrides_needs_two_episodes_and_corrections_on_half_of_them(corrected, plain, flagged):
    pushback = [_session(_prompt("push the branch"), _prompt("no, the branch must not go before ci"), name=f"c{i}")
                for i in range(corrected)]
    agreed = [_session(_prompt("push the branch"), _prompt("thanks, carry on"), name=f"p{i}") for i in range(plain)]
    row = _row(_report(pushback + agreed), CI)
    assert row["episodes"] == corrected + plain and row["corrected"] == corrected
    assert any(f.startswith("user_overrides") for f in row["flags"]) is flagged

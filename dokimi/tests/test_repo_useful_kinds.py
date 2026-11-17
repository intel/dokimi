# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The first useful result counts only the events of a known kind, as ``tool_calls`` does."""

from __future__ import annotations

from dokimi.repos.process_metrics import process_metrics
from dokimi.repos.useful import first_useful
from dokimi.repos.useful_spec import parse_useful

TEST_CMD = r"pytest tests/test_x\.py"


def _command(text: str, rc: int | None, t: float | None = None) -> dict:
    return {"t": t, "kind": "command", "text": text, "rc": rc}


def test_an_unknown_kind_is_not_a_step_and_steps_agree_with_tool_calls():
    events = [_command("ls", 0, t=1.0), {"t": 1.5, "kind": "probe", "name": "p", "v": 1},
              _command("pytest tests/test_x.py", 0, t=2.0)]
    assert first_useful(parse_useful({"command": TEST_CMD}), events)["steps"] == 2
    assert process_metrics(events, lambda _: False)["tool_calls"] == 2

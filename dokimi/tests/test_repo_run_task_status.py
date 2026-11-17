# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The host writes a task's status into result.json, the key the report reads."""

from __future__ import annotations

import json

from tests.test_repo_run import _run, setup  # noqa: F401  # the fixture and the runner of test_repo_run

def test_the_host_writes_the_tasks_status_into_result_json(setup):
    # the report reads the status from this key only: a run that drops the write shows every task as unknown
    task_json = setup["root"] / ".agent-eval/tasks/fix_add/task.json"
    task = json.loads(task_json.read_text())
    task["provenance"] = {"source": "hand-written", "status": "validated"}
    task_json.write_text(json.dumps(task))
    _run(setup, "--arm", "guidance:raw", source=str(setup["root"]))
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["task_status"] == "validated"


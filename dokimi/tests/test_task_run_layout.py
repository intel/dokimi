# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from dokimi.harnesses.runner import TaskSuiteRunner
from dokimi.harnesses.tasks import load_task
from tests.test_task_harnesses import _fixture_task


@pytest.mark.parametrize("repeats", [1, 3])
def test_command_run_output_layout_is_the_same_for_all_repeat_counts(tmp_path, repeats):
    task = load_task(_fixture_task(tmp_path))
    suite = TaskSuiteRunner(
        tasks=[task], harnesses=["fixture"], command_template="fake {task_path}",
        output_root=tmp_path / "runs", repeats=repeats, dry_run=True,
    )
    rows = suite.run()["results"]
    assert [Path(row["output_dir"]) for row in rows] == [
        suite.output_root / task.name / "fixture" / f"run{index}" for index in range(1, repeats + 1)
    ]

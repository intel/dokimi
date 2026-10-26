# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The docker-* task container's name is unique per cell, not per process."""

from __future__ import annotations

from dokimi.harnesses import load_task
from dokimi.harnesses.registry import create_harness
from docker_cli_fake import DockerCLI


def test_two_runs_of_one_task_in_one_process_get_their_own_container(tmp_path):
    # A suite runs every harness cell in one process: a pid-only name made the
    # second cell's `docker rm -f` remove the first cell's container.
    task_dir = tmp_path / "t"
    for d in ("tests", "environment", "solution"):
        (task_dir / d).mkdir(parents=True)
    (task_dir / "instruction.md").write_text("x", encoding="utf-8")
    (task_dir / "task.toml").write_text("[metadata]\ntags = []\n", encoding="utf-8")
    task = load_task(task_dir)
    names = set()
    for arm in ("a", "b"):
        cli = DockerCLI()
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / arm)
        (name,) = {a[a.index("--name") + 1] for a in cli.args if a[0] == "run"}
        # every `docker rm -f` of this arm must name this arm's container
        assert {a[-1] for a in cli.args if a[0] == "rm"} == {name}
        names.add(name)
    assert len(names) == 2 and all(n.startswith(f"dokimi-run-{task.name}-") for n in names)

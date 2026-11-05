# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A command arm's prompt is the task instruction as written, with no dokimi wrapper."""

from __future__ import annotations

from dokimi.harnesses.command import CommandHarness
from dokimi.harnesses.tasks import TaskSpec


def test_command_prompt_is_the_raw_instruction(tmp_path):
    task = TaskSpec(name="toy", path=tmp_path / "toy", instruction="Fix the off-by-one in sum().")
    harness = CommandHarness("fake", "echo {prompt_quoted}")
    argv = harness.build_command(task, model=None, output_dir=tmp_path / "out")
    assert argv == ["echo", "Fix the off-by-one in sum()."]


def test_every_prompt_placeholder_keeps_a_multiline_instruction_as_written(tmp_path):
    instruction = "Fix {x}.\nSecond line.\n"
    task = TaskSpec(name="toy", path=tmp_path / "toy", instruction=instruction)

    def argv(template):
        harness = CommandHarness("fake", template)
        return harness.build_command(task, model=None, output_dir=tmp_path / "out")

    assert argv("echo '{prompt}'") == ["echo", instruction]
    assert argv("echo {prompt_quoted}") == ["echo", instruction]
    assert argv("echo '{prompt_escaped}'") == ["echo", "Fix {{x}}.\nSecond line.\n"]

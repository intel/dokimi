# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``process.guidance_read`` in ``result.json`` after a fake run: only guidance files the workspace held (contract §4.1)."""

from __future__ import annotations

import json

from test_repo_run import _run, setup  # noqa: F401  # the run fixture, with its helpers


def _process(s, arm: str) -> dict:
    cell = f"fix_add__{arm.replace(':', '-')}__m-1__r1"
    return json.loads((s["out"] / "b1" / cell / "result.json").read_text())["process"]


def test_guidance_read_lists_only_the_guidance_files_the_workspace_held(setup, monkeypatch):  # noqa: F811
    # One Bash call of four reads gives each rc 0, since a failure does not say which file failed (contract §4.1).
    call = "cat sub/AGENTS.md; ls sub/x/; cat sub/x/AGENTS.md 2>/dev/null; cat sub/x/y/AGENTS.md 2>/dev/null"
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([["Bash", {"command": call}]]))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert _process(setup, "guidance:raw")["guidance_read"] == ["sub/AGENTS.md"]


def test_guidance_read_leaves_out_a_file_the_arm_removed(setup, monkeypatch):  # noqa: F811
    calls = [["Bash", {"command": "cat sub/AGENTS.md"}], ["Bash", {"command": "cat AGENTS.md"}]]
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps(calls))
    _run(setup, "--arm", "guidance:none", "--task", "fix_add")
    assert _process(setup, "guidance:none")["guidance_read"] == []

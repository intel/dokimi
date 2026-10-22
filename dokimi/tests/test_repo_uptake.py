# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Uptake of what a `patch:` arm added (#41 D4): prep records it, the runner counts it, report and compare show it.
No docker, no LLM."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.repos.compare import compare, format_compare
from dokimi.repos.report import format_report, summarize
from dokimi.repos.uptake import arm_uptake
from dokimi.repos.workspace import parse_arm
from test_repo_arms import setup, toy, _prep  # noqa: F401  # fixtures

KIT_PATCH = """diff --git a/tools/fmt b/tools/fmt
new file mode 100755
--- /dev/null
+++ b/tools/fmt
@@ -0,0 +1,2 @@
+#!/bin/sh
+exec clang-format -i "$@"
diff --git a/.mcp.json b/.mcp.json
new file mode 100644
--- /dev/null
+++ b/.mcp.json
@@ -0,0 +1 @@
+{"mcpServers": {"code.graph": {"command": "cg-mcp"}}}
diff --git a/sub/AGENTS.md b/sub/AGENTS.md
--- a/sub/AGENTS.md
+++ b/sub/AGENTS.md
@@ -1 +1,2 @@
 # sub guidance
+Format with `tools/fmt`; ask the code.graph tools who calls a function.
diff --git a/src/AGENTS.md b/src/AGENTS.md
new file mode 100644
--- /dev/null
+++ b/src/AGENTS.md
@@ -0,0 +1 @@
+# src guidance
"""

WS = "/eval/run/workspace"


def _cmd(text):
    return {"kind": "command", "text": text, "rc": 0}


@pytest.mark.parametrize("text", ["tools/fmt src/a.cpp", "./tools/fmt src/a.cpp", "sh tools/fmt", "x=1; tools/fmt",
                                  f"{WS}/tools/fmt a", "bash -c 'tools/fmt a'", "(tools/fmt)"])
def test_a_command_that_names_an_added_file_is_a_use(text):
    assert arm_uptake([_cmd(text)], ["tools/fmt"], [], WS)["commands"] == 1


@pytest.mark.parametrize("text", ["tools/fmt2 a", "mytools/fmt a", "cd tools && ./fmt a", "tools/fmt.bak",
                                  "/elsewhere/tools/fmt"])
def test_a_command_that_does_not_name_an_added_file_is_not(text):
    assert arm_uptake([_cmd(text)], ["tools/fmt"], [], WS)["commands"] == 0


def test_reads_and_calls_of_the_arms_mcp_servers_count_others_do_not():
    events = [{"kind": "read", "path": "tools/fmt"}, {"kind": "read", "path": "src/a.cpp"},
              {"kind": "tool", "name": "mcp__code_graph__callers"}, {"kind": "tool", "name": "mcp__other__x"},
              {"kind": "tool", "name": "Grep"}]
    got = arm_uptake(events, ["tools/fmt"], ["code.graph"], WS)
    assert (got["reads"], got["mcp_calls"], got["commands"], got["used"], got["measured"]) == (1, 1, 0, True, True)
    assert arm_uptake([], ["tools/fmt"], [], WS)["used"] is False
    unmeasured = arm_uptake(None, ["tools/fmt"], [], WS)
    assert unmeasured["measured"] is False and unmeasured["used"] is False


def test_prep_records_the_added_files_that_are_not_guidance_and_the_mcp_servers(toy):  # noqa: F811
    patch = toy["tmp"] / "kit.diff"
    patch.write_text(KIT_PATCH)
    meta, ws = _prep(toy, parse_arm(f"patch:{patch}"))
    assert meta["arm_files"] == [".mcp.json", "src/AGENTS.md", "sub/AGENTS.md", "tools/fmt"]
    # a changed guidance file and an added one are loaded, not used; the rest is what the agent can use
    assert meta["arm_patch"]["added"] == [".mcp.json", "tools/fmt"]
    assert meta["arm_patch"]["mcp_servers"] == ["code.graph"]


def _mcp_link_patch(target, extra=""):
    return ("diff --git a/.mcp.json b/.mcp.json\nnew file mode 120000\n--- /dev/null\n+++ b/.mcp.json\n"
            f"@@ -0,0 +1 @@\n+{target}\n\\ No newline at end of file\n{extra}")


@pytest.mark.parametrize("where", ["host", "loop"])
def test_a_mcp_json_linked_out_of_the_workspace_or_in_a_loop_is_not_read(toy, where):  # noqa: F811
    host = toy["tmp"] / "host.json"  # outside the workspace: what the agent's harness would not find
    host.write_text('{"mcpServers": {"host-only": {}}}')
    patch = toy["tmp"] / "link.diff"
    patch.write_text(_mcp_link_patch(host if where == "host" else ".mcp.json"))
    meta, ws = _prep(toy, parse_arm(f"patch:{patch}"))
    assert (ws / ".mcp.json").is_symlink() and meta["arm_patch"]["mcp_servers"] == []


def test_a_mcp_json_linked_inside_the_workspace_names_its_servers(toy):  # noqa: F811
    patch = toy["tmp"] / "link.diff"
    patch.write_text(_mcp_link_patch("tools/mcp.json", "diff --git a/tools/mcp.json b/tools/mcp.json\n"
                                     "new file mode 100644\n--- /dev/null\n+++ b/tools/mcp.json\n@@ -0,0 +1 @@\n"
                                     '+{"mcpServers": {"code.graph": {}}}\n'))
    meta, _ = _prep(toy, parse_arm(f"patch:{patch}"))
    assert meta["arm_patch"]["mcp_servers"] == ["code.graph"]


def _run(s, batch, *arms):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", batch, "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), "--agent-network", "host", "--task", "fix_add",
            "--reps", "2", *[x for a in arms for x in ("--arm", a)]]
    args = build_parser().parse_args(argv)
    args.func(args)


def test_repo_run_records_uptake_and_compare_warns_when_nothing_was_used(setup, monkeypatch):  # noqa: F811
    patch = setup["tmp"] / "kit.diff"
    patch.write_text(KIT_PATCH)
    arm = str(parse_arm(f"patch:{patch}"))
    _run(setup, "unused", "guidance:raw", f"patch:{patch}")
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([["Bash", {"command": "./tools/fmt src/calc.py"}],
                                                     ["mcp__code_graph__callers", {"symbol": "add"}]]))
    _run(setup, "used", "guidance:raw", f"patch:{patch}")

    used = setup["out"] / "used"
    result = json.loads(next(used.glob("*patch*__r1/result.json")).read_text())
    assert result["uptake"] == {"files": [".mcp.json", "tools/fmt"], "mcp_servers": ["code.graph"], "measured": True,
                                "commands": 1, "reads": 0, "mcp_calls": 1, "used": True}
    assert "uptake" not in json.loads(next(used.glob("*guidance-raw__*__r1/result.json")).read_text())
    assert "arm's additions used in 2/2 cell(s)" in format_report(summarize(used))

    unused = setup["out"] / "unused"
    assert "arm's additions used in 0/2 cell(s)" in format_report(summarize(unused))
    quiet = compare([used], "guidance:raw", [arm])
    assert quiet["uptake"] == {arm: {"cells": 2, "used": 2, "unmeasured": 0}}
    assert not [w for w in quiet["warnings"] if "used none" in w]
    loud = compare([unused], "guidance:raw", [arm])
    assert [w for w in loud["warnings"] if "used none" in w] == [
        f"{arm}: the agent used none of what the arm added in 2 cell(s); a delta measures the patch's text, "
        "not the tool"]
    assert f"uptake: {arm} used what its arm added in 0/2 cell(s)" in format_compare(loud)

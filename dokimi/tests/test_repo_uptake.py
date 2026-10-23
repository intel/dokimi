# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Uptake of what a `patch:` arm added (#41 D4): prep records it, the runner counts it, report and compare show it.
No docker, no LLM."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.repos.compare import compare, format_compare
from dokimi.repos.events import convert
from dokimi.repos.report import format_report, summarize, unmeasured_note
from dokimi.repos.uptake import arm_uptake, mcp_servers
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

# Each half of KIT_PATCH alone: a server and no file an agent can use, and the reverse.
SERVER_ONLY_PATCH = KIT_PATCH[KIT_PATCH.index("diff --git a/.mcp.json"):KIT_PATCH.index("diff --git a/sub/AGENTS.md")]
FILE_ONLY_PATCH = KIT_PATCH[:KIT_PATCH.index("diff --git a/.mcp.json")]

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


def test_a_call_the_harness_denied_is_not_a_use():
    def use(i, name, args):
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": name, "input": args}]}}
    hook = "PreToolUse:Bash hook error: [python3 /eval/support/no_background.py]: run_in_background is refused"
    stream = [{"type": "system", "subtype": "init", "model": "m-1"},
              use("b1", "Bash", {"command": "./tools/fmt src/calc.py", "run_in_background": True}),
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b1", "is_error": True,
                                                        "content": hook}]}},
              use("m1", "mcp__code_graph__callers", {"symbol": "add"}),
              {"type": "result", "subtype": "success", "result": "done", "num_turns": 2,
               "permission_denials": [{"tool_use_id": "m1", "tool_name": "mcp__code_graph__callers"}]}]
    events = convert("claude-stream-json", "\n".join(json.dumps(r) for r in stream), WS).events
    assert [e.get("denied") for e in events] == [True, True]
    got = arm_uptake(events, ["tools/fmt"], ["code.graph"], WS)
    assert (got["commands"], got["mcp_calls"], got["used"], got["measured"]) == (0, 0, False, True)
    retried = arm_uptake([*events, _cmd("./tools/fmt src/calc.py")], ["tools/fmt"], ["code.graph"], WS)
    assert (retried["commands"], retried["used"]) == (1, True)


def test_prep_records_the_added_files_that_are_not_guidance_and_the_mcp_servers(toy):  # noqa: F811
    patch = toy["tmp"] / "kit.diff"
    patch.write_text(KIT_PATCH)
    meta, ws = _prep(toy, parse_arm(f"patch:{patch}"))
    assert meta["arm_files"] == [".mcp.json", "src/AGENTS.md", "sub/AGENTS.md", "tools/fmt"]
    # a changed guidance file and an added one are loaded, not used; the rest is what the agent can use
    assert meta["arm_patch"]["added"] == ["tools/fmt"]  # not .mcp.json: reading the config is not using a server
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


MIXED_PATCH = """diff --git a/src/calc.py b/src/calc.py
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,3 @@
+# checked
 def add(a, b):
     return a - b
diff --git a/.gitignore b/.gitignore
new file mode 100644
--- /dev/null
+++ b/.gitignore
@@ -0,0 +1 @@
+gen.sh
diff --git a/gen.sh b/gen.sh
new file mode 100755
--- /dev/null
+++ b/gen.sh
@@ -0,0 +1 @@
+#!/bin/sh
"""


def test_prep_records_as_added_a_file_gitignore_hides_not_a_file_the_patch_changed(toy):  # noqa: F811
    patch = toy["tmp"] / "mixed.diff"
    patch.write_text(MIXED_PATCH)
    meta, ws = _prep(toy, parse_arm(f"patch:{patch}"))
    assert meta["arm_files"] == [".gitignore", "gen.sh", "src/calc.py"] and (ws / "gen.sh").is_file()
    assert meta["arm_patch"]["added"] == [".gitignore", "gen.sh"]


def test_a_mcp_json_the_patch_did_not_write_names_no_server(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"from-base": {}}}')
    assert mcp_servers(tmp_path, ["tools/fmt"]) == [] and mcp_servers(tmp_path, [".mcp.json"]) == ["from-base"]


def _run(s, batch, *arms, model="m-1"):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", model, "--batch", batch, "--out", str(s["out"]),
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
    assert result["uptake"] == {"files": ["tools/fmt"], "mcp_servers": ["code.graph"], "measured": True,
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


@pytest.mark.parametrize("patch_text, files, servers, tool_use", [
    (SERVER_ONLY_PATCH, [], ["code.graph"], ["mcp__code_graph__callers", {"symbol": "add"}]),
    (FILE_ONLY_PATCH, ["tools/fmt"], [], ["Bash", {"command": "./tools/fmt src/calc.py"}]),
])
def test_a_patch_that_adds_only_a_server_or_only_a_file_records_its_uptake(
        setup, monkeypatch, patch_text, files, servers, tool_use):  # noqa: F811
    patch = setup["tmp"] / "half.diff"
    patch.write_text(patch_text)
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([tool_use]))
    _run(setup, "half", f"patch:{patch}")
    uptake = json.loads(next((setup["out"] / "half").glob("*patch*__r1/result.json")).read_text())["uptake"]
    assert (uptake["files"], uptake["mcp_servers"], uptake["used"]) == (files, servers, True)


def _used_none(summary):
    return [w for w in summary["warnings"] if "used none" in w]


def test_an_agent_that_left_no_trace_is_unmeasured_one_that_called_no_tool_is_measured(setup, monkeypatch):  # noqa: F811
    patch = setup["tmp"] / "kit.diff"
    patch.write_text(KIT_PATCH)
    arm = str(parse_arm(f"patch:{patch}"))
    for action in ("silent", "idle"):
        monkeypatch.setenv("FAKE_ACTION", action)
        _run(setup, action, "guidance:raw", f"patch:{patch}")
    silent, idle = setup["out"] / "silent", setup["out"] / "idle"
    assert json.loads(next(silent.glob("*patch*__r1/result.json")).read_text())["uptake"]["measured"] is False
    assert json.loads(next(idle.glob("*patch*__r1/result.json")).read_text())["uptake"]["measured"] is True
    unmeasured = compare([silent], "guidance:raw", [arm])
    assert unmeasured["uptake"] == {arm: {"cells": 0, "used": 0, "unmeasured": 2}} and not _used_none(unmeasured)
    measured = compare([idle], "guidance:raw", [arm])
    assert measured["uptake"] == {arm: {"cells": 2, "used": 0, "unmeasured": 0}} and _used_none(measured)


def test_compare_counts_the_uptake_of_the_models_it_compares(setup, monkeypatch):  # noqa: F811
    patch = setup["tmp"] / "kit.diff"
    patch.write_text(KIT_PATCH)
    arm = str(parse_arm(f"patch:{patch}"))
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([["Bash", {"command": "./tools/fmt src/calc.py"}]]))
    _run(setup, "m1", "guidance:raw", f"patch:{patch}", model="m-1")
    monkeypatch.delenv("FAKE_TOOL_USES")
    _run(setup, "m2", "guidance:raw", f"patch:{patch}", model="m-2")
    batches = [setup["out"] / "m1", setup["out"] / "m2"]
    both = compare(batches, "guidance:raw", [arm])
    assert both["uptake"] == {arm: {"cells": 4, "used": 2, "unmeasured": 0}} and not _used_none(both)
    m2 = compare(batches, "guidance:raw", [arm], models=["m-2"])
    assert m2["uptake"] == {arm: {"cells": 2, "used": 0, "unmeasured": 0}} and _used_none(m2)


@pytest.mark.parametrize("text", ["npm run lint", "docker run img", "git log -- build", "cat lint", "make build",
                                  "lint x", "cd d && lint", "run", "x | lint", "(lint)", ";lint"])
def test_a_root_level_added_file_is_not_named_by_a_common_word_of_a_command(text):
    for name in ("lint", "run", "build"):
        assert arm_uptake([_cmd(text)], [name], [], WS)["commands"] == 0


@pytest.mark.parametrize("text", ["./lint x", f"{WS}/lint", "x; ./lint", "sh lint", "bash ./lint a",
                                  "python3 lint"])
def test_a_root_level_added_file_is_named_by_a_path_or_as_a_command(text):
    assert arm_uptake([_cmd(text)], ["lint"], [], WS)["commands"] == 1


def test_a_read_alone_or_an_mcp_call_alone_is_a_use():
    read = arm_uptake([{"kind": "read", "path": "tools/fmt"}], ["tools/fmt"], [], WS)
    assert (read["reads"], read["mcp_calls"], read["commands"], read["used"]) == (1, 0, 0, True)
    call = arm_uptake([{"kind": "tool", "name": "mcp__code_graph__x"}], [], ["code.graph"], WS)
    assert (call["mcp_calls"], call["reads"], call["commands"], call["used"]) == (1, 0, 0, True)
    other = arm_uptake([{"kind": "tool", "name": "mcp__code_graph2__x"}], [], ["code.graph"], WS)
    assert (other["mcp_calls"], other["used"]) == (0, False)


def test_a_mcp_json_the_patch_changed_names_only_the_servers_it_added(toy):  # noqa: F811
    from dokimi.repos.workspace import _apply_patch, _git, _git_env

    ws, env = toy["tmp"] / "w", _git_env(("t", "t@example.com"))
    ws.mkdir()
    (ws / ".mcp.json").write_text('{"mcpServers": {"github": {}}}\n')
    _git(ws, env, "init", "-q")
    patch = toy["tmp"] / "mcp.diff"
    patch.write_text("diff --git a/.mcp.json b/.mcp.json\n--- a/.mcp.json\n+++ b/.mcp.json\n@@ -1 +1 @@\n"
                     '-{"mcpServers": {"github": {}}}\n+{"mcpServers": {"github": {}, "code.graph": {}}}\n')
    _, meta = _apply_patch(ws, env, toy["manifest"], parse_arm(f"patch:{patch}"))
    assert meta["arm_patch"]["mcp_servers"] == ["code.graph"] and meta["arm_patch"]["added"] == []


def test_the_text_report_and_compare_show_the_cells_that_could_not_be_measured():
    assert unmeasured_note({"cells": 0, "used": 0, "unmeasured": 2}) == " (warning: 2 more cell(s) left no trace to measure)"
    assert unmeasured_note({"cells": 2, "used": 1, "unmeasured": 0}) == ""

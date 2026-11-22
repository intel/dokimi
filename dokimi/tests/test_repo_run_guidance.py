# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``process.guidance_read`` in ``result.json`` after a fake run: only guidance files the workspace held (contract §4.1)."""

from __future__ import annotations

import json
import ntpath
import subprocess
from types import SimpleNamespace

from dokimi.repos import held_guidance as held_module
from dokimi.repos import parse_manifest
from dokimi.repos.held_guidance import HeldGuidance, held_guidance
from dokimi.repos.runner import run_process_metrics
from test_repo_manifest import MANIFEST
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


def test_guidance_searched_lists_the_held_guidance_files_a_grep_showed_lines_of(setup, monkeypatch):  # noqa: F811
    # a file that never existed, and one the arm removed (guidance:none), are not listed; a read stays a read
    call = "grep -n guidance sub/AGENTS.md sub/gone/AGENTS.md; cat AGENTS.md"
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([["Bash", {"command": call}]]))
    _run(setup, "--arm", "guidance:raw,guidance:none", "--task", "fix_add")
    raw, none = _process(setup, "guidance:raw"), _process(setup, "guidance:none")
    assert (raw["guidance_searched"], raw["guidance_read"]) == (["sub/AGENTS.md"], ["AGENTS.md"])
    assert (none["guidance_searched"], none["guidance_read"]) == ([], [])


def _held_workspace(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "docs").mkdir(parents=True)
    (ws / "sub").mkdir()
    (ws / "AGENTS.md").write_text("a\n")
    (ws / "docs/AGENTS.md").write_text("d\n")
    (ws / "alias").symlink_to("docs", target_is_directory=True)  # git lists alias, not alias/AGENTS.md
    (ws / "sub/AGENTS.md").symlink_to("missing.md")  # dangling
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    return held_guidance(tmp_path, parse_manifest(MANIFEST))


def test_a_path_through_a_link_to_a_held_guidance_file_is_held_and_a_dangling_link_is_not(tmp_path):
    held = _held_workspace(tmp_path)
    assert "alias/AGENTS.md" in held and "docs/AGENTS.md" in held and "AGENTS.md" in held
    assert "sub/AGENTS.md" not in held  # its target never existed
    assert "docs/new/AGENTS.md" not in held and "../AGENTS.md" not in held  # made later, or outside the workspace


def test_a_link_that_loops_through_a_dotdot_component_leads_to_no_held_file(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs/AGENTS.md").write_text("d\n")
    (ws / "alias").symlink_to("alias/../docs", target_is_directory=True)  # each pass of the pending ``..`` returns to docs
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    held = held_guidance(tmp_path, parse_manifest(MANIFEST))
    assert held.resolved("alias/AGENTS.md") is None
    assert "alias/AGENTS.md" not in held and "docs/AGENTS.md" in held


def test_a_link_that_goes_up_from_a_name_that_was_no_directory_leads_to_no_held_file(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs/AGENTS.md").write_text("d\n")
    (ws / "dangling").symlink_to("missing/../docs", target_is_directory=True)  # open() fails on ``missing``
    (ws / "afile").symlink_to("docs/AGENTS.md/../../docs")  # AGENTS.md is a file: ENOTDIR
    (ws / "fine").symlink_to("docs/../docs", target_is_directory=True)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    held = held_guidance(tmp_path, parse_manifest(MANIFEST))
    (ws / "missing").mkdir()  # made later: the start decides
    assert "dangling/AGENTS.md" not in held and "afile/AGENTS.md" not in held
    assert "fine/AGENTS.md" in held and "docs/AGENTS.md" in held
    ctx = SimpleNamespace(manifest=parse_manifest(MANIFEST))
    command = {"kind": "command", "text": "grep x docs/AGENTS.md dangling/AGENTS.md", "rc": 0,
               "searched": ["docs/AGENTS.md", "dangling/AGENTS.md"]}
    got = run_process_metrics(ctx, [command, {"kind": "read", "path": "dangling/AGENTS.md", "rc": 0}], 0, held)
    assert (got["guidance_searched"], got["guidance_read"]) == (["docs/AGENTS.md"], [])


def test_a_link_the_agent_removed_or_retargeted_still_leads_to_the_file_the_workspace_held(tmp_path):
    held = _held_workspace(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "alias").unlink()
    (ws / "alias").mkdir()  # the agent made a directory of its own where the link was
    (ws / "alias/AGENTS.md").write_text("mine\n")
    assert "alias/AGENTS.md" in held and "docs/AGENTS.md" in held
    ctx = SimpleNamespace(manifest=parse_manifest(MANIFEST))
    events = [{"kind": "read", "path": "alias/AGENTS.md", "rc": 0},
              {"kind": "command", "text": "grep x alias/AGENTS.md", "rc": 0, "searched": ["alias/AGENTS.md"]}]
    got = run_process_metrics(ctx, events, 0, held)
    assert (got["guidance_read"], got["guidance_searched"]) == (["alias/AGENTS.md"], ["alias/AGENTS.md"])


def test_a_windows_alias_leads_to_the_held_file_through_the_drive(monkeypatch):
    root = "C:\\repo\\workspace"
    monkeypatch.setattr(held_module, "os", SimpleNamespace(path=ntpath, sep="\\", altsep="/"))  # the path rules of Windows
    held = object.__new__(HeldGuidance)
    held.root = root
    held.links = {ntpath.normcase(root + "\\" + name): target for name, target in {
        "alias": "docs", "abs": root + "\\docs", "rooted": "\\repo\\workspace\\docs", "dots": "..\\workspace\\docs",
        "loop": "loop", "ext": "D:\\external"}.items()}
    held.dirs = frozenset({ntpath.normcase(root), ntpath.normcase(root + "\\docs")})
    held.files = frozenset({ntpath.normcase("docs/AGENTS.md")})
    for path in ("alias/AGENTS.md", "abs/AGENTS.md", "rooted/AGENTS.md", "dots/AGENTS.md", "alias\\AGENTS.md", "docs/AGENTS.md"):
        assert path in held
    for path in ("DOCS/AGENTS.md", "docs/agents.md", "ALIAS/AGENTS.md", "Alias\\Agents.md", "Dots/AGENTS.md"):
        assert path in held  # Windows folds the case of a name
    assert "other/AGENTS.md" not in held and "loop/AGENTS.md" not in held
    assert held.resolved("alias/AGENTS.md") == "docs/AGENTS.md"
    assert held.resolved("ext/AGENTS.md") is None and "ext/AGENTS.md" not in held  # D: has no path from C:\repo\workspace


def test_the_metrics_count_a_search_and_a_read_through_a_directory_link_but_not_a_dangling_one(tmp_path):
    ctx = SimpleNamespace(manifest=parse_manifest(MANIFEST))
    events = [{"kind": "read", "path": "alias/AGENTS.md", "rc": 0},
              {"kind": "command", "text": "grep x AGENTS.md sub/AGENTS.md", "rc": 0, "searched": ["AGENTS.md", "sub/AGENTS.md"]},
              {"kind": "read", "path": "sub/AGENTS.md", "rc": 0}]
    got = run_process_metrics(ctx, events, 0, _held_workspace(tmp_path))
    assert (got["guidance_read"], got["guidance_searched"]) == (["alias/AGENTS.md"], ["AGENTS.md"])


def test_guidance_read_leaves_out_a_file_the_arm_removed(setup, monkeypatch):  # noqa: F811
    calls = [["Bash", {"command": "cat sub/AGENTS.md"}], ["Bash", {"command": "cat AGENTS.md"}]]
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps(calls))
    _run(setup, "--arm", "guidance:none", "--task", "fix_add")
    assert _process(setup, "guidance:none")["guidance_read"] == []

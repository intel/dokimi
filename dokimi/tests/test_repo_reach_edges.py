# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Edge cases of the L1 reach probe found in review (no LLM; the fake harness of test_repo_reach)."""

from __future__ import annotations

import sys

import pytest
from tests.test_repo_reach import _profile, _reach, _repo, _write

from dokimi import process
from dokimi.repos import SourceError, WorkTree, load_manifest
from dokimi.repos.events import OUTPUT_KEEP, clip_output
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.reach import (
    ReachError,
    build_workspace,
    distinct_salt,
    prose_words,
    resolve_links,
    run_reach,
    scenarios,
    token,
)


def test_a_directory_named_root_gets_its_own_scenario():
    ids = [s.id for s in scenarios(["CLAUDE.md", "root/CLAUDE.md"], ("root", "cwd"))]
    assert ids == ["cwd=.", "cwd=root"]
    assert len(set(ids)) == len(ids)


def test_prose_counts_words_not_formatting():
    assert prose_words("`RC111111`\n`RC222222`\n`RC333333`\n`RC444444`\n`RC555555`") == 0
    assert prose_words("1. RC111111\n2. RC222222\n3) RC333333\n```\nRC444444\n```") == 0
    assert prose_words("NONE") == 0
    assert prose_words("I will not list these tokens.") == 5


def test_unreadable_guidance_is_a_reach_error(tmp_path, monkeypatch):
    original = WorkTree.read_text

    def broken(self, path):
        if path == "sub/AGENTS.md":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return original(self, path)

    monkeypatch.setattr(WorkTree, "read_text", broken)
    with pytest.raises(ReachError, match="cannot read guidance file sub/AGENTS.md"):
        _reach(tmp_path, monkeypatch, kinds=("root",))


def test_old_traces_are_removed(tmp_path, monkeypatch):
    stale = tmp_path / "out" / "traces" / "07-cwd_gone.stdout"
    _write(tmp_path, "out/traces/07-cwd_gone.stdout", "from an earlier run\n")
    _write(tmp_path, "out/traces/notes.txt", "not ours\n")
    _write(tmp_path, "out/traces/104-read_gone.events.jsonl", "{}\n")  # {n:02d} widens past 99 scenarios
    _reach(tmp_path, monkeypatch, kinds=("root",))
    names = sorted(p.name for p in (tmp_path / "out" / "traces").iterdir())
    assert not stale.exists() and "104-read_gone.events.jsonl" not in names
    assert "notes.txt" in names and any(n.startswith("00-") for n in names)


def test_salt_is_changed_until_every_token_is_distinct(monkeypatch):
    tree = type("T", (), {"read_text": lambda self, p: "mentions RCAAAAAA already\n"})()
    real = token

    def colliding(salt, path, position):
        if salt == "s":  # first salt: two files share a token
            return "RCBBBBBB" if position == "head" else real(salt, path, position)
        if salt == "s-1":  # second: a token the guidance already contains
            return "RCAAAAAA" if (path, position) == ("a.md", "tail") else real(salt, path, position)
        return real(salt, path, position)

    monkeypatch.setattr("dokimi.repos.reach.token", colliding)
    assert distinct_salt(tree, ["a.md", "b.md"], "s") == "s-2"


def test_clip_counts_the_bytes_really_omitted():
    text = "a" + "é" * 6000  # 2 bytes each, after one 1-byte character: the head cut splits an é
    clipped, truncated = clip_output(text)
    head, rest = clipped.split("\n[... ", 1)
    omitted = int(rest.split(" bytes omitted", 1)[0])
    tail = rest.split("...]\n", 1)[1]
    assert truncated and len(head.encode()) == OUTPUT_KEEP - 1
    assert omitted == len(text.encode()) - len(head.encode()) - len(tail.encode())


def test_a_harness_that_drops_tokens_gives_truncated_cells(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch, mode="truncate", kinds=("root",))
    classes = {r["file"]: r["class"] for r in payload["records"]}
    assert classes["CLAUDE.md"] == "truncated"  # the fake reports every other token


def test_a_link_into_git_or_to_nothing_is_not_probed(tmp_path, monkeypatch):
    tree = type("T", (), {"resolve": lambda self, path: ".git/config" if path == "a.md" else path})()
    assert resolve_links(tree, ["a.md", "b.md"]) == (
        {"b.md": []}, [{"path": "a.md", "reason": "a.md links to an unsafe path: .git/config"}])

    def dangling(self, path):
        raise SourceError(f"{path} links to nothing: gone.md")

    monkeypatch.setattr(WorkTree, "resolve", dangling)
    with pytest.raises(ReachError, match="each is a link that cannot be followed: .* links to nothing"):
        _reach(tmp_path, monkeypatch, kinds=("root",))


def test_a_timed_out_scenario_keeps_its_trace_up_to_the_kill(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch, mode="line_then_hang", kinds=("root",), timeout_s=1)
    assert payload["scenarios"][0]["error"].startswith("TimeoutExpired")
    assert '"claude_code_version": "9.8.7"' in (tmp_path / "out/traces/00-cwd_..stdout").read_text(encoding="utf-8")


def test_a_trace_over_the_capture_limit_is_kept_and_parsed_whole(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch, mode="big", kinds=("root",))
    assert payload["scenarios"][0]["error"] is None
    assert {r["class"] for r in payload["records"] if r["file"] == "CLAUDE.md"} == {"loaded"}
    trace = (tmp_path / "out/traces/00-cwd_..stdout").read_text(encoding="utf-8")
    assert len(trace) > process.CAPTURE_LIMIT and "x" * (3 << 19) in trace
    assert "not kept" not in trace


def test_a_missing_harness_binary_is_an_error_cell(tmp_path, monkeypatch):
    missing = str(tmp_path / "no-such-harness")
    profile = _profile(tmp_path, invoke=[missing, "-p", "{prompt}", "--model", "{model}", "{tools}"],
                       version_cmd=[sys.executable, "-c", "print('9.8.7')"])
    tree = WorkTree(_repo(tmp_path))
    (tmp_path / "work").mkdir()
    payload = run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                        profile=load_profile(profile), model="m", out_dir=tmp_path / "out",
                        work_dir=tmp_path / "work", salt="s", kinds=("root",))
    assert payload["scenarios"][0]["error"].startswith(("FileNotFoundError", "PermissionError"))
    assert missing in payload["scenarios"][0]["error"]
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert (tmp_path / "out/traces/00-cwd_..stdout").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("answer, needle", [
    (lambda: process.reply(stderr="fatal: cannot init\n", returncode=128), "git init .*: exit 128 fatal: cannot init"),
    (lambda: process.reply(timed_out=True), "git init .*: timed out"),
])
def test_a_failed_git_init_in_the_probe_workspace_is_a_reach_error(tmp_path, answer, needle):
    tree = WorkTree(_repo(tmp_path))
    (tmp_path / "ws").mkdir()
    runner = process.FakeRunner(lambda call: answer())
    with pytest.raises(ReachError, match=needle):
        build_workspace(tree, ["CLAUDE.md"], tmp_path / "ws", "s", runner=runner)
    assert len(runner.calls) == 1


def test_git_that_cannot_start_in_the_probe_workspace_is_a_reach_error(tmp_path):
    def missing(call):
        raise FileNotFoundError(call.argv[0])

    tree = WorkTree(_repo(tmp_path))
    (tmp_path / "ws").mkdir()
    with pytest.raises(ReachError, match="git init in the probe workspace failed: .*git"):
        build_workspace(tree, ["CLAUDE.md"], tmp_path / "ws", "s", runner=process.FakeRunner(missing))

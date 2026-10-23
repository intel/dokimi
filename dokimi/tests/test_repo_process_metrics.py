# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Process metrics from ``events.jsonl`` (contract §4.1), and the manifest rules around them (§2)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dokimi.repos import WorkTree, check_repo, load_manifest, match_glob, parse_manifest
from dokimi.repos.manifest import TaskSummary, grade_problems
from dokimi.repos.process_metrics import parse_counter, process_metrics
from dokimi.repos.runner import run_process_metrics

from test_repo_manifest import MANIFEST, _repo, _task

EVENTS = [
    {"t": 1.0, "kind": "read", "path": "AGENTS.md"},
    {"t": 1.5, "kind": "read", "path": "src/AGENTS.md"},
    {"t": 1.6, "kind": "read", "path": "AGENTS.md"},
    {"t": 2.0, "kind": "read", "path": "src/lib.c"},
    {"t": 3.0, "kind": "command", "text": "bazel build //x", "rc": 0},
    {"t": 4.0, "kind": "command", "text": "bazelisk test //x", "rc": 1, "output": "ERROR: icpx is not found"},
    {"t": 5.0, "kind": "command", "text": "make -j8 &", "rc": None, "denied": True},
    {"t": 6.0, "kind": "command", "text": "ls", "rc": None},
    {"t": 7.0, "kind": "edit", "path": "src/lib.c"},
    {"t": 8.0, "kind": "tool", "name": "WebFetch", "denied": True},
]
GLOBS = ("**/AGENTS.md", ".github/instructions/*.md")


def _guidance(path: str) -> bool:
    return any(match_glob(g, path) for g in GLOBS)


def _counters(**raw):
    return {name: parse_counter(name, value) for name, value in raw.items()}


def test_built_in_counts():
    got = process_metrics(EVENTS, _guidance)
    assert got["tool_calls"] == 10
    assert got["commands"] == 4
    assert got["failed_commands"] == 1  # rc null (no result, or denied) is not a failure
    assert got["denied_commands"] == 1  # the denied WebFetch is a tool call, not a command
    assert got["guidance_read"] == ["AGENTS.md", "src/AGENTS.md"]  # matched by the globs, once each
    assert got["counters"] == {}


def test_no_events_counts_zero():
    assert process_metrics([], _guidance) == {"tool_calls": 0, "commands": 0, "failed_commands": 0,
                                              "denied_commands": 0, "guidance_read": [], "counters": {}}


def test_counters_search_text_output_or_both():
    counters = _counters(bazel=r"\bbazel(?:isk)?\b", icpx={"regex": "icpx is not found", "match": "output"},
                         icpx_in_text={"regex": "icpx"}, x_anywhere={"regex": r"//x|icpx", "match": "any"},
                         make=r"\bmake\b")
    got = process_metrics(EVENTS, _guidance, counters)["counters"]
    # a denied command still counts: the agent asked for it
    assert got == {"bazel": 2, "icpx": 1, "icpx_in_text": 0, "x_anywhere": 2, "make": 1}


@pytest.mark.parametrize("value, needle", [
    ("bazel(", "not a valid regex"),
    ({"regex": "[a-"}, "not a valid regex"),
    ({"regex": "x", "match": "stderr"}, "match must be one of"),
    ({"match": "text"}, "give a regex"),
    ("", "give a regex"),
    (3, "give a regex"),
])
def test_parse_counter_refuses(value, needle):
    with pytest.raises(ValueError, match=needle) as exc:
        parse_counter("m", value)
    assert str(exc.value).startswith("process_metrics/m: ")


def test_manifest_keeps_counters_and_reports_every_bad_regex():
    raw = json.loads(json.dumps(MANIFEST))
    raw["process_metrics"] = {"bazel": r"\bbazel\b", "icpx": {"regex": "icpx", "match": "output"}}
    m = parse_manifest(raw)
    assert m.to_dict()["process_metrics"] == {"bazel": {"regex": r"\bbazel\b", "match": "text"},
                                              "icpx": {"regex": "icpx", "match": "output"}}
    raw["process_metrics"] = {"a": "(", "b": "[", "ok": "x"}
    with pytest.raises(Exception) as exc:
        parse_manifest(raw)
    assert "process_metrics/a" in str(exc.value) and "process_metrics/b" in str(exc.value)


def test_guidance_read_is_the_workspace_guidance_files_the_agent_read():
    # globs are root-relative: a home-dir or ../ AGENTS.md is no repository guidance, a denied read read nothing.
    events = [{"kind": "read", "path": p} for p in
              ("AGENTS.md", "src/lib.c", "/home/agent/.claude/AGENTS.md", "../AGENTS.md")]
    events.append({"kind": "read", "path": "src/AGENTS.md", "denied": True})
    ctx = SimpleNamespace(manifest=parse_manifest(MANIFEST))
    assert run_process_metrics(ctx, events)["guidance_read"] == ["AGENTS.md"]


def test_a_command_the_permissions_denied_is_denied_not_failed():
    # A permission denial comes back as an is_error result (rc 1); a hook denial has rc null. Neither ran.
    events = [{"kind": "command", "text": "rm -rf build", "rc": 1, "denied": True},
              {"kind": "command", "text": "make &", "rc": None, "denied": True},
              {"kind": "command", "text": "make", "rc": 2}]
    m = process_metrics(events, lambda path: False)
    assert (m["failed_commands"], m["denied_commands"]) == (1, 2)


def test_a_counter_name_that_yaml_reads_as_a_number_or_bool_is_refused():
    # Such a key passed validation, then broke the sorted result.json write after the agent had run.
    raw = json.loads(json.dumps(MANIFEST))
    raw["process_metrics"] = {404: "Not Found", True: "x", "ok": "y"}
    with pytest.raises(Exception) as exc:
        parse_manifest(raw)
    assert "process_metrics/404" in str(exc.value) and "process_metrics/True" in str(exc.value)


def test_process_metrics_needs_a_runnable_manifest():
    raw = {"schema": "repo_manifest.v1", "id": "toy", "guidance": {"globs": ["AGENTS.md"]},
           "process_metrics": {"x": "x"}}
    with pytest.raises(Exception, match="process_metrics without eval_dir"):
        parse_manifest(raw)


def _summary(task_id: str, grader: str) -> TaskSummary:
    return TaskSummary(id=task_id, family="fix", base="0" * 40, grader=grader, gate=("pass",))


def test_grade_is_required_only_for_a_repository_grader():
    raw = json.loads(json.dumps(MANIFEST))
    raw["entry_points"].pop("grade")
    m = parse_manifest(raw)  # the schema no longer requires it
    assert grade_problems(m, [_summary("a", "dokimi:review_replay"), _summary("b", "dokimi:x")]) == []
    problems = grade_problems(m, [_summary("a", "dokimi:review_replay"), _summary("b", "tests")])
    assert len(problems) == 1 and problems[0].startswith("entry_points/grade: required") and problems[0].endswith(": b")
    assert grade_problems(parse_manifest(MANIFEST), [_summary("b", "tests")]) == []


def test_validate_reports_a_missing_grade(tmp_path):
    raw = json.loads(json.dumps(MANIFEST))
    raw["entry_points"].pop("grade")
    root = _repo(tmp_path, raw)
    tree = WorkTree(root)
    report = check_repo(tree, load_manifest(tree, "dev/evals/repo-eval.yaml"))
    assert any("entry_points/grade: required" in e and "fix_one, review_two" in e for e in report.errors)


def test_validate_reports_a_bad_task_host(tmp_path):
    root = _repo(tmp_path)
    _task(root, "needs_cpus", host={"min_cpus": "four"})
    tree = WorkTree(root)
    report = check_repo(tree, load_manifest(tree, "dev/evals/repo-eval.yaml"))
    assert any(e.startswith("dev/evals/tasks/needs_cpus/task.json:") and "min_cpus" in e for e in report.errors)


def test_any_matches_the_output_when_the_text_does_not():
    counters = _counters(out_only={"regex": "is not found", "match": "any"},
                         text_only={"regex": "is not found", "match": "text"})
    got = process_metrics(EVENTS, _guidance, counters)["counters"]
    assert got == {"out_only": 1, "text_only": 0}


def test_counters_search_command_events_only():
    events = [{"kind": "read", "path": "a", "text": "bazel", "output": "bazel"},
              {"kind": "tool", "name": "WebFetch", "text": "bazel", "output": "bazel"},
              {"kind": "command", "text": "bazel build", "rc": 0}]
    counters = _counters(bazel={"regex": "bazel", "match": "any"})
    assert process_metrics(events, _guidance, counters)["counters"] == {"bazel": 1}


@pytest.mark.parametrize("rc, failed", [(1, 1), (2, 1), (-9, 1), (137, 1), (0, 0), (None, 0)])
def test_failed_commands_counts_every_non_zero_rc(rc, failed):
    events = [{"kind": "command", "text": "x", "rc": rc}]
    assert process_metrics(events, _guidance)["failed_commands"] == failed


def test_a_grader_that_only_resembles_a_built_in_one_needs_grade():
    raw = json.loads(json.dumps(MANIFEST))
    raw["entry_points"].pop("grade")
    m = parse_manifest(raw)
    problems = grade_problems(m, [_summary("a", "dokimi_tests"), _summary("b", "x-dokimi:y"), _summary("c", "dokimi")])
    assert len(problems) == 1 and problems[0].endswith(": a, b, c")


def test_only_a_read_counts_as_reading_guidance():
    got = process_metrics([{"kind": "edit", "path": "AGENTS.md"}], _guidance)
    assert got["guidance_read"] == []


@pytest.mark.parametrize("path, read", [
    ("./AGENTS.md", ["AGENTS.md"]), ("src/./AGENTS.md", ["src/AGENTS.md"]), ("src//AGENTS.md", ["src/AGENTS.md"]),
    ("src/../AGENTS.md", ["AGENTS.md"]), ("a/../../x/AGENTS.md", []), ("../AGENTS.md", []),
    ("~/AGENTS.md", []), ("/home/a/AGENTS.md", [])])
def test_guidance_paths_are_normalized_before_the_workspace_test(path, read):
    assert process_metrics([{"kind": "read", "path": path}], _guidance)["guidance_read"] == read


def test_a_read_that_failed_read_no_guidance():
    events = [{"kind": "read", "path": "AGENTS.md", "rc": 1}, {"kind": "read", "path": "src/AGENTS.md", "rc": 0}]
    assert process_metrics(events, _guidance)["guidance_read"] == ["src/AGENTS.md"]


def test_a_read_error_in_the_stream_carries_a_failure(tmp_path):
    from dokimi.repos.events import convert
    def line(rec):
        return json.dumps(rec)
    raw = "\n".join([
        line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/workspace/AGENTS.md"}}]}}),
        line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "is_error": True, "content": "File does not exist."}]}}),
        line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r2", "name": "Read", "input": {"file_path": "/workspace/src/AGENTS.md"}}]}}),
        line({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r2", "content": "ok"}]}})])
    events = convert("claude-stream-json", raw, "/workspace").events
    assert [e["rc"] for e in events] == [1, 0]
    assert process_metrics(events, _guidance)["guidance_read"] == ["src/AGENTS.md"]

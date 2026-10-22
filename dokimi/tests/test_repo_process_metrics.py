# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Process metrics from ``events.jsonl`` (contract §4.1), and the manifest rules around them (§2)."""

from __future__ import annotations

import json

import pytest

from dokimi.repos import WorkTree, check_repo, load_manifest, match_glob, parse_manifest
from dokimi.repos.manifest import TaskSummary, grade_problems
from dokimi.repos.process_metrics import parse_counter, process_metrics

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

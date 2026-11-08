# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`refusal()` (tests/cli_refusal.py) answers an input refusal as `cli.main` does.

Tests that call a command through `refusal()` rely on it standing in for `cli.main`. These run
the same argv both ways and require the same exit code, stdout and stderr, so the helper cannot drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dokimi import cli
from tests.cli_refusal import refusal


def _assert_helper_matches_main(argv, monkeypatch, capsys, expect):
    with refusal() as outcome:
        args = cli.parse_args(argv)
        args.func(args)
    capsys.readouterr()  # drop the output the helper re-emitted
    monkeypatch.setattr(sys, "argv", ["dokimi", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    assert outcome.code == 2
    assert outcome.err.startswith("Error: ") and expect in outcome.err, outcome.err
    assert (outcome.code, outcome.out, outcome.err) == (code, captured.out, captured.err)


@pytest.mark.parametrize("argv_for, expect", [
    (lambda d: ["data", "export", "--kind", "answers", "--input", str(d / "missing.json"),
                "--out-dir", str(d / "out")], "cannot export"),
    (lambda d: ["eval", "judge", "validate", "--answers", str(d / "a.json"), "--mode", "absolute",
                "--escalate-model", "m"], "need --mode pairwise"),
    (lambda d: ["eval", "tasks", "list", "--tasks-root", str(d / "missing")], "--tasks-root is not a directory"),
    (lambda d: ["explore", "list", "--tasks-root", str(d / "missing")], "--tasks-root is not a directory"),
    (lambda d: ["catalog", "check", "--root", str(d / "missing")], "--root is not a directory"),
    (lambda d: ["subjects", "list", "--subjects-root", str(d / "missing")], "--subjects-root is not a directory"),
], ids=["data-export-missing-input", "judge-validate-escalation-needs-pairwise", "eval-tasks-list-root",
        "explore-list-root", "catalog-check-root", "subjects-list-root"])
def test_helper_matches_cli_main_for_converted_commands(argv_for, expect, tmp_path, monkeypatch, capsys):
    _assert_helper_matches_main(argv_for(tmp_path), monkeypatch, capsys, expect)


def _harbor_task(root):
    task = root / "task-one"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "instruction.md").write_text("Edit the code.", encoding="utf-8")
    (task / "task.toml").write_text('[metadata]\ndifficulty = "smoke"\n', encoding="utf-8")
    return task


def _explore_root(d):
    root = d / "explore"
    root.mkdir()
    (root / "t.yaml").write_text("id: t\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n", encoding="utf-8")
    return root


def _tasks_root(d):
    root = d / "tasks"
    root.mkdir()
    _harbor_task(root)
    return root


def _skill_catalog(d):
    skill = d / "catalog" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: alpha\ndescription: Does alpha.\n---\n# alpha\n", encoding="utf-8")
    return d / "catalog"


def _explore_run(d, arms):
    return ["explore", "run", "--tasks", "t", "--tasks-root", str(_explore_root(d)), "--arms", arms]


def _subject_run(d):
    docs = d / "docs.md"
    docs.write_text("# Docs\n", encoding="utf-8")
    questions = d / "questions.json"
    questions.write_text('[{"id": "q1", "question": "Q?"}]', encoding="utf-8")
    descriptor = d / "subject.toml"
    descriptor.write_text(
        f'[subject]\nid = "docs-subject"\nkind = "doc-source"\nref = "local:{docs.as_posix()}"\n\n'
        f'[suite]\nproducts = ["oneTBB"]\nquestions = ["{questions.as_posix()}"]\n'
        f'tasks = ["{_harbor_task(d).as_posix()}"]\n', encoding="utf-8")
    return ["subjects", "run", str(descriptor), "--out-dir", str(d / "out"), "--work-harnesses", "no-such-harness"]


@pytest.mark.parametrize("argv_for, expect", [
    (lambda d: ["catalog", "formats", "--root", str(d / "missing")], "--root is not a directory"),
    (lambda d: _explore_run(d, "command:"), "'command:' arm requires a template"),
    (lambda d: _explore_run(d, "bogus"), "Unknown explore arm: 'bogus'"),
    (lambda d: _explore_run(d, ","), "No arms selected"),
    (lambda d: ["tasks", "run", "--tasks", ",", "--tasks-root", str(_tasks_root(d)), "--harnesses", "codex"],
     "No tasks selected"),
    (lambda d: ["tasks", "run", "--tasks", "task-one", "--tasks-root", str(_tasks_root(d)), "--harnesses", ","],
     "No harnesses selected"),
    (_subject_run, "no-such-harness"),
    (lambda d: ["catalog", "trigger-probe", "--catalog", str(_skill_catalog(d)), "--trigger-set",
                str(d / "missing.json"), "--claude", "python3", "--out", str(d / "probe.json")],
     "--trigger-set: "),
], ids=["catalog-formats-root", "explore-run-command-arm-without-template", "explore-run-unknown-arm",
        "explore-run-no-arms", "tasks-run-no-tasks", "tasks-run-no-harnesses", "subjects-run-unknown-work-harness",
        "trigger-probe-unreadable-trigger-set"])
def test_helper_matches_cli_main_for_the_remaining_converted_refusals(
        argv_for, expect, tmp_path, monkeypatch, capsys):
    def fake_matrix_run(config, *, out_dir, **kw):  # the awareness matrix runs before the work preflight
        run_dir = Path(out_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "matrix-rollup.json").write_text(json.dumps({
            "schema_version": "matrix_rollup.v1", "library_name": config.product, "cells": [],
            "plugin_deltas": [], "warnings": [],
        }), encoding="utf-8")

    monkeypatch.setattr("dokimi.commands.arms.run_matrix", fake_matrix_run)
    _assert_helper_matches_main(argv_for(tmp_path), monkeypatch, capsys, expect)

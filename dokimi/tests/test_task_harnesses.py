# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for executable coding-task harness support."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import stat
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.harnesses import TaskSuiteRunner, discover_tasks, load_task
from dokimi.harnesses.command import CommandHarness, clear_run_dir
from dokimi.harnesses.operations import load_operations
from dokimi.harnesses.registry import create_harness, known_harnesses
from dokimi.harnesses.runner import summarize_task_results
from dokimi.process import FakeRunner, reply


def _fixture_task(tmp_path: Path) -> Path:
    task = tmp_path / "toy-task"
    (task / "tests").mkdir(parents=True)
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")  # Harbor needs one
    (task / "environment").mkdir()
    (task / "solution").mkdir()
    (task / "instruction.md").write_text("Write solution.py and pass tests.", encoding="utf-8")
    (task / "task.toml").write_text(
        """
[metadata]
difficulty = "easy"
tags = ["toy", "python"]

[agent]
timeout_sec = 12

[verifier]
timeout_sec = 3

[environment]
docker_image = "toy:latest"
allow_internet = false
""",
        encoding="utf-8",
    )
    return task


def test_task_loader_reads_terminal_bench_metadata(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    assert task.name == "toy-task"
    assert task.timeout_sec == 12
    assert task.metadata["tags"] == ["toy", "python"]
    assert "Write solution.py" in task.instruction


def test_discover_tasks_reads_the_given_root(tmp_path):
    _fixture_task(tmp_path)
    tasks = discover_tasks(tmp_path)
    assert [t.name for t in tasks] == ["toy-task"]


def test_discover_tasks_without_a_root_names_the_flag(tmp_path):
    """There is no bundled tasks tree, so an omitted root is an error."""
    with pytest.raises(ValueError, match="--tasks-root"):
        discover_tasks(None)


def test_load_task_by_name_without_a_root_names_the_flag():
    with pytest.raises(ValueError, match="--tasks-root"):
        load_task("toy-task")


def test_load_task_by_name_resolves_against_the_root(tmp_path):
    _fixture_task(tmp_path)
    assert load_task("toy-task", root=tmp_path).name == "toy-task"


def test_load_task_with_a_missing_path_still_reports_the_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="Task directory not found"):
        load_task(tmp_path / "no-such-task")


def test_known_harness_aliases_render_harbor_commands(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    harness = create_harness("codex")
    cmd = harness.build_command(task, model="gpt-5", output_dir=tmp_path / "out")
    assert cmd[:2] == ["harbor", "run"]
    assert "codex" in cmd
    assert "gpt-5" in cmd
    assert "codex" in known_harnesses()


def test_command_harness_id_includes_template_to_avoid_collisions():
    first = create_harness("command:echo {task}")
    second = create_harness("command:cat {task_path}")
    assert first.harness_id == "command:echo {task}"
    assert second.harness_id == "command:cat {task_path}"


def test_command_template_allows_code_braces_in_template_and_prompt(tmp_path):
    task_path = _fixture_task(tmp_path)
    (task_path / "instruction.md").write_text(
        'Write {"ok": true} into config.py and keep f"{value}" intact.',
        encoding="utf-8",
    )
    task = load_task(task_path)

    literal = CommandHarness("fake", "echo {'literal':1} {task}")
    assert literal.build_command(task, model=None, output_dir=tmp_path / "out") == [
        "echo",
        "{literal:1}",
        "toy-task",
    ]

    prompt = CommandHarness("fake", "echo {prompt_quoted}")
    rendered_prompt = " ".join(prompt.build_command(task, model=None, output_dir=tmp_path / "out"))
    assert '{"ok": true}' in rendered_prompt
    assert 'f"{value}"' in rendered_prompt

    escaped = CommandHarness("fake", "echo {prompt_escaped}")
    rendered_escaped = " ".join(
        escaped.build_command(task, model=None, output_dir=tmp_path / "out")
    )
    assert "{{ok: true}}" in rendered_escaped
    assert "f{{value}}" in rendered_escaped


def test_command_harness_collects_reward_and_operations(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    out_dir = tmp_path / "out"

    def agent(call):
        Path(call.env["DOKIMI_OPERATIONS_FILE"]).write_text(
            json.dumps({"type": "tool", "name": "edit", "elapsed_sec": 0.25}) + "\n",
            encoding="utf-8",
        )
        reward_dir = Path(call.env["DOKIMI_OUTPUT_DIR"]) / "logs" / "verifier"
        reward_dir.mkdir(parents=True)
        (reward_dir / "reward.txt").write_text("1.0", encoding="utf-8")
        return reply('DOKIMI_OP {"type":"loop","name":"turn","elapsed_sec":0.1}\n')

    monkeypatch.setenv("DOKIMI_TEST_ONLY_OPERATOR", "kept")
    fake = FakeRunner(agent)
    harness = CommandHarness("fake", "fake-agent --task {task_path} --model {model}", runner=fake)
    result = harness.run(task, model="m", output_dir=out_dir)
    (call,) = fake.calls
    assert call.argv[0] == "fake-agent" and call.cwd == task.path.parent.parent
    # The command is the operator's own CLI: it runs with the operator's environment.
    assert call.env["DOKIMI_TEST_ONLY_OPERATOR"] == "kept"
    # Reward, operations and telemetry are parsed from this output: none of it may be cut.
    assert call.capture_limit is None

    assert result.passed is True
    assert result.reward == 1.0
    assert result.metrics["stdout_bytes"] > 0
    by_type = result.as_dict()["metrics"]["operations_by_type"]
    assert by_type["harness"] == 1
    assert by_type["tool"] == 1
    assert by_type["loop"] == 1


def test_command_harness_relative_output_dir_reads_the_agents_failing_reward(
    tmp_path, monkeypatch
):
    """`tasks run` defaults to a relative --output-dir, and the agent runs in the
    task checkout's parent. The agent's reward 0.0 (written where
    $DOKIMI_OUTPUT_DIR points, exit 0, as Harbor-style agents do) must be the
    one the framework reads, not lost so the cell falls back to rc == 0."""
    task = load_task(_fixture_task(tmp_path / "content" / "tasks"))
    operator_cwd = tmp_path / "operator"
    operator_cwd.mkdir()
    monkeypatch.chdir(operator_cwd)
    script = (
        "import os, pathlib; "
        "d = pathlib.Path(os.environ['DOKIMI_OUTPUT_DIR']); "
        "d.mkdir(parents=True, exist_ok=True); "
        "(d / 'reward.txt').write_text('0.0'); "
        "pathlib.Path(os.environ['DOKIMI_OPERATIONS_FILE']).write_text("
        "'{\"type\": \"tool\", \"name\": \"edit\"}' + chr(10))"
    )
    template = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {{output_dir}}"
    harness = CommandHarness("fake", template)

    result = harness.run(task, output_dir=Path("results") / "task-runs" / "cell")

    assert result.returncode == 0
    assert (result.passed, result.reward) == (False, 0.0), (
        f"agent wrote reward 0.0 and exited 0, cell read as passed={result.passed} "
        f"reward={result.reward}"
    )
    assert (operator_cwd / "results" / "task-runs" / "cell" / "reward.txt").is_file()
    assert not (tmp_path / "content" / "results").exists()
    assert any(op.type == "tool" for op in result.operations)
    # The {output_dir} placeholder is absolute too (Harbor's --jobs-dir); the
    # artifact keeps the path as the caller gave it.
    assert result.command[-1] == (operator_cwd / "results" / "task-runs" / "cell").as_posix()
    assert result.output_dir == str(Path("results") / "task-runs" / "cell")


def test_command_harness_uses_finite_timeout_when_task_has_none(tmp_path, monkeypatch):
    task_path = _fixture_task(tmp_path)
    (task_path / "task.toml").write_text('[metadata]\ndifficulty = "easy"\n', encoding="utf-8")
    task = load_task(task_path)
    fake = FakeRunner(lambda call: reply("reward: 1.0"))
    result = CommandHarness("fake", "fake-agent --task {task_path}", runner=fake).run(
        task, output_dir=tmp_path / "out"
    )

    assert fake.calls[0].timeout == 3600.0
    assert result.metrics["timeout_sec"] == 3600.0


def test_operation_loader_accepts_markers_and_unstructured_lines(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "operations.jsonl").write_text(
        json.dumps({"type": "subagent", "name": "reviewer"}) + "\n",
        encoding="utf-8",
    )
    ops = load_operations(
        out,
        'DOKIMI_OP {"type":"tool","name":"search"}\nloop 1 done',
        "",
    )
    assert [op.type for op in ops] == ["subagent", "tool", "loop"]


def test_operation_loader_defaults_malformed_elapsed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "operations.jsonl").write_text(
        json.dumps({"type": "tool", "name": "edit", "elapsed_sec": "not-a-number"}) + "\n",
        encoding="utf-8",
    )
    ops = load_operations(out, "", "")
    assert ops[0].elapsed_sec == 0.0


def test_operation_loader_reports_unreadable_jsonl(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    log = out / "operations.jsonl"
    log.write_text("{}", encoding="utf-8")

    def fail_read_text(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    ops = load_operations(out, "", "")
    assert ops[0].type == "log_parse_error"
    assert ops[0].status == "error"
    assert "boom" in ops[0].metadata["error"]


def test_task_suite_summary_compares_to_baseline(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))

    def fake_run(self, task, *, model=None, output_dir=None, timeout_sec=None, dry_run=False):
        from dokimi.harnesses.models import HarnessResult, OperationRecord

        passed = self.harness_id == "candidate"
        return HarnessResult(
            harness=self.harness_id,
            task=task.name,
            command=["fake"],
            returncode=0 if passed else 1,
            elapsed_sec=1.0,
            reward=1.0 if passed else 0.0,
            passed=passed,
            operations=[OperationRecord("harness", self.harness_id)],
        )

    monkeypatch.setattr(CommandHarness, "run", fake_run)
    suite = TaskSuiteRunner(
        tasks=[task],
        harnesses=["baseline", "candidate"],
        baseline_harness="baseline",
        command_template="fake {task_path}",
        output_root=tmp_path / "runs",
    )
    output = suite.run()

    validate_artifact("task_runs", output)
    assert output["summary"]["per_harness"]["baseline"]["pass_rate"] == 0.0
    assert output["summary"]["per_harness"]["candidate"]["pass_rate"] == 1.0
    assert output["summary"]["comparisons"]["candidate"]["pass_rate_delta"] == 1.0


def test_a_bad_harness_id_fails_before_any_cell_runs(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    ran = []
    monkeypatch.setattr(CommandHarness, "run", lambda self, *a, **k: ran.append(self.harness_id))
    suite = TaskSuiteRunner(
        tasks=[task],
        harnesses=["command:fake {task_path}", f"docker-claude-skill:{tmp_path / 'nope' / 'SKILL.md'}"],
        output_root=tmp_path / "runs",
    )
    with pytest.raises(ValueError, match="does not exist"):
        suite.run()
    assert ran == []


def test_summarize_task_results_counts_operations():
    rows = [
        {
            "harness": "h1",
            "passed": True,
            "elapsed_sec": 1,
            "metrics": {"operation_count": 2, "operations_by_type": {"tool": 1, "loop": 1}},
        },
        {
            "harness": "h1",
            "passed": False,
            "elapsed_sec": 2,
            "metrics": {"operation_count": 1, "operations_by_type": {"tool": 1}},
        },
    ]
    summary = summarize_task_results(rows)
    assert summary["per_harness"]["h1"]["pass_rate"] == 0.5
    assert summary["per_harness"]["h1"]["operations_by_type"] == {"tool": 2, "loop": 1}


# ── paired binary significance (McNemar) on the execution-verified track ─────


def _row(task, harness, passed, *, repeat=1, error=None):
    metrics = {"operation_count": 0, "operations_by_type": {}}
    if error:
        metrics["error"] = error
    return {"task": task, "harness": harness, "passed": passed, "reward": None if error else float(passed),
            "elapsed_sec": 1.0, "repeat": repeat, "metrics": metrics}


def _sweep(pairs, *, baseline="noskill", treatment="skill"):
    """One row per task for both harnesses; ``pairs`` is [(base, treat), ...]."""
    rows = []
    for i, (base, treat) in enumerate(pairs):
        rows.append(_row(f"t{i}", baseline, base))
        rows.append(_row(f"t{i}", treatment, treat))
    return summarize_task_results(rows, baseline_harness=baseline)


def test_comparison_carries_a_paired_binary_test():
    # Six tasks the skill fixes, four both arms already solve.
    summary = _sweep([(False, True)] * 6 + [(True, True)] * 4)
    mc = summary["comparisons"]["skill"]["mcnemar"]
    assert mc["test"] == "mcnemar_exact"
    assert mc["n"] == 10
    assert mc["n_gained"] == 6 and mc["n_lost"] == 0
    assert mc["n_both_passed"] == 4
    assert mc["p_exact"] < 0.05
    assert mc["significant"] is True
    assert mc["significant_adjusted"] is True


def test_baseline_harness_is_not_compared_against_itself():
    summary = _sweep([(False, True)] * 6)
    assert "noskill" not in summary["comparisons"]


def test_no_pairing_without_a_baseline_harness():
    rows = [_row("t0", "skill", True), _row("t0", "noskill", False)]
    assert summarize_task_results(rows)["comparisons"] == {}


def test_harness_errors_are_excluded_rather_than_counted_as_failures():
    """A docker crash is missing data; charging it to the treatment would lie."""
    rows = [
        _row("t0", "noskill", False), _row("t0", "skill", True),
        _row("t1", "noskill", False), _row("t1", "skill", True),
        _row("t2", "noskill", False),
        _row("t2", "skill", False, error="RuntimeError: docker build failed"),
    ]
    mc = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]["mcnemar"]
    assert mc["n"] == 2                 # t2 dropped, not scored as a loss
    assert mc["n_gained"] == 2 and mc["n_lost"] == 0
    assert mc["n_excluded"] == 1


def test_one_shared_cell_keeps_exclusions_when_the_test_cannot_run():
    """One shared cell is not a McNemar test, and must not hide the rest.

    Two further cells that each harness attempted alone used to make the
    comparison null: ``mcnemar_test`` returns None below two pairs, and the
    exclusion count lived only on that result. The artifact then looked like
    no comparison, not a withheld one with two cells dropped.
    """
    rows = [
        _row("t0", "noskill", False), _row("t0", "skill", True),
        _row("t1", "noskill", False),
        _row("t1", "skill", False, error="RuntimeError: docker build failed"),
        _row("t2", "noskill", False, error="RuntimeError: docker build failed"),
        _row("t2", "skill", True),
    ]
    mc = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]["mcnemar"]
    assert mc is not None
    assert mc["n"] == 1
    assert mc["n_excluded"] == 2
    assert mc["p_exact"] is None
    assert mc["significant"] is None
    assert mc["significant_adjusted"] is None
    assert mc["n_gained"] == 1 and mc["n_lost"] == 0
    from dokimi.report.task_runs_report import _mcnemar_lines
    rendered = "\n".join(_mcnemar_lines({"skill": {"mcnemar": mc}}, "noskill"))
    assert "2 cell-exclusion" in rendered
    assert "—" in rendered


def _tested_plus_withheld_rows():
    """``skill`` gets a real McNemar test; ``thin`` shares one cell, so it is withheld."""
    rows = []
    for i in range(6):
        rows += [_row(f"t{i}", "noskill", False), _row(f"t{i}", "skill", True)]
    rows.append(_row("t0", "thin", True))
    rows.append(_row("t1", "thin", False, error="RuntimeError: docker build failed"))
    return rows


def test_a_withheld_comparison_is_not_counted_in_the_correction_family():
    """A withheld record tested nothing: it must not inflate the Holm multiplier."""
    comparisons = summarize_task_results(
        _tested_plus_withheld_rows(), baseline_harness="noskill"
    )["comparisons"]
    tested = comparisons["skill"]["mcnemar"]
    withheld = comparisons["thin"]["mcnemar"]
    assert tested["family_size"] == 1
    assert tested["correction"] == "holm"
    assert withheld["p_exact"] is None
    assert withheld["p_adjusted"] is None
    assert "correction" not in withheld
    assert "family_size" not in withheld
    assert set(withheld) >= set(tested) - {"correction", "correction_family", "family_size"}


def test_a_run_with_a_withheld_comparison_validates():
    rows = _tested_plus_withheld_rows()
    artifact = {
        "schema_version": "task_runs.v1",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "haiku",
        "baseline_harness": "noskill",
        "harnesses": ["noskill", "skill", "thin"],
        "repeats": 1,
        "tasks": [
            {"name": f"t{i}", "path": f"tasks/t{i}", "metadata": {"difficulty": "easy"},
             "verifier": {}, "agent": {}, "environment": {}}
            for i in range(6)
        ],
        "results": [{**r, "operations": []} for r in rows],
        "summary": summarize_task_results(rows, baseline_harness="noskill"),
    }
    withheld = artifact["summary"]["comparisons"]["thin"]["mcnemar"]
    assert withheld["ci_lo"] is None and withheld["ci_confidence"] is None
    validate_artifact("task_runs", artifact)


def test_cells_that_errored_on_both_sides_are_counted_as_excluded():
    """The loss is largest exactly where the old count reported zero.

    Measuring exclusions against the *surviving* cells missed any cell that
    errored on both sides, so a sweep where docker was broken for half the tasks
    reported ``n_excluded == 0`` and looked like a clean comparison.
    """
    rows = [
        _row("t0", "noskill", False), _row("t0", "skill", True),
        _row("t1", "noskill", False), _row("t1", "skill", True),
        _row("t2", "noskill", False, error="RuntimeError: docker build failed"),
        _row("t2", "skill", False, error="RuntimeError: docker build failed"),
    ]
    mc = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]["mcnemar"]
    assert mc["n"] == 2
    assert mc["n_excluded"] == 1


def test_pass_rate_excludes_errored_cells_from_the_denominator():
    """Two scored passes and one docker crash is 2/2, not 2/3."""
    rows = [
        _row("t0", "skill", True), _row("t1", "skill", True),
        _row("t2", "skill", False, error="RuntimeError: docker build failed"),
    ]
    stats = summarize_task_results(rows)["per_harness"]["skill"]
    assert stats["n"] == 3
    assert stats["errored"] == 1
    assert stats["scored"] == 2
    assert stats["pass_rate"] == 1.0
    assert stats["per_task"]["t2"] == {"n": 1, "passed": 0, "errored": 1, "pass_rate": None}


def test_all_cells_errored_gives_no_pass_rate():
    rows = [_row("t0", "skill", False, error="RuntimeError: docker build failed")]
    stats = summarize_task_results(rows)["per_harness"]["skill"]
    assert stats["scored"] == 0
    assert stats["pass_rate"] is None


def test_unpaired_deltas_are_withheld_when_one_arm_errored():
    """Arms that lost different cells differ by more than the treatment."""
    rows = [
        _row("t0", "noskill", False), _row("t0", "skill", True),
        _row("t1", "noskill", False), _row("t1", "skill", True),
        _row("t2", "noskill", True),
        _row("t2", "skill", False, error="RuntimeError: docker build failed"),
    ]
    cmp = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]
    assert cmp["pass_rate_delta"] is None
    assert cmp["passed_delta"] is None  # not "1 fewer pass": the cell has no verdict
    assert cmp["errored"] == 1
    assert cmp["baseline_errored"] == 0
    assert cmp["mcnemar"]["n"] == 2     # the paired test still uses t0, t1


def test_cost_delta_is_withheld_when_one_arm_errored():
    """An errored cell has no cost, so the arm would look cheaper."""
    rows = [_row("t0", "noskill", True), _row("t0", "skill", True),
            _row("t1", "noskill", True), _row("t1", "skill", False, error="E")]
    for r in rows:
        if not r["metrics"].get("error"):
            r["metrics"]["cost_usd"] = 1.0
    cmp = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]
    assert cmp["cost_delta_usd"] is None


def test_deltas_are_reported_when_both_arms_lost_the_same_cell():
    """A build that breaks for one task in both arms leaves the scored sets equal."""
    rows = [
        _row("t0", "noskill", False), _row("t0", "skill", True),
        _row("t1", "noskill", False), _row("t1", "skill", True),
        _row("t2", "noskill", False, error="RuntimeError: docker build failed"),
        _row("t2", "skill", False, error="RuntimeError: docker build failed"),
    ]
    cmp = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]
    assert cmp["pass_rate_delta"] == 1.0
    assert cmp["passed_delta"] == 2
    assert cmp["errored"] == 1 and cmp["baseline_errored"] == 1


def test_an_errored_row_never_counts_as_passed():
    """A foreign row marked passed with metrics.error must not lift pass_rate past 1."""
    rows = [_row("t0", "skill", True), _row("t1", "skill", True)]
    rows[0]["metrics"]["error"] = "E"
    stats = summarize_task_results(rows)["per_harness"]["skill"]
    assert stats["passed"] == 1 and stats["scored"] == 1 and stats["pass_rate"] == 1.0
    assert stats["per_task"]["t0"]["passed"] == 0


def test_an_errored_row_is_out_of_avg_reward():
    rows = [_row("t0", "skill", True), _row("t1", "skill", True)]
    rows[0]["metrics"]["error"] = "E"
    rows[0]["reward"] = 0.0
    assert summarize_task_results(rows)["per_harness"]["skill"]["avg_reward"] == 1.0


def test_pass_rate_delta_is_reported_when_no_cell_errored():
    cmp = _sweep([(False, True), (True, True)])["comparisons"]["skill"]
    assert cmp["pass_rate_delta"] == 0.5
    assert cmp["errored"] == 0 and cmp["baseline_errored"] == 0


def test_repeats_pair_by_index_within_a_task():
    rows = [
        _row("t0", "noskill", False, repeat=1), _row("t0", "skill", True, repeat=1),
        _row("t0", "noskill", True, repeat=2), _row("t0", "skill", True, repeat=2),
    ]
    mc = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]["skill"]["mcnemar"]
    assert mc["n"] == 2
    assert mc["n_gained"] == 1 and mc["n_both_passed"] == 1
    # Two pairs, but both are repeats of t0: one independent unit, and the
    # artifact has to say so rather than letting n stand in for sample size.
    assert mc["n_units"] == 1
    assert mc["pseudo_replicated"] is True


def test_one_repeat_per_task_is_not_flagged_as_pseudo_replicated():
    summary = _sweep([(False, True)] * 6)
    mc = summary["comparisons"]["skill"]["mcnemar"]
    assert mc["n_units"] == mc["n"] == 6
    assert mc["pseudo_replicated"] is False


def test_pvalues_are_corrected_across_the_compared_harnesses():
    rows = []
    for i in range(6):
        rows.append(_row(f"t{i}", "noskill", False))
        rows.append(_row(f"t{i}", "skill-a", True))
        rows.append(_row(f"t{i}", "skill-b", True))
    cmp = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]
    for harness in ("skill-a", "skill-b"):
        mc = cmp[harness]["mcnemar"]
        assert mc["correction"] == "holm"
        assert mc["family_size"] == 2
        # Correction can only raise a p-value, never lower it.
        assert mc["p_adjusted"] >= mc["p_exact"]


def test_a_harness_with_no_shared_task_gets_no_test():
    rows = [_row("t0", "noskill", True), _row("t1", "skill", True)]
    cmp = summarize_task_results(rows, baseline_harness="noskill")["comparisons"]
    assert cmp["skill"]["mcnemar"] is None
    # The unpaired pass-rate delta is still reported; only the test is withheld.
    assert cmp["skill"]["pass_rate_delta"] == 0.0


def test_identical_outcomes_yield_no_verdict_rather_than_a_negative_one():
    summary = _sweep([(True, True), (False, False), (True, True)])
    mc = summary["comparisons"]["skill"]["mcnemar"]
    assert mc["n_discordant"] == 0
    assert mc["p_exact"] is None
    assert mc["significant"] is None
    assert mc["significant_adjusted"] is None


def test_a_real_mcnemar_block_validates_and_then_renders():
    """Schema-valid is not the same as renderable.

    Every other test on this track stops at the summary dict, so a formatter
    that raises on a field the runner really emits — a null ``p_exact``, the new
    ``pseudo_replicated`` caveat — would pass the suite. This runs the whole
    seam: real ``summarize_task_results`` output, validated, then rendered.
    """
    from dokimi.report.task_runs_report import render_task_runs_report

    rows = []
    for i in range(6):
        rows += [_row(f"t{i}", "noskill", False, repeat=1),
                 _row(f"t{i}", "skill", True, repeat=1),
                 _row(f"t{i}", "noskill", False, repeat=2),
                 _row(f"t{i}", "skill", True, repeat=2)]
    artifact = {
        "schema_version": "task_runs.v1",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "haiku",
        "baseline_harness": "noskill",
        "harnesses": ["noskill", "skill"],
        "repeats": 2,
        "tasks": [
            {"name": f"t{i}", "path": f"tasks/t{i}", "metadata": {"difficulty": "easy"},
             "verifier": {}, "agent": {}, "environment": {}}
            for i in range(6)
        ],
        "results": [{**r, "operations": []} for r in rows],
        "summary": summarize_task_results(rows, baseline_harness="noskill"),
    }
    validate_artifact("task_runs", artifact)

    md = render_task_runs_report(artifact)
    assert "## Paired significance vs baseline (McNemar)" in md
    # 12 pairs from 6 tasks: the caveat has to reach the reader, not just the JSON.
    assert "only 6 independent task(s)" in md


def test_path_placeholders_keep_windows_separators(tmp_path):
    from dataclasses import replace
    from pathlib import PureWindowsPath

    task = replace(load_task(_fixture_task(tmp_path)), path=PureWindowsPath(r"C:\tasks\toy-task"))
    harness = CommandHarness("fake", "agent --task {task_path} --out {output_dir}")
    cmd = harness.build_command(task, model=None, output_dir=PureWindowsPath(r"C:\runs\out"))
    assert cmd == ["agent", "--task", "C:/tasks/toy-task", "--out", "C:/runs/out"]


def _timeout_expired(call):
    return reply("partial out", "partial err", timed_out=True)


@pytest.mark.parametrize("harness_id", ["command:fake-agent {task_path}", "codex", "claude-code",
                                        "terminal-bench:aider"])
def test_agent_timeout_is_a_failed_cell_not_an_errored_one(tmp_path, monkeypatch, harness_id):
    """An agent that exceeds its timeout produced a verdict: it failed. Raising
    would make the runner record it as errored and drop it from pass_rate."""
    task = load_task(_fixture_task(tmp_path))

    result = create_harness(harness_id, runner=FakeRunner(_timeout_expired)).run(
        task, model="m", output_dir=tmp_path / "out")

    assert result.returncode == 124
    assert result.reward == 0.0
    assert result.passed is False
    assert result.metrics["timed_out"] is True
    assert "error" not in result.metrics
    assert "partial out" in result.stdout_tail
    assert "TIMEOUT" in result.stderr_tail


def test_agent_timeout_with_text_output_still_reports(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))

    fake = FakeRunner(lambda call: reply("reward: 1.0", timed_out=True))
    result = CommandHarness("fake", "fake-agent", runner=fake).run(task, output_dir=tmp_path / "out")
    # A reward printed before the timeout does not turn a timeout into a pass.
    assert result.reward == 0.0 and result.passed is False


def _pid_gone(pid: int, within: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


# An agent that starts a helper (a tool call, a language server) and writes its pid; the
# helper's output goes elsewhere, so only the process tree ties it to the agent.
_AGENT_WITH_HELPER = (
    "import subprocess, sys, time; "
    "h = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
    "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
    "open(sys.argv[1], 'w').write(str(h.pid)); print('working', flush=True); "
)


@pytest.mark.skipif(sys.platform == "win32", reason="the pid probe is POSIX")
@pytest.mark.parametrize("ends", ["timeout", "exit"])
def test_command_harness_leaves_nothing_of_the_agent_running(tmp_path, ends):
    """A timed-out agent's helper used to outlive the cell (only the agent was killed), and so
    did a helper left behind by an agent that exited; both are killed with the agent's tree."""
    task = load_task(_fixture_task(tmp_path / "tasks"))
    pid_file = tmp_path / "helper.pid"
    script = _AGENT_WITH_HELPER + ("time.sleep(60)" if ends == "timeout" else "print('reward: 1')")
    template = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(str(pid_file))}"
    result = CommandHarness("fake", template).run(task, output_dir=tmp_path / "out", timeout_sec=2)
    helper = int(pid_file.read_text())
    try:
        assert _pid_gone(helper), "the agent's helper outlived the cell"
    finally:
        with contextlib.suppress(OSError):
            os.kill(helper, signal.SIGKILL)
    if ends == "timeout":
        assert (result.returncode, result.reward, result.metrics["timed_out"]) == (124, 0.0, True)
        assert "working" in result.stdout_tail and "TIMEOUT" in result.stderr_tail
    else:
        assert (result.returncode, result.reward, result.passed) == (0, 1.0, True)


# A `harbor` that, on SIGTERM, takes a second to remove its trial container (here a
# marker file), as the real one does before it exits.
_HARBOR_THAT_CLEANS_UP = """import pathlib, signal, sys, time
def stop(*_):
    time.sleep(1)
    pathlib.Path(sys.argv[-1], "removed").write_text("yes")  # argv[-1]: --jobs-dir
    sys.exit(143)
signal.signal(signal.SIGTERM, stop)
print("trial running", flush=True)
time.sleep(60)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows terminates the job at once")
def test_a_timed_out_harbor_gets_the_grace_to_remove_its_container(tmp_path, monkeypatch):
    """`harbor` killed at once left its trial container and image behind (they belong to
    the Docker daemon); SIGTERM and a grace let it remove them. A plain command gets none."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    harbor = bin_dir / "harbor"
    harbor.write_text(f"#!{sys.executable}\n{_HARBOR_THAT_CLEANS_UP}", encoding="utf-8")
    harbor.chmod(harbor.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    task = load_task(_fixture_task(tmp_path / "tasks"))
    out = tmp_path / "out"
    result = create_harness("terminal-bench:aider").run(task, model="m", output_dir=out, timeout_sec=1)
    assert (result.returncode, result.metrics["timed_out"]) == (124, True)
    assert "trial running" in result.stdout_tail
    assert (out / "removed").is_file(), "harbor was killed before it removed its container"
    fake = FakeRunner()
    create_harness("command:agent", runner=fake).run(task, output_dir=out)
    assert fake.calls[0].term_grace == 0.0


def test_an_arm_that_hangs_is_not_scored_above_one_that_fails_fast(tmp_path):
    """Real sweep: two arms identical except that on the hard tasks one hangs
    past timeout_sec and the other exits 1. Both are failures, so the arms tie
    and the delta is reported (nothing errored)."""
    import sys

    root = tmp_path / "tasks"
    for name in ("easy-1", "easy-2", "hard-1", "hard-2"):
        task = root / name
        task.mkdir(parents=True)
        (task / "instruction.md").write_text("x", encoding="utf-8")
        (task / "task.toml").write_text("[agent]\ntimeout_sec = 1\n", encoding="utf-8")
    # The harness id becomes a run directory name; a script path relative to
    # the harness cwd keeps it under Windows' 260-character path limit.
    (tmp_path / "agent.py").write_text(
        "import sys, time\n"
        "mode, task = sys.argv[1], sys.argv[2]\n"
        "if task.startswith('easy'):\n"
        "    print('reward: 1.0'); sys.exit(0)\n"
        "if mode == 'hang':\n"
        "    time.sleep(30)\n"
        "print('reward: 0.0'); sys.exit(1)\n",
        encoding="utf-8",
    )
    py = Path(sys.executable).as_posix()
    hang = f"command:{py} agent.py hang {{task}}"
    fast = f"command:{py} agent.py fast {{task}}"
    output = TaskSuiteRunner(
        tasks=discover_tasks(root),
        harnesses=[fast, hang],
        baseline_harness=fast,
        output_root=tmp_path / "runs",
    ).run()

    validate_artifact("task_runs", output)
    ph = output["summary"]["per_harness"]
    for h in (hang, fast):
        assert ph[h]["errored"] == 0
        assert ph[h]["scored"] == 4
        assert ph[h]["pass_rate"] == 0.5
    cmp = output["summary"]["comparisons"][hang]
    assert cmp["pass_rate_delta"] == 0.0
    assert cmp["errored"] == 0 and cmp["baseline_errored"] == 0
    hung = {r["task"]: r for r in output["results"] if r["harness"] == hang}
    assert hung["hard-1"]["returncode"] == 124


def test_harness_significance_uses_the_unrounded_adjusted_p():
    from dokimi.harnesses.runner import _correct_across_harnesses

    # Holm over three: 3 x 0.0166662 = 0.0499986 is significant, though it
    # is recorded to 4 decimals as 0.05.
    comparisons = {h: {"mcnemar": {"p_exact": 0.0166662}} for h in ("a", "b", "c")}
    _correct_across_harnesses(comparisons)
    for cr in comparisons.values():
        assert cr["mcnemar"]["p_adjusted"] == 0.05
        assert cr["mcnemar"]["significant_adjusted"] is True


def test_command_harness_ignores_a_reward_left_by_a_previous_run(tmp_path):
    task = load_task(_fixture_task(tmp_path / "tasks"))
    cell = tmp_path / "out" / "cell"
    (cell / "a-old-job").mkdir(parents=True)
    (cell / "a-old-job" / "reward.txt").write_text("1.0")
    script = (
        "import os, pathlib; "
        "d = pathlib.Path(os.environ['DOKIMI_OUTPUT_DIR']) / 'b-new-job'; "
        "d.mkdir(parents=True); (d / 'reward.txt').write_text('0.0')"
    )
    harness = CommandHarness("fake", f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}")

    result = harness.run(task, output_dir=cell)

    assert (result.passed, result.reward) == (False, 0.0)
    assert not (cell / "a-old-job").exists()


def _rmtree_that_cannot_delete(path, ignore_errors=False, onerror=None, **kwargs):
    """``shutil.rmtree`` on a tree it may not delete (root-owned, written by a container)."""
    if not (ignore_errors or onerror):  # with a hook, its retry fails too
        raise PermissionError(13, "Permission denied", str(path))


def test_cell_whose_previous_run_dir_cannot_be_cleared_is_errored_not_scored(
    tmp_path, monkeypatch
):
    import shutil

    from dokimi.harnesses.runner import _safe_id

    _fixture_task(tmp_path / "tasks")
    # The agent writes no reward and exits 0; it runs from tmp_path (the dir above the tasks root).
    (tmp_path / "agent.py").write_text("open('agent-ran', 'w').close()\n", encoding="utf-8")
    harness_id = f"command:{Path(sys.executable).as_posix()} agent.py"
    runs = tmp_path / "runs"
    cell = runs / "toy-task" / _safe_id(harness_id)
    (cell / "old-job").mkdir(parents=True)
    (cell / "old-job" / "reward.txt").write_text("1.0")
    ran = tmp_path / "agent-ran"
    monkeypatch.setattr(shutil, "rmtree", _rmtree_that_cannot_delete)

    output = TaskSuiteRunner(
        tasks=discover_tasks(tmp_path / "tasks"), harnesses=[harness_id], output_root=runs
    ).run()

    validate_artifact("task_runs", output)
    [row] = output["results"]
    assert (row["passed"], row["reward"]) == (False, None), "the stale 1.0 was read as this run's"
    assert "could not clear" in row["metrics"]["error"]
    assert str(cell.absolute()) in row["metrics"]["error"]
    assert not ran.exists(), "the agent ran on top of the previous run's files"
    stats = output["summary"]["per_harness"][harness_id]
    assert (stats["errored"], stats["scored"]) == (1, 0)
    assert (cell / "old-job" / "reward.txt").read_text() == "1.0"  # left for the operator


def test_clear_run_dir_error_gives_a_remedy_that_fits_any_cause(tmp_path, monkeypatch):
    import shutil

    cell = tmp_path / "cell"
    cell.mkdir()
    monkeypatch.setattr(shutil, "rmtree", _rmtree_that_cannot_delete)

    with pytest.raises(RuntimeError) as excinfo:
        clear_run_dir(cell)

    message = str(excinfo.value)
    assert f"could not clear {cell}" in message
    assert "(PermissionError: [Errno 13] Permission denied" in message
    assert "owner and permissions" in message and "Windows" in message
    assert "root" not in message.replace(str(tmp_path), "")


def test_clear_run_dir_deletes_read_only_files_windows_will_not_unlink(tmp_path, monkeypatch):
    """Windows refuses to delete a read-only file (git objects); the clear retries it writable."""
    cell = tmp_path / "cell"
    obj = cell / "repo" / ".git" / "objects" / "ab" / "cdef01"
    obj.parent.mkdir(parents=True)
    obj.write_text("blob")
    (cell / "reward.txt").write_text("1.0")
    obj.chmod(stat.S_IREAD)
    refused = []
    real_unlink = os.unlink

    def windows_unlink(path, *, dir_fd=None):
        if not os.stat(path, dir_fd=dir_fd, follow_symlinks=False).st_mode & stat.S_IWRITE:
            refused.append(path)
            raise PermissionError(13, "Access is denied", path)
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", windows_unlink)

    clear_run_dir(cell)

    assert refused, "the read-only file was never refused: Windows was not simulated"
    assert not cell.exists()


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs POSIX modes that bind"
)
def test_clear_run_dir_does_not_chmod_through_a_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    outside.chmod(0o444)
    cell = tmp_path / "cell"
    cell.mkdir()
    (cell / "link").symlink_to(outside)
    cell.chmod(0o555)  # the link cannot be unlinked, so the retry hook sees it
    try:
        with pytest.raises(RuntimeError, match="could not clear"):
            clear_run_dir(cell)
        # The retry only adds the write bit: what is left stays listable.
        assert stat.S_IMODE(cell.stat().st_mode) & 0o555 == 0o555
    finally:
        cell.chmod(0o755)

    assert stat.S_IMODE(outside.stat().st_mode) == 0o444
    assert outside.read_text() == "keep"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs POSIX modes that bind"
)
def test_clear_run_dir_on_an_unreadable_dir_is_a_clean_cell_error(tmp_path):
    """rmtree reports a failed os.open on a mode-000 dir; the retry hook must not raise TypeError."""
    cell = tmp_path / "cell"
    (cell / "job").mkdir(parents=True)
    (cell / "job" / "reward.txt").write_text("1.0")
    (cell / "job").chmod(0)
    try:
        with pytest.raises(RuntimeError, match="could not clear"):
            clear_run_dir(cell)
    finally:
        (cell / "job").chmod(0o755)


def test_command_harness_reads_markers_from_stderr(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    fake = FakeRunner(lambda call: reply("reward: 1.0", 'DOKIMI_OP {"type": "tool", "name": "on-stderr"}\n'))
    result = CommandHarness("fake", "fake-agent --task {task_path}", runner=fake).run(
        task, output_dir=tmp_path / "out"
    )
    assert "on-stderr" in [op.name for op in result.operations]


def test_command_harness_non_positive_timeout_is_a_scored_timeout(tmp_path):
    """A budget of -1 was an immediate timeout (rc 124, reward 0), not an errored cell."""
    task = load_task(_fixture_task(tmp_path))
    fake = FakeRunner(lambda call: reply("reward: 1.0"))
    result = CommandHarness("fake", "fake-agent --task {task_path}", runner=fake).run(
        task, output_dir=tmp_path / "out", timeout_sec=-1
    )
    assert fake.calls == []
    assert result.returncode == 124 and result.reward == 0.0 and not result.passed


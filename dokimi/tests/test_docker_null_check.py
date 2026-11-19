# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The null check (intel/dokimi#808, G6): a task's untouched start must fail its tests.

No docker here: a :class:`DockerCLI` stands for the daemon and its verifier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.harnesses import create_harness, known_harnesses, load_task, null_check
from dokimi.harnesses.docker_solver import DockerSolverHarness
from dokimi.utils import InputError

from docker_cli_fake import DockerCLI, reply

FAILED_TWO = "F F\n2 failed, 1 passed in 0.52s\n"
ERRORS_THREE = "E E E\n=== 1 passed, 3 errors in 0.20s ===\n"
ALL_PASS = "...\n3 passed in 0.10s\n"


def _harbor(root: Path, name: str = "null-task") -> Path:
    task = root / name
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True)
    (task / "solution" / "solve.sh").write_text("#!/bin/bash\necho solved\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("#!/bin/bash\npytest -q\n", encoding="utf-8")
    (task / "instruction.md").write_text("Fix it.", encoding="utf-8")
    (task / "task.toml").write_text(
        '[metadata]\ntags = ["mkl"]\n\n[environment]\nworkdir = "/app"\n', encoding="utf-8"
    )
    return task


def _verifier(stdout: str, returncode: int):
    """A daemon whose in-container verifier prints ``stdout`` and exits ``returncode``."""
    def answer(call):
        if "-lc" in call.argv:
            return reply(stdout, returncode=returncode)
        return None
    return answer


# ── pytest summary parsing ──────────────────────────────────────────────────


@pytest.mark.parametrize(("output", "expected"), [
    ("\x1b[31m1 failed\x1b[0m in 0.10s\n", {"failed": 1}),
    ("===== 2 failed, 1 passed in 0.52 seconds =====\n", {"failed": 2, "passed": 1}),
    (FAILED_TWO, {"failed": 2, "passed": 1}),
    (ERRORS_THREE, {"passed": 1, "error": 3}),
    ("=== 4 failed in 1.00s ===\n", {"failed": 4}),
    ("1 failed, 2 passed, 1 warning in 0.10s\n", {"failed": 1, "passed": 2, "warning": 1}),
    ("=== 2 skipped, 1 error in 0.01s ===\n", {"skipped": 2, "error": 1}),
    ("1 xfailed, 1 error in 0.01s\n", {"xfailed": 1, "error": 1}),
    ("1 warning, 1 error in 0.02s\n", {"warning": 1, "error": 1}),
    ("1 failed in 0.50s\n3 warnings in 2.00s\n", {"failed": 1}),
])
def test_pytest_counts_reads_the_last_summary(output, expected):
    assert null_check.pytest_counts(output) == expected


def test_pytest_counts_takes_the_last_summary_line():
    assert null_check.pytest_counts(FAILED_TWO + ALL_PASS) == {"passed": 3}


@pytest.mark.parametrize("output", ["", "no tests ran in 0.01s\n", "Segmentation fault\n"])
def test_pytest_counts_without_a_summary_is_none(output):
    assert null_check.pytest_counts(output) is None


# ── one null cell's status ──────────────────────────────────────────────────


@pytest.mark.parametrize(("reward", "counts", "status"), [
    (0.0, {"failed": 2, "passed": 1}, null_check.NULL_FAIL),
    (0.0, {"failed": 1, "error": 0}, null_check.NULL_FAIL),
    (1.0, {"passed": 3}, null_check.NULL_PASS),  # the tests pass on the untouched start
    (1.0, None, null_check.NULL_PASS),  # a verdict of pass wins whatever the summary says
    (0.0, {"passed": 1, "error": 3}, null_check.FIXTURE_ERROR),  # errors: no test judged it
    (0.0, {"failed": 1, "error": 1}, null_check.FIXTURE_ERROR),
    (0.0, {"passed": 3}, null_check.NO_VERDICT),  # all passed yet reward 0: unexplained
    (0.0, None, null_check.NO_VERDICT),
    (None, {"failed": 2}, null_check.NO_VERDICT),  # no reward at all
])
def test_null_status(reward, counts, status):
    assert null_check.null_status(reward, counts) == status


# ── per-task verdicts ───────────────────────────────────────────────────────


def _row(task, harness, *, passed=None, status=None):
    metrics = {} if status is None else {"null_check": status}
    return {"task": task, "harness": harness, "passed": passed, "metrics": metrics}


def test_task_verdicts_pair_oracle_and_null_per_task():
    results = [
        _row("a", null_check.ORACLE, passed=True),
        _row("a", null_check.NULL, status=null_check.NULL_FAIL),
        _row("b", null_check.ORACLE, passed=True),
        _row("b", null_check.NULL, status=null_check.NULL_PASS),
        _row("c", null_check.NULL, status=null_check.FIXTURE_ERROR),
    ]
    verdicts = null_check.task_verdicts(results)
    assert verdicts == {
        "a": ("pass", null_check.NULL_FAIL),
        "b": ("pass", null_check.NULL_PASS),
        "c": ("missing", null_check.FIXTURE_ERROR),
    }
    assert null_check.all_pass(verdicts) is False


def test_task_verdicts_with_repeats_need_every_null_repeat_to_fail():
    results = [
        _row("a", null_check.ORACLE, passed=True),
        _row("a", null_check.ORACLE, passed=True),
        _row("a", null_check.NULL, status=null_check.NULL_FAIL),
        _row("a", null_check.NULL, status=null_check.NO_VERDICT),
    ]
    assert null_check.task_verdicts(results)["a"] == ("pass", null_check.NO_VERDICT)


def test_task_verdicts_with_repeats_need_every_oracle_repeat_to_pass():
    results = [
        _row("a", null_check.ORACLE, passed=True),
        _row("a", null_check.ORACLE, passed=False),
        _row("a", null_check.NULL, status=null_check.NULL_FAIL),
        _row("a", null_check.NULL, status=null_check.NULL_FAIL),
    ]
    verdicts = null_check.task_verdicts(results)
    assert verdicts["a"] == ("fail", null_check.NULL_FAIL)
    assert null_check.all_pass(verdicts) is False


def test_all_pass_needs_at_least_one_task_and_both_cells_ok():
    assert null_check.all_pass({}) is False
    assert null_check.all_pass({"a": ("pass", null_check.NULL_FAIL)}) is True
    assert null_check.all_pass({"a": ("fail", null_check.NULL_FAIL)}) is False


# ── the harness ─────────────────────────────────────────────────────────────


def test_registry_builds_the_null_mode():
    assert "docker-null" in known_harnesses()
    harness = create_harness("docker-null")
    assert isinstance(harness, DockerSolverHarness)
    assert harness.mode == "null"


def test_docker_null_run_fails_the_tests_without_solving(tmp_path):
    task = load_task(_harbor(tmp_path))
    cli = DockerCLI(reward="0.0", answer=_verifier(FAILED_TWO, returncode=1))
    result = create_harness("docker-null", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.metrics["null_check"] == null_check.NULL_FAIL
    assert result.passed is True
    assert not any("solve.sh" in " ".join(c.argv) for c in cli.calls), "the null cell must not solve"


def test_docker_null_run_that_passes_on_the_start_is_not_ok(tmp_path):
    task = load_task(_harbor(tmp_path))
    cli = DockerCLI(reward="1.0", answer=_verifier(ALL_PASS, returncode=0))
    result = create_harness("docker-null", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.metrics["null_check"] == null_check.NULL_PASS
    assert result.passed is False


def test_docker_null_run_with_fixture_errors_is_not_ok(tmp_path):
    task = load_task(_harbor(tmp_path))
    cli = DockerCLI(reward="0.0", answer=_verifier(ERRORS_THREE, returncode=1))
    result = create_harness("docker-null", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.metrics["null_check"] == null_check.FIXTURE_ERROR
    assert result.passed is False


def test_docker_oracle_run_has_no_null_check_metric(tmp_path):
    task = load_task(_harbor(tmp_path))
    cli = DockerCLI(reward="1.0", solver="")
    result = create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")

    assert "null_check" not in result.metrics
    assert result.passed is True


# ── the CLI: pairing, verdict, and help ─────────────────────────────────────


def _run(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_tasks_run_null_without_oracle_is_refused(tmp_path):
    _harbor(tmp_path)
    with pytest.raises(InputError, match="docker-null gives a verdict only with its oracle"):
        _run(["tasks", "run", "--tasks", "null-task", "--tasks-root", str(tmp_path),
              "--harnesses", "docker-null", "--out-dir", str(tmp_path / "out")])


def test_tasks_run_null_with_baseline_oracle_is_not_refused(tmp_path, capsys):
    _harbor(tmp_path)
    _run(["tasks", "run", "--tasks", "null-task", "--tasks-root", str(tmp_path),
          "--harnesses", "docker-null", "--baseline-harness", "docker-oracle", "--dry-run",
          "--out-dir", str(tmp_path / "out")])
    assert "Also running baseline harness: docker-oracle" in capsys.readouterr().out


def test_print_null_check_fails_when_no_task_ran(capsys):
    from dokimi.commands.tasks import _print_null_check

    assert _print_null_check({"results": []}) is False
    assert "Null check failed: no task ran" in capsys.readouterr().err


def test_print_null_check_lists_each_task(capsys):
    from dokimi.commands.tasks import _print_null_check

    output = {"results": [
        _row("a", null_check.ORACLE, passed=True),
        _row("a", null_check.NULL, status=null_check.NULL_FAIL),
    ]}
    assert _print_null_check(output) is True
    assert "a  oracle=pass  null=null_fail" in capsys.readouterr().out


def test_tasks_run_exits_1_when_the_untouched_start_passes(tmp_path, monkeypatch, capsys):
    import dokimi.harnesses as harnesses

    _harbor(tmp_path)
    # The daemon's verifier passes whatever runs: the oracle is fine, the null cell is not.
    cli = DockerCLI(reward="1.0", answer=_verifier(ALL_PASS, returncode=0))
    real = harnesses.TaskSuiteRunner
    monkeypatch.setattr(harnesses, "TaskSuiteRunner", lambda **kw: real(runner=cli, **kw))
    with pytest.raises(SystemExit) as exc:
        _run(["tasks", "run", "--tasks", "null-task", "--tasks-root", str(tmp_path),
              "--harnesses", "docker-oracle,docker-null", "--out-dir", str(tmp_path / "out")])
    assert exc.value.code == 1
    assert "null=null_pass" in capsys.readouterr().out


def test_tasks_run_help_lists_docker_oracle(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["tasks", "run", "--help"])
    text = capsys.readouterr().out
    assert "docker-oracle" in text
    assert "docker-null" in text

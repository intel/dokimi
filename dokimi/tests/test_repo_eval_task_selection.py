# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reusable repo-eval workflow's ``Select tasks`` step, run for real.

The step's Python is lifted out of ``.github/workflows/repo-eval.yml`` and run in a
scratch git repository with the environment GitHub gives it, so a change to the
selector (path splitting, the smoke set, the manifest comparison) fails here and not
on a caller's pull request.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "repo-eval.yml"
EVAL_DIR = ".agent-eval"
TASKS = f"{EVAL_DIR}/tasks"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _steps() -> list[dict[str, Any]]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [s for job in doc["jobs"].values() for s in job.get("steps", [])]


def _select_script() -> str:
    run = next(s for s in _steps() if s.get("id") == "select")["run"]
    match = re.search(r"python - <<'PY'\n(.*)\nPY\s*$", run, re.S)
    assert match, "the Select tasks step is no longer a python heredoc"
    return match.group(1)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for cmd in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(root, *cmd)
    for task in ("t1", "my task"):
        _write(root, f"{TASKS}/{task}/task.json", "{}")
        _write(root, f"{TASKS}/{task}/grader.py")
    _write(root, "README.md")
    _write(root, f"{EVAL_DIR}/repo-eval.yaml")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def _run_step(
    repo: Path,
    tmp_path: Path,
    *,
    commit: bool = True,
    manifest_tasks: str | None = TASKS,
    **overrides: str,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Commit the working tree (unless ``commit`` is false) and run the step on HEAD."""
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "change", "--allow-empty")
    temp = tmp_path / "runner"
    temp.mkdir(exist_ok=True)
    (temp / "validate.json").write_text(
        json.dumps({"manifest": {"id": "demo", "tasks": manifest_tasks, "eval_dir": EVAL_DIR}}),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "RUNNER_TEMP": str(temp),
        "GITHUB_OUTPUT": str(temp / "out"),
        "GITHUB_STEP_SUMMARY": str(temp / "summary"),
        "EVENT": "pull_request",
        "BEFORE": "",
        "BASE_SHA": "origin/main",
        "WORKFLOW_REF": "o/r/.github/workflows/ci.yml@refs/heads/x",
        "REPOSITORY": "o/r",
        "MANIFEST": f"{EVAL_DIR}/repo-eval.yaml",
        "TASKS": "changed",
        "SMOKE_TASKS": "t1",
        "SMOKE_PATHS": "",
        "EXCLUDE_TASKS": "",
        **overrides,
    }
    (temp / "out").write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", _select_script()],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, temp


def _select(repo: Path, tmp_path: Path, **overrides: str) -> list[str]:
    result, temp = _run_step(repo, tmp_path, **overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(
        ln
        for ln in (temp / "out").read_text(encoding="utf-8").splitlines()
        if ln.startswith("tasks=")
    )
    return json.loads(line.removeprefix("tasks="))


@pytest.mark.outcome("cjm-repo-eval-selection")
def test_a_task_directory_with_a_space_is_selected_whole(repo, tmp_path):
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path) == ["my task"]


def test_a_quoted_file_name_does_not_hide_its_task(repo, tmp_path):
    _write(repo, f'{TASKS}/t1/we"irdé name.txt')
    assert _select(repo, tmp_path) == ["t1"]


def test_both_sides_of_a_move_between_tasks_are_selected(repo, tmp_path):
    _write(repo, f"{TASKS}/t1/big.py", "".join(f"line {i}\n" for i in range(200)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "more")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / TASKS / "t1" / "big.py").rename(repo / TASKS / "my task" / "big.py")
    assert _select(repo, tmp_path) == ["my task", "t1"]


def test_a_shared_helper_directly_in_the_tasks_dir_runs_the_smoke_set(repo, tmp_path):
    _write(repo, f"{TASKS}/common.py")
    assert _select(repo, tmp_path) == ["t1"]


def test_a_shared_directory_in_the_tasks_dir_runs_the_smoke_set(repo, tmp_path):
    _write(repo, f"{TASKS}/_shared/lib.py")
    assert _select(repo, tmp_path) == ["t1"]


def test_a_deleted_task_directory_runs_the_smoke_set(repo, tmp_path):
    shutil.rmtree(repo / TASKS / "my task")
    assert _select(repo, tmp_path) == ["t1"]


@pytest.mark.outcome("cjm-repo-eval-selection")
def test_a_harness_file_in_the_eval_dir_adds_the_smoke_set_to_the_changed_task(repo, tmp_path):
    _write(repo, f"{EVAL_DIR}/Dockerfile")
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path) == ["my task", "t1"]


@pytest.mark.parametrize(
    "manifest", [f"{EVAL_DIR}/repo-eval.yaml", f"./{EVAL_DIR}/../repo-eval.yaml"]
)
def test_the_manifest_is_compared_normalised(repo, tmp_path, manifest):
    if manifest.startswith("./"):
        _write(repo, "repo-eval.yaml")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "m")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _write(repo, "repo-eval.yaml", "changed\n")
    else:
        _write(repo, f"{EVAL_DIR}/repo-eval.yaml", "changed\n")
    assert _select(repo, tmp_path, MANIFEST=manifest) == ["t1"]


@pytest.mark.outcome("cjm-repo-eval-selection")
def test_an_empty_smoke_set_means_every_task(repo, tmp_path):
    _write(repo, f"{EVAL_DIR}/Dockerfile")
    assert _select(repo, tmp_path, SMOKE_TASKS="") == ["my task", "t1"]


def test_a_guidance_only_manifest_checks_no_task(repo, tmp_path):
    _write(repo, f"{EVAL_DIR}/Dockerfile")
    assert _select(repo, tmp_path, manifest_tasks=None) == []


@pytest.mark.outcome("cjm-repo-eval-selection")
def test_an_unrelated_file_selects_nothing(repo, tmp_path):
    _write(repo, "README.md", "changed\n")
    assert _select(repo, tmp_path) == []


def test_markdown_in_the_tasks_dir_selects_nothing(repo, tmp_path):
    _write(repo, f"{TASKS}/README.md", "changed\n")
    assert _select(repo, tmp_path) == []


@pytest.mark.parametrize("smoke_path", [f"{EVAL_DIR}/rubric.md", f"{EVAL_DIR}/prompts/"])
def test_a_markdown_file_named_in_smoke_paths_runs_the_smoke_set(repo, tmp_path, smoke_path):
    _write(repo, f"{EVAL_DIR}/prompts/p.md", "changed\n")
    _write(repo, f"{EVAL_DIR}/rubric.md", "changed\n")
    assert _select(repo, tmp_path, SMOKE_PATHS=smoke_path) == ["t1"]


def test_an_explicit_task_list_is_checked_as_given(repo, tmp_path):
    _write(repo, "README.md", "changed\n")
    assert _select(repo, tmp_path, TASKS="t1") == ["t1"]


@pytest.mark.parametrize("tasks", ["", " ", ","])
def test_an_empty_task_list_means_changed(repo, tmp_path, tasks):
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path, TASKS=tasks) == ["my task"]


def test_all_minus_excluded_tasks(repo, tmp_path):
    _write(repo, "README.md", "changed\n")
    assert _select(repo, tmp_path, TASKS="all", EXCLUDE_TASKS="t1") == ["my task"]


def test_a_push_diffs_against_the_previous_tip(repo, tmp_path):
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path, EVENT="push", BEFORE="origin/main") == ["my task"]


@pytest.mark.outcome("cjm-repo-eval-selection")
def test_a_push_without_a_previous_tip_checks_every_task(repo, tmp_path):
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path, EVENT="push", BEFORE="0" * 40) == ["my task", "t1"]


def test_a_push_after_a_force_push_diffs_the_two_tips_directly(repo, tmp_path):
    # the old tip is not an ancestor of the new HEAD: its own change counts too
    _git(repo, "checkout", "-q", "-b", "old")
    _write(repo, f"{TASKS}/t1/grader.py", "changed\n")
    _git(repo, "commit", "-q", "-a", "-m", "old tip")
    old = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path, EVENT="push", BEFORE=old) == ["my task", "t1"]


def test_a_pull_request_without_a_base_sha_checks_every_task(repo, tmp_path):
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path, BASE_SHA="") == ["my task", "t1"]


def test_a_merge_commit_ignores_what_the_base_gained_after_the_pr_opened(repo, tmp_path):
    # base.sha stays at the fork point while the checkout merges into the base's current tip
    fork = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, f"{TASKS}/t1/grader.py", "changed\n")
    _git(repo, "commit", "-q", "-a", "-m", "pr")
    _git(repo, "checkout", "-q", "main")
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    _write(repo, f"{EVAL_DIR}/Dockerfile")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main moves on")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "pr")
    assert _select(repo, tmp_path, commit=False, BASE_SHA=fork, SMOKE_TASKS="my task") == ["t1"]


def test_a_change_to_the_calling_workflow_runs_the_smoke_set(repo, tmp_path):
    _write(repo, ".github/workflows/ci.yml", "changed\n")
    assert _select(repo, tmp_path) == ["t1"]


def test_every_path_that_triggers_the_self_check_runs_the_smoke_set(repo, tmp_path):
    self_check = WORKFLOW.with_name("repo-eval-self.yml")
    doc = yaml.safe_load(self_check.read_text(encoding="utf-8"))
    paths = doc[True]["pull_request"]["paths"]  # YAML 1.1 reads the `on` key as True
    caller = f"o/r/.github/workflows/{self_check.name}@refs/pull/1/merge"
    # a file each filter matches: `**` as a nested file, `*` as nothing and as a suffix
    files = sorted(
        {
            p.replace("**", "x/y.py").replace("*", star)
            for p in paths
            if not p.startswith(f"{EVAL_DIR}/")
            for star in ("", "_x")
        }
    )
    missed = []
    for f in files:
        _write(repo, f, "changed\n")
        smoke = doc["jobs"]["self"]["with"]["smoke-paths"]
        if _select(repo, tmp_path, SMOKE_PATHS=smoke, WORKFLOW_REF=caller) != ["t1"]:
            missed.append(f)
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert files and not missed


@pytest.mark.parametrize("smoke_paths", ["./vendor/", "vendor/,other/", "other/ vendor/"])
def test_smoke_paths_are_normalised_and_split_like_ids(repo, tmp_path, smoke_paths):
    _write(repo, "vendor/grader/lib.py", "changed\n")
    assert _select(repo, tmp_path, SMOKE_PATHS=smoke_paths) == ["t1"]


def test_a_smoke_directory_without_its_slash_is_a_prefix_as_given(repo, tmp_path):
    _write(repo, "vendor-old/x.py", "changed\n")
    assert _select(repo, tmp_path, SMOKE_PATHS="vendor/") == []


@pytest.mark.outcome("cjm-repo-eval-selection")
def test_an_unknown_explicit_task_fails_the_run(repo, tmp_path):
    result, _ = _run_step(repo, tmp_path, TASKS="nope")
    assert result.returncode != 0 and "no such task(s): nope" in result.stdout + result.stderr


def test_an_unknown_smoke_task_fails_the_run(repo, tmp_path):
    _write(repo, f"{EVAL_DIR}/Dockerfile")
    result, _ = _run_step(repo, tmp_path, SMOKE_TASKS="nope")
    assert result.returncode != 0 and "no such task(s): nope" in result.stdout + result.stderr


def test_an_unreadable_previous_tip_checks_every_task(repo, tmp_path):
    _write(repo, f"{TASKS}/my task/grader.py", "changed\n")
    assert _select(repo, tmp_path, EVENT="push", BEFORE="1" * 40) == ["my task", "t1"]


def _summary(tmp_path: Path, job_status: str) -> tuple[str, Path]:
    """Run the tasks-check Summary step with no check.json, as after a killed check."""
    run = next(s for s in _steps() if s.get("name") == "Summary")["run"]
    match = re.search(r"python - <<'PY'[^\n]*\n(.*)\nPY\s*$", run, re.S)
    assert match, "the Summary step is no longer a python heredoc"
    temp = tmp_path / job_status
    temp.mkdir()
    env = {**os.environ, "RUNNER_TEMP": str(temp), "JOB_STATUS": job_status, "TASK": "t1"}
    result = subprocess.run(
        [sys.executable, "-c", match.group(1)], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout, temp / "repo-check" / "check" / "check.json"


@pytest.mark.outcome("cjm-repo-eval-workflow")
def test_a_job_cancelled_before_the_verdict_records_a_timeout(tmp_path):
    summary, check = _summary(tmp_path, "cancelled")
    assert json.loads(check.read_text(encoding="utf-8"))["tasks"][0]["verdict"] == "timeout"
    assert "`t1`: TIMEOUT" in summary
    # a timeout cancels the job, and only always() runs a step in a cancelled job
    steps = {s.get("name"): s for s in _steps()}
    assert steps["Summary"]["if"] == steps["Upload the check results"]["if"] == "${{ always() }}"


def test_a_failed_check_without_a_verdict_invents_none(tmp_path):
    summary, check = _summary(tmp_path, "failure")
    assert not check.exists() and "No check.json" in summary


def test_the_check_results_artifact_keeps_the_stderr_files():
    upload = next(s for s in _steps() if "Upload the check results" in str(s.get("name", "")))
    paths = [ln.strip() for ln in str(upload["with"]["path"]).splitlines()]
    assert "${{ runner.temp }}/repo-check/check/*/*.err" in paths


def _tasks_check_argv(tmp_path: Path, inputs: dict[str, bool]) -> list[str]:
    """Run the ``repo tasks check`` step with a stub ``dokimi`` and return its argv."""
    step = next(s for s in _steps() if s.get("name") == "repo tasks check")
    values = {f"inputs.{k}": str(v).lower() for k, v in inputs.items()}
    values |= {"inputs.manifest": "m.yml", "matrix.task": "t1"}
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path)}
    for key, value in step["env"].items():
        ref = re.fullmatch(r"\$\{\{ ([\w.-]+) \}\}", str(value))
        env[key] = values[ref.group(1)] if ref else str(value)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "dokimi"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RUNNER_TEMP/argv"\n', encoding="utf-8")
    stub.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], env=env, check=True)
    return (tmp_path / "argv").read_text(encoding="utf-8").splitlines()


def test_allow_unsupported_is_a_boolean_input_that_is_off_by_default():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    declared = doc[True]["workflow_call"]["inputs"]["allow-unsupported"]  # `on` reads as True
    assert declared["type"] == "boolean"
    assert declared["default"] is False


@pytest.mark.outcome("cjm-repo-eval-workflow")
@pytest.mark.skipif(sys.platform == "win32", reason="the step is a bash script")
@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("allow", [False, True])
def test_the_tasks_check_step_forwards_warm_and_allow_unsupported(tmp_path, warm, allow):
    argv = _tasks_check_argv(tmp_path, {"warm": warm, "allow-unsupported": allow})
    assert argv[:6] == ["repo", "tasks", "check", ".", "--manifest", "m.yml"]
    assert ("--warm" in argv) is warm
    assert ("--allow-unsupported" in argv) is allow

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the standalone Docker solver harness and its telemetry rollup.

Docker itself is never invoked here: the pipeline steps are monkeypatched so the
tests cover JSON-telemetry parsing, the reward contract, repeats, and the
cost/per-task summary aggregation — the parts that turn raw runs into tracked,
saved data.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.harnesses import TaskSuiteRunner, load_task
from dokimi.harnesses.docker_solver import (
    DockerSolverHarness,
    _DEFAULT_BUILD_TIMEOUT,
    _DEFAULT_VERIFY_TIMEOUT,
    _last_json_object,
    _parse_claude_json,
    _task_build_timeout,
    _task_verify_timeout,
)
from dokimi.harnesses.registry import create_harness, known_harnesses, with_skill_counterpart
from dokimi.harnesses.runner import summarize_task_results

# A representative `claude -p --output-format json` result line.
_CLAUDE_JSON = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 4200,
        "ttft_ms": 1646,
        "num_turns": 5,
        "result": "done",
        "total_cost_usd": 0.0731,
        "usage": {
            "input_tokens": 6086,
            "output_tokens": 512,
            "cache_read_input_tokens": 1024,
            "cache_creation_input_tokens": 16389,
        },
    }
)


def _fixture_task(tmp_path: Path) -> Path:
    task = tmp_path / "intel-perf-toy"
    (task / "tests").mkdir(parents=True)
    (task / "environment").mkdir()
    (task / "solution").mkdir()
    (task / "instruction.md").write_text(
        "Optimize the loop. Recompile with `g++ -O3 bench.cpp -o /app/bench`.",
        encoding="utf-8",
    )
    (task / "task.toml").write_text(
        '[metadata]\ndifficulty = "medium"\ntags = ["c++", "perf"]\n'
        "[agent]\ntimeout_sec = 30\n",
        encoding="utf-8",
    )
    return task


# ── telemetry parsing ───────────────────────────────────────────────────────


def test_parse_claude_json_maps_full_telemetry():
    usage = _parse_claude_json(_CLAUDE_JSON, "t", "haiku", latency=9.9)
    assert usage is not None
    assert usage.prompt_tokens == 6086
    assert usage.completion_tokens == 512
    assert usage.total_tokens == 6598
    assert usage.cache_read_tokens == 1024
    assert usage.cache_write_tokens == 16389
    assert usage.cost_usd == 0.0731
    assert usage.cost_known_calls == 1
    assert usage.n_calls == 5
    # duration_ms wins over the wall-clock fallback; ttft is converted to seconds.
    assert usage.latency_sec == 4.2
    assert usage.ttft_sec == 1.646


def test_parse_claude_json_returns_none_on_garbage():
    assert _parse_claude_json("not json at all", "t", "m", 1.0) is None


def test_parse_claude_json_uses_wallclock_when_duration_absent():
    payload = json.dumps({"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 2}})
    usage = _parse_claude_json(payload, "t", "m", latency=3.5)
    assert usage is not None
    assert usage.latency_sec == 3.5
    assert usage.cost_usd is None
    assert usage.cost_known_calls == 0


def test_last_json_object_picks_final_result_line():
    text = "log noise\n{\"a\": 1}\nINFO building\n" + _CLAUDE_JSON + "\n"
    obj = _last_json_object(text)
    assert obj["total_cost_usd"] == 0.0731


# ── task-driven timeouts ──────────────────────────────────────────────────────


def test_build_timeout_honors_task_metadata(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    # fixture has no build_timeout_sec -> default fallback
    assert _task_build_timeout(task) == _DEFAULT_BUILD_TIMEOUT
    assert _task_verify_timeout(task) == _DEFAULT_VERIFY_TIMEOUT


def test_build_and_verify_timeouts_read_toml(tmp_path):
    task = tmp_path / "intel-perf-slow-build"
    (task / "tests").mkdir(parents=True)
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("x", encoding="utf-8")
    (task / "task.toml").write_text(
        "[metadata]\ntags = []\n"
        "[environment]\nbuild_timeout_sec = 900\n"
        "[verifier]\ntimeout_sec = 120\n",
        encoding="utf-8",
    )
    spec = load_task(task)
    assert _task_build_timeout(spec) == 900.0
    assert _task_verify_timeout(spec) == 120.0


# ── container network/resources from task.environment ────────────────────────


def _capture_docker_calls(monkeypatch):
    import dokimi.harnesses.docker_solver as mod

    calls: list[list[str]] = []

    def fake_docker(args, log, *, check=True, timeout=None):
        calls.append(args)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(mod, "_docker", fake_docker)
    return calls


def test_start_container_defaults_to_network_none(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    calls = _capture_docker_calls(monkeypatch)

    DockerSolverHarness(harness_id="docker-oracle", mode="oracle")._start_container(
        task, "img:latest", "container-1", log=None
    )

    run_call = next(c for c in calls if c[0] == "run")
    assert "--network" in run_call and run_call[run_call.index("--network") + 1] == "none"


def test_start_container_honors_public_network_mode(tmp_path, monkeypatch):
    task_dir = tmp_path / "external-net-task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "environment").mkdir()
    (task_dir / "instruction.md").write_text("x", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        '[metadata]\ntags = []\n\n[environment]\nnetwork_mode = "public"\ncpus = 2\nmemory_mb = 512\n',
        encoding="utf-8",
    )
    task = load_task(task_dir)
    calls = _capture_docker_calls(monkeypatch)

    DockerSolverHarness(harness_id="docker-oracle", mode="oracle")._start_container(
        task, "img:latest", "container-2", log=None
    )

    run_call = next(c for c in calls if c[0] == "run")
    # bridge, not host: outbound internet without exposing host-local
    # services to untrusted task code.
    assert run_call[run_call.index("--network") + 1] == "bridge"
    assert run_call[run_call.index("--cpus") + 1] == "2"
    assert run_call[run_call.index("--memory") + 1] == "512m"


def test_start_container_zero_cpus_is_honored_not_defaulted(tmp_path, monkeypatch):
    # Regression: `task.environment.get("cpus") or 4` would silently
    # discard an explicit `cpus = 0` because 0 is falsy.
    task_dir = tmp_path / "zero-cpus-task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "environment").mkdir()
    (task_dir / "instruction.md").write_text("x", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        '[metadata]\ntags = []\n\n[environment]\ncpus = 0\n',
        encoding="utf-8",
    )
    task = load_task(task_dir)
    calls = _capture_docker_calls(monkeypatch)

    DockerSolverHarness(harness_id="docker-oracle", mode="oracle")._start_container(
        task, "img:latest", "container-4", log=None
    )

    run_call = next(c for c in calls if c[0] == "run")
    assert run_call[run_call.index("--cpus") + 1] == "0"


def test_start_container_malformed_memory_falls_back_instead_of_crashing(tmp_path, monkeypatch):
    # Regression: `int(memory_mb)` raised uncaught if a task.toml author
    # wrote a unit-suffixed string (e.g. "512m") instead of a bare number.
    task_dir = tmp_path / "bad-memory-task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "environment").mkdir()
    (task_dir / "instruction.md").write_text("x", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        '[metadata]\ntags = []\n\n[environment]\nmemory_mb = "512m"\n',
        encoding="utf-8",
    )
    task = load_task(task_dir)
    calls = _capture_docker_calls(monkeypatch)

    DockerSolverHarness(harness_id="docker-oracle", mode="oracle")._start_container(
        task, "img:latest", "container-5", log=None
    )

    run_call = next(c for c in calls if c[0] == "run")
    assert run_call[run_call.index("--memory") + 1] == "2g"


def test_solve_oracle_copies_the_whole_solution_directory(tmp_path, monkeypatch):
    # Regression test: a real run against uxlfoundation/skills'
    # onetbb-histogram-local-aggregation failed (reward 0) because solve.sh
    # did `cp /solution/histogram.cpp ...` -- a sibling file that only
    # exists if the whole solution/ dir is copied in, not just solve.sh.
    task = load_task(_fixture_task(tmp_path))
    calls = _capture_docker_calls(monkeypatch)

    DockerSolverHarness(harness_id="docker-oracle", mode="oracle")._solve_oracle(
        task, "container-3", log=None
    )

    cp_call = next(c for c in calls if c[0] == "cp")
    assert cp_call[1] == f"{task.path / 'solution'}/."
    assert cp_call[2] == "container-3:/solution"

    exec_call = [c for c in calls if c[0] == "exec"][-1]
    assert exec_call[-1] == "chmod +x /solution/solve.sh && /solution/solve.sh"


# ── registry wiring ───────────────────────────────────────────────────────────


def test_registry_builds_docker_harnesses(tmp_path):
    assert isinstance(create_harness("docker-oracle"), DockerSolverHarness)
    assert create_harness("docker-oracle").mode == "oracle"
    claude = create_harness("docker-claude")
    assert claude.mode == "claude" and claude.skill_path is None
    (tmp_path / "SKILL.md").write_text("x", encoding="utf-8")
    skill = create_harness(f"docker-claude-skill:{tmp_path / 'SKILL.md'}")
    assert skill.skill_path == str(tmp_path / "SKILL.md")
    assert "docker-claude" in known_harnesses()


@pytest.mark.parametrize("prefix", ["docker-claude", "docker-claude-host"])
def test_registry_builds_the_discover_arm_beside_the_skill_arm(tmp_path, prefix):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("x", encoding="utf-8")
    skill = create_harness(f"{prefix}-skill:{skill_md}")
    discover = create_harness(f"{prefix}-skill-discover:{skill_md}")
    assert skill.skill_notice is True and discover.skill_notice is False
    assert (discover.skill_path, discover.agent_location) == (skill.skill_path, skill.agent_location)
    assert f"{prefix}-skill-discover:<skill>" in known_harnesses()
    assert with_skill_counterpart(f"{prefix}-skill-discover:{skill_md}") == f"{prefix}-skill:{skill_md}"
    assert with_skill_counterpart(f"{prefix}-skill:{skill_md}") is None
    with pytest.raises(ValueError, match="requires a skill path"):
        create_harness(f"{prefix}-skill-discover:")
    task = load_task(_fixture_task(tmp_path))
    assert "--no-skill-notice" in discover.run(task, dry_run=True).command
    assert "--no-skill-notice" not in skill.run(task, dry_run=True).command


def _arm_row(task, harness, passed, *, invoked=None, error=None, repeat=1):
    metrics: dict = {"operation_count": 0, "operations_by_type": {}}
    if invoked is not None:
        metrics["n_skill_invocations"] = invoked
    if error:
        metrics["solver_error"] = error
    return {"task": task, "harness": harness, "passed": passed, "elapsed_sec": 1.0,
            "reward": 1.0 if passed else 0.0, "repeat": repeat, "metrics": metrics}


def test_summary_reports_the_discovery_gap_per_pair():
    base, skill, disc = "docker-claude", "docker-claude-skill:s", "docker-claude-skill-discover:s"
    rows = []
    for rep in (1, 2):
        rows += [_arm_row("a", base, False, repeat=rep),
                 _arm_row("a", skill, True, invoked=1, repeat=rep),
                 _arm_row("a", disc, rep == 1, invoked=0, repeat=rep)]
    rows.append(_arm_row("a", "docker-claude-skill-discover:other", False, invoked=0))
    summary = summarize_task_results(rows, baseline_harness=base)
    assert summary["discovery_gaps"] == {disc: {
        "with_skill_harness": skill, "with_skill_pass_rate": 1.0, "discover_pass_rate": 0.5,
        "gap": 0.5, "attempted_cells_match": True, "errored_cells_match": True,
        "with_skill_invocation_rate": 1.0, "discover_invocation_rate": 0.0}}
    # The discovery arm is an arm of its own against the baseline, too.
    assert summary["comparisons"][disc]["pass_rate_delta"] == 0.5
    rows[-2]["metrics"]["solver_error"] = "boom"  # arms lost different cells: withheld
    lost = summarize_task_results(rows)["discovery_gaps"][disc]
    assert lost["gap"] is None
    assert lost["attempted_cells_match"] is True and lost["errored_cells_match"] is False
    assert "discovery_gaps" not in summarize_task_results(rows[:2])


def test_docker_claude_skill_requires_a_path():
    import pytest

    with pytest.raises(ValueError):
        create_harness("docker-claude-skill:")


# ── run contract (Docker steps patched out) ──────────────────────────────────


def _patch_pipeline(monkeypatch, *, reward: float, usage_json: str | None):
    """Stub the Docker-touching steps so run() exercises only host logic."""
    import dokimi.harnesses.docker_solver as mod

    # run()'s cleanup calls _docker(["rm", ...]); stub it so these stay
    # Docker-free even on hosts without a `docker` binary.
    monkeypatch.setattr(mod, "_docker", lambda *a, **k: None)
    monkeypatch.setattr(mod.ContainerSandbox, "open", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(DockerSolverHarness, "_build_image", lambda self, t, i, log: None)
    monkeypatch.setattr(DockerSolverHarness, "_start_container", lambda self, t, i, c, log, sandbox=None: None)
    monkeypatch.setattr(DockerSolverHarness, "_solve_oracle", lambda self, t, c, log: None)

    def fake_verify(self, task, container, output_dir, log):
        (output_dir / "reward.txt").write_text(str(reward), encoding="utf-8")
        return reward

    monkeypatch.setattr(DockerSolverHarness, "_verify", fake_verify)

    if usage_json is not None:
        def fake_solve_claude(self, task, container, output_dir, model, timeout,
                              operations, log, extra_metrics, sandbox=None):
            (output_dir / "solver.json").write_text(usage_json, encoding="utf-8")
            return _parse_claude_json(usage_json, task.name, model or "", 1.0)

        monkeypatch.setattr(DockerSolverHarness, "_solve_claude", fake_solve_claude)


def test_docker_claude_run_folds_cost_into_metrics(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    _patch_pipeline(monkeypatch, reward=1.0, usage_json=_CLAUDE_JSON)
    harness = create_harness("docker-claude")
    result = harness.run(task, model="haiku", output_dir=tmp_path / "out")

    assert result.passed is True
    assert result.reward == 1.0
    assert result.metrics["cost_usd"] == 0.0731
    assert result.metrics["total_tokens"] == 6598
    assert result.metrics["mode"] == "claude"


def test_docker_oracle_run_has_no_llm_cost(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    _patch_pipeline(monkeypatch, reward=0.0, usage_json=None)
    result = create_harness("docker-oracle").run(task, output_dir=tmp_path / "out")
    assert result.passed is False
    assert "cost_usd" not in result.metrics  # oracle = no LLM telemetry


def test_dry_run_skips_docker(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    result = create_harness("docker-claude").run(task, output_dir=tmp_path / "out", dry_run=True)
    assert result.metrics["dry_run"] is True
    assert result.passed is False


# ── summary rollup: cost, per-task, repeats ───────────────────────────────────


def test_summary_rolls_up_cost_and_per_task():
    rows = [
        {"task": "a", "harness": "skill", "passed": True, "elapsed_sec": 1.0, "reward": 1.0,
         "metrics": {"operation_count": 1, "operations_by_type": {"harness": 1},
                     "cost_usd": 0.05, "total_tokens": 100, "prompt_tokens": 80,
                     "completion_tokens": 20}},
        {"task": "a", "harness": "skill", "passed": False, "elapsed_sec": 1.0, "reward": 0.0,
         "metrics": {"operation_count": 1, "operations_by_type": {"harness": 1},
                     "cost_usd": 0.03, "total_tokens": 60, "prompt_tokens": 50,
                     "completion_tokens": 10}},
        {"task": "b", "harness": "skill", "passed": True, "elapsed_sec": 1.0, "reward": 1.0,
         "metrics": {"operation_count": 1, "operations_by_type": {"harness": 1},
                     "cost_usd": 0.02, "total_tokens": 40}},
    ]
    summary = summarize_task_results(rows, baseline_harness=None)
    stats = summary["per_harness"]["skill"]
    assert stats["cost"]["total_cost_usd"] == 0.1
    assert stats["cost"]["cost_known_n"] == 3
    assert stats["cost"]["total_tokens"] == 200
    # task "a" ran twice (1 pass), task "b" once (1 pass)
    assert stats["per_task"]["a"] == {"n": 2, "passed": 1, "errored": 0, "pass_rate": 0.5}
    assert stats["per_task"]["b"] == {"n": 1, "passed": 1, "errored": 0, "pass_rate": 1.0}


def test_summary_cost_delta_vs_baseline():
    rows = [
        {"task": "a", "harness": "noskill", "passed": False, "elapsed_sec": 1.0, "reward": 0.0,
         "metrics": {"operation_count": 0, "operations_by_type": {}, "cost_usd": 0.04}},
        {"task": "a", "harness": "skill", "passed": True, "elapsed_sec": 1.0, "reward": 1.0,
         "metrics": {"operation_count": 0, "operations_by_type": {}, "cost_usd": 0.10}},
    ]
    summary = summarize_task_results(rows, baseline_harness="noskill")
    cmp = summary["comparisons"]["skill"]
    assert cmp["pass_rate_delta"] == 1.0
    assert cmp["cost_delta_usd"] == 0.06


def test_summary_cost_null_when_no_pricing():
    rows = [
        {"task": "a", "harness": "h", "passed": True, "elapsed_sec": 1.0, "reward": 1.0,
         "metrics": {"operation_count": 0, "operations_by_type": {}}},
    ]
    stats = summarize_task_results(rows)["per_harness"]["h"]
    assert stats["cost"]["total_cost_usd"] is None
    assert stats["cost"]["cost_known_n"] == 0


def test_repeats_produce_multiple_rows_and_validate(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    _patch_pipeline(monkeypatch, reward=1.0, usage_json=_CLAUDE_JSON)
    suite = TaskSuiteRunner(
        tasks=[task],
        harnesses=["docker-claude"],
        model="haiku",
        output_root=tmp_path / "runs",
        repeats=3,
    )
    output = suite.run()
    validate_artifact("task_runs", output)
    assert output["repeats"] == 3
    assert len(output["results"]) == 3
    assert {r["repeat"] for r in output["results"]} == {1, 2, 3}
    stats = output["summary"]["per_harness"]["docker-claude"]
    assert stats["n"] == 3
    assert stats["cost"]["cost_known_n"] == 3


def test_one_failing_cell_does_not_abort_the_sweep(tmp_path, monkeypatch):
    """A harness crash on one task records a failed row and the sweep continues
    — the artifact still holds every other cell instead of being discarded."""
    good = _fixture_task(tmp_path)
    bad = tmp_path / "intel-perf-bad"
    (bad / "tests").mkdir(parents=True)
    (bad / "environment").mkdir()
    (bad / "instruction.md").write_text("x", encoding="utf-8")
    (bad / "task.toml").write_text('[metadata]\ntags = []\n', encoding="utf-8")

    from dokimi.harnesses.command import CommandHarness

    real_run = DockerSolverHarness.run

    def flaky_run(self, task, *, model=None, output_dir=None, timeout_sec=None, dry_run=False):
        if task.name == "intel-perf-bad":
            raise RuntimeError("docker build failed (exit 1); see run.log")
        # The good task: stub the Docker pipeline and run the real method.
        _patch_pipeline(monkeypatch, reward=1.0, usage_json=_CLAUDE_JSON)
        return real_run(self, task, model=model, output_dir=output_dir, dry_run=dry_run)

    monkeypatch.setattr(DockerSolverHarness, "run", flaky_run)
    suite = TaskSuiteRunner(
        tasks=[load_task(bad), load_task(good)],
        harnesses=["docker-claude"],
        model="haiku",
        output_root=tmp_path / "runs",
    )
    output = suite.run()

    validate_artifact("task_runs", output)
    assert len(output["results"]) == 2  # both cells present, sweep survived
    rows = {r["task"]: r for r in output["results"]}
    assert rows["intel-perf-bad"]["passed"] is False
    assert rows["intel-perf-bad"]["reward"] is None  # no verdict, not a verified 0
    assert "docker build failed" in rows["intel-perf-bad"]["metrics"]["error"]
    assert rows["intel-perf-toy"]["passed"] is True
    assert CommandHarness  # keep import for the patched module path


def test_recompile_failure_recorded_in_metrics(tmp_path, monkeypatch):
    """A failed in-container recompile is surfaced in metrics so the cell can be
    told apart from a genuine model miss (it is a tooling failure)."""
    task = load_task(_fixture_task(tmp_path))  # instruction has a `g++ ... -o /app/bench`
    import dokimi.harnesses.docker_solver as mod

    monkeypatch.setattr(mod, "_docker", lambda *a, **k: None)
    monkeypatch.setattr(mod.ContainerSandbox, "open", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(DockerSolverHarness, "_build_image", lambda self, t, i, log: None)
    monkeypatch.setattr(DockerSolverHarness, "_start_container", lambda self, t, i, c, log, sandbox=None: None)
    monkeypatch.setattr(DockerSolverHarness, "_verify", lambda self, t, c, o, log: 0.0)

    # Snapshot copy-out and the claude subprocess are stubbed; recompile "fails".
    monkeypatch.setattr(DockerSolverHarness, "_recompile",
                        lambda self, t, c, log: ["g++ -O3 bench.cpp -o /app/bench"])

    def fake_solve(self, task, container, output_dir, model, timeout, operations, log, extra_metrics,
                   sandbox=None):
        failed = self._recompile(task, container, log)
        if failed:
            extra_metrics["recompile_failed"] = failed
        return _parse_claude_json(_CLAUDE_JSON, task.name, model or "", 1.0)

    monkeypatch.setattr(DockerSolverHarness, "_solve_claude", fake_solve)
    result = create_harness("docker-claude").run(task, model="haiku", output_dir=tmp_path / "out")
    assert result.metrics["recompile_failed"] == ["g++ -O3 bench.cpp -o /app/bench"]
    assert result.passed is False


def test_api_error_is_recorded_as_a_solver_error(tmp_path, monkeypatch):
    """A refused or flagged request scores 0 like a miss; metrics must say it was not one."""
    import dokimi.harnesses.docker_solver as mod

    flagged = json.dumps({"type": "result", "is_error": True, "terminal_reason": "api_error",
                          "num_turns": 2, "total_cost_usd": 0.07,
                          "result": "API Error: request flagged\nDetails: x"})
    task = load_task(_fixture_task(tmp_path))
    monkeypatch.setattr(mod, "_docker", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(mod.ContainerSandbox, "open", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(DockerSolverHarness, "_build_image", lambda self, t, i, log: None)
    monkeypatch.setattr(DockerSolverHarness, "_start_container", lambda self, t, i, c, log, sandbox=None: None)
    monkeypatch.setattr(DockerSolverHarness, "_verify", lambda self, t, c, o, log: 0.0)
    monkeypatch.setattr(DockerSolverHarness, "_recompile", lambda self, t, c, log: [])
    real_run = subprocess.run
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: (
        subprocess.CompletedProcess(cmd, 0, flagged, "") if cmd[0] == "claude" else real_run(cmd, **kw)))
    result = create_harness("docker-claude-host").run(task, model="haiku", output_dir=tmp_path / "out")
    assert result.metrics["solver_error"] == "api_error: API Error: request flagged"
    assert result.passed is False


def test_a_completed_run_has_no_solver_error():
    from dokimi.harnesses.claude_json import solver_error

    assert solver_error(_CLAUDE_JSON) is None
    assert solver_error("") == "no result object on stdout"


def test_model_is_taken_from_the_result_when_not_given():
    obj = json.loads(_CLAUDE_JSON)
    # The first key is a cheap helper model; the run's model is the costliest entry.
    obj["modelUsage"] = {"claude-haiku-4-5": {"inputTokens": 1, "costUSD": 0.001},
                         "claude-opus-5-5": {"inputTokens": 1, "costUSD": 0.07}}
    usage = _parse_claude_json(json.dumps(obj), "t", "", latency=1.0)
    assert usage is not None and usage.model == "claude-opus-5-5"


def test_the_model_used_is_recorded_in_metrics(tmp_path, monkeypatch):
    obj = json.loads(_CLAUDE_JSON)
    obj["modelUsage"] = {"claude-sonnet-4-5": {"costUSD": 0.07}}
    task = load_task(_fixture_task(tmp_path))
    _patch_pipeline(monkeypatch, reward=1.0, usage_json=json.dumps(obj))
    result = create_harness("docker-claude").run(task, output_dir=tmp_path / "out")
    assert result.metrics["model"] == "claude-sonnet-4-5"


def test_solver_error_flags_api_errors_only():
    from dokimi.harnesses.claude_json import solver_error

    flagged = json.dumps({"type": "result", "is_error": True, "terminal_reason": "api_error",
                          "result": "Invalid API key\nmore"})
    assert solver_error(flagged) == "api_error: Invalid API key"
    assert solver_error(_CLAUDE_JSON) is None
    assert solver_error("") == "no result object on stdout"


def test_an_error_after_a_real_attempt_is_not_a_solver_error():
    from dokimi.harnesses.claude_json import solver_error, terminal_reason

    late = json.dumps({"type": "result", "is_error": True, "terminal_reason": "api_error",
                       "num_turns": 40, "total_cost_usd": 2.31, "usage": {"output_tokens": 9000},
                       "result": "API Error: 529 overloaded"})
    assert solver_error(late) is None
    assert terminal_reason(late) == "api_error"
    assert terminal_reason(_CLAUDE_JSON) == "success"


def test_solver_error_reads_the_errors_list():
    from dokimi.harnesses.claude_json import solver_error

    obj = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "num_turns": 0, "errors": ["spawn failed: ENOENT"]})
    assert solver_error(obj) == "error_during_execution: spawn failed: ENOENT"


def test_solver_error_cells_are_excluded_from_the_pairing():
    def row(task, harness, passed, **metrics):
        return {"task": task, "harness": harness, "passed": passed, "reward": 1.0 if passed else 0.0,
                "elapsed_sec": 1.0, "metrics": metrics}

    rows = [row("a", "base", True, solver_error="api_error: Invalid API key"), row("a", "skill", True)]
    for t in "bcdef":
        rows += [row(t, "base", False), row(t, "skill", True)]
    summary = summarize_task_results(rows, baseline_harness="base")
    assert summary["per_harness"]["base"]["errored"] == 1
    assert summary["per_harness"]["base"]["pass_rate"] == 0.0
    assert summary["comparisons"]["skill"]["mcnemar"]["n"] == 5
    assert summary["comparisons"]["skill"]["mcnemar"]["n_excluded"] == 1


def test_probe_and_oracle_rows_are_not_compared():
    rows = [{"task": t, "harness": h, "passed": h != "base", "reward": 0.0, "elapsed_sec": 1.0,
             "metrics": {}}
            for t in ("a", "b") for h in ("base", "skill", "docker-probe", "docker-probe:x",
                                           "docker-oracle")]
    comparisons = summarize_task_results(rows, baseline_harness="base")["comparisons"]
    assert set(comparisons) == {"skill"}
    assert comparisons["skill"]["mcnemar"]["family_size"] == 1


# ── timeouts: build is errored, solve/verify is a reward-0 failure ────────────


def test_build_timeout_raises_so_the_cell_is_errored(tmp_path, monkeypatch):
    """A build that exceeds build_timeout_sec never ran the agent, so it must
    be errored like a build that fails fast — not a reward-0 failure."""
    task = load_task(_fixture_task(tmp_path))
    _patch_pipeline(monkeypatch, reward=1.0, usage_json=None)

    def slow_build(self, t, i, log):
        raise subprocess.TimeoutExpired(["docker", "build"], 900)

    monkeypatch.setattr(DockerSolverHarness, "_build_image", slow_build)
    with pytest.raises(RuntimeError, match="docker build timed out after 900"):
        create_harness("docker-oracle").run(task, output_dir=tmp_path / "out")
    assert "BUILD TIMEOUT" in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")

    output = TaskSuiteRunner(
        tasks=[task], harnesses=["docker-oracle"], output_root=tmp_path / "runs",
    ).run()
    row = output["results"][0]
    assert row["reward"] is None
    assert "docker build timed out" in row["metrics"]["error"]
    assert output["summary"]["per_harness"]["docker-oracle"]["errored"] == 1


@pytest.mark.parametrize("step", ["_solve_oracle", "_verify"])
def test_solve_or_verify_timeout_is_a_failed_cell(tmp_path, monkeypatch, step):
    task = load_task(_fixture_task(tmp_path))
    _patch_pipeline(monkeypatch, reward=1.0, usage_json=None)

    def slow(self, *args):
        raise subprocess.TimeoutExpired(["docker", "exec"], 30)

    monkeypatch.setattr(DockerSolverHarness, step, slow)
    result = create_harness("docker-oracle").run(task, output_dir=tmp_path / "out")
    assert result.returncode == 124
    assert result.reward == 0.0
    assert result.passed is False
    assert "error" not in result.metrics

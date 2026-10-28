# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the standalone Docker solver harness and its telemetry rollup.

Docker itself is never invoked here: a :class:`DockerCLI` runner stands for the
docker CLI and ``claude``, so the tests drive the real pipeline and cover the
docker arguments, JSON-telemetry parsing, the reward and timeout contract,
repeats, and the cost/per-task summary aggregation — the parts that turn raw
runs into tracked, saved data.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from dokimi import process
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
from dokimi.process import reply
from docker_cli_fake import DockerCLI

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


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """No developer proxy, provider or Claude settings reach the launch; the host
    `claude` the in-container agent copies is a path that is never run."""
    import dokimi.harnesses.container_agent as container_mod

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-operator-config"))
    for name in list(os.environ):
        if name.lower() in ("http_proxy", "https_proxy", "no_proxy", "all_proxy") or name.startswith(
                ("ANTHROPIC_", "CLAUDE_CODE_", "AWS_", "CLOUD_ML_", "GOOGLE_", "VERTEX_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(container_mod, "claude_binary", lambda: tmp_path / "claude")


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


def _start(task, container: str) -> list[list[str]]:
    """The docker calls ``_start_container`` makes for ``task``."""
    cli = DockerCLI()
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)._start_container(
        task, "img:latest", container, io.StringIO()
    )
    return cli.args


def test_start_container_defaults_to_network_none(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    calls = _start(task, "container-1")

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
    calls = _start(task, "container-2")

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
    calls = _start(task, "container-4")

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
    calls = _start(task, "container-5")

    run_call = next(c for c in calls if c[0] == "run")
    assert run_call[run_call.index("--memory") + 1] == "2g"


def test_start_container_drops_capabilities_and_bounds_pids(tmp_path, monkeypatch):
    # Regression (#229 P2-1a): the task container had no cap drop, no-new-privileges or
    # pids limit, unlike repos/container.py. Only the named capabilities the phase steps
    # need (chown, DAC override, file owner, setuid/setgid for task scripts) are added back.
    # The flags must sit before the image: after it, docker hands them to `sleep`.
    task = load_task(_fixture_task(tmp_path))
    run_call = next(c for c in _start(task, "container-6") if c[0] == "run")
    image_at = run_call.index("img:latest")
    assert run_call[run_call.index("--cap-drop") + 1] == "ALL"
    assert run_call[run_call.index("--security-opt") + 1] == "no-new-privileges"
    assert run_call[run_call.index("--pids-limit") + 1] == "32768"
    added = {run_call[i + 1] for i, arg in enumerate(run_call) if arg == "--cap-add"}
    assert added == {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"}
    for flag in ("--init", "--cap-drop", "--security-opt", "--pids-limit", "--cap-add"):
        assert flag in run_call
        assert all(i < image_at for i, arg in enumerate(run_call) if arg == flag)
    assert "--privileged" not in run_call
    assert [run_call[i + 1] for i, arg in enumerate(run_call) if arg == "--security-opt"] == ["no-new-privileges"]


def test_solve_oracle_copies_the_whole_solution_directory(tmp_path, monkeypatch):
    # Regression test: a real run against uxlfoundation/skills'
    # onetbb-histogram-local-aggregation failed (reward 0) because solve.sh
    # did `cp /solution/histogram.cpp ...` -- a sibling file that only
    # exists if the whole solution/ dir is copied in, not just solve.sh.
    task = load_task(_fixture_task(tmp_path))
    cli = DockerCLI()
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)._solve_oracle(
        task, "container-3", io.StringIO(), timeout=30
    )
    calls = cli.args

    cp_call = next(c for c in calls if c[0] == "cp")
    assert cp_call[1] == f"{task.path / 'solution'}/."
    assert cp_call[2] == "container-3:/solution"

    exec_call = [c for c in calls if c[0] == "exec"][-1]
    assert exec_call[-1] == "chmod +x /solution/solve.sh && /solution/solve.sh"
    # The oracle gets the agent's budget; before, solve.sh had none at all.
    assert cli.calls[-1].timeout == 30


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


@pytest.mark.outcome("cjm-skill-discover")
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


@pytest.mark.outcome("cjm-skill-discover")
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


@pytest.mark.parametrize("skill, disc", [("s/SKILL.md", "s"), ("s", "./s/"),
                                         ("s", "{cwd}/s"), ("s/", "s/SKILL.md")])
@pytest.mark.outcome("cjm-skill-discover")
def test_discovery_gap_pairs_spellings_of_one_skill(tmp_path, monkeypatch, skill, disc):
    monkeypatch.chdir(tmp_path)
    skill = f"docker-claude-skill:{skill}"
    disc = f"docker-claude-skill-discover:{disc.format(cwd=tmp_path)}"
    rows = [_arm_row("a", "docker-claude", False), _arm_row("a", skill, True, invoked=1),
            _arm_row("a", disc, False, invoked=0),
            _arm_row("a", "docker-claude-host-skill:s", True, invoked=1),
            _arm_row("a", "docker-claude-skill:t", True, invoked=1)]
    gap = summarize_task_results(rows, baseline_harness="docker-claude")["discovery_gaps"][disc]
    assert (gap["with_skill_harness"], gap["gap"]) == (skill, 1.0)
    # Another launch or another skill is not its pair, and an empty path pairs with nothing.
    assert "discovery_gaps" not in summarize_task_results(rows[:1] + rows[2:])
    empty = [_arm_row("a", "docker-claude", False), _arm_row("a", "docker-claude-skill-discover:", False)]
    assert "discovery_gaps" not in summarize_task_results(empty)


def test_docker_claude_skill_requires_a_path():
    import pytest

    with pytest.raises(ValueError):
        create_harness("docker-claude-skill:")


# ── run contract (Docker steps patched out) ──────────────────────────────────


def _pipeline(*, reward: float | None, usage_json: str | None = None, answer=None) -> DockerCLI:
    """A daemon whose verifier leaves ``reward`` and whose ``claude`` prints ``usage_json``."""
    return DockerCLI(reward=None if reward is None else str(reward), solver=usage_json or "",
                     answer=answer)


def test_docker_claude_run_folds_cost_into_metrics(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, usage_json=_CLAUDE_JSON)
    harness = create_harness("docker-claude", runner=cli)
    result = harness.run(task, model="haiku", output_dir=tmp_path / "out")

    assert result.passed is True
    assert result.reward == 1.0
    assert result.metrics["cost_usd"] == 0.0731
    assert result.metrics["total_tokens"] == 6598
    assert result.metrics["mode"] == "claude"


def test_docker_oracle_run_has_no_llm_cost(tmp_path, monkeypatch):
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=0.0, usage_json=None)
    result = create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
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
    cli = _pipeline(reward=1.0, usage_json=_CLAUDE_JSON)
    suite = TaskSuiteRunner(
        tasks=[task],
        harnesses=["docker-claude"],
        model="haiku",
        output_root=tmp_path / "runs",
        repeats=3,
        runner=cli,
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

    def bad_build(call):
        if call.argv[1] == "build" and "dokimi/intel-perf-bad:latest" in call.argv:
            return reply(stderr="boom", returncode=1)
        return None

    suite = TaskSuiteRunner(
        tasks=[load_task(bad), load_task(good)],
        harnesses=["docker-claude"],
        model="haiku",
        output_root=tmp_path / "runs",
        runner=_pipeline(reward=1.0, usage_json=_CLAUDE_JSON, answer=bad_build),
    )
    output = suite.run()

    validate_artifact("task_runs", output)
    assert len(output["results"]) == 2  # both cells present, sweep survived
    rows = {r["task"]: r for r in output["results"]}
    assert rows["intel-perf-bad"]["passed"] is False
    assert rows["intel-perf-bad"]["reward"] is None  # no verdict, not a verified 0
    assert "docker build failed" in rows["intel-perf-bad"]["metrics"]["error"]
    assert rows["intel-perf-toy"]["passed"] is True


def test_recompile_failure_recorded_in_metrics(tmp_path):
    """A failed in-container recompile is surfaced in metrics so the cell can be
    told apart from a genuine model miss (it is a tooling failure)."""
    task = load_task(_fixture_task(tmp_path))  # instruction has a `g++ ... -o /app/bench`

    def no_compiler(call):
        return reply(stderr="g++: not found", returncode=127) if "g++" in call.argv[-1] else None

    cli = _pipeline(reward=0.0, usage_json=_CLAUDE_JSON, answer=no_compiler)
    result = create_harness("docker-claude", runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")
    assert result.metrics["recompile_failed"] == ["g++ -O3 bench.cpp -o /app/bench"]
    assert result.passed is False


def test_api_error_is_recorded_as_a_solver_error(tmp_path):
    """A refused or flagged request scores 0 like a miss; metrics must say it was not one."""
    flagged = json.dumps({"type": "result", "is_error": True, "terminal_reason": "api_error",
                          "num_turns": 2, "total_cost_usd": 0.07,
                          "result": "API Error: request flagged\nDetails: x"})
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=0.0, usage_json=flagged)
    result = create_harness("docker-claude-host", runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")
    assert result.metrics["solver_error"] == "api_error: API Error: request flagged"
    assert result.passed is False
    (solve,) = cli.solver_calls()
    assert solve.argv[0] == "claude" and solve.timeout == 30  # the task's agent.timeout_sec


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
    cli = _pipeline(reward=1.0, usage_json=json.dumps(obj))
    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")
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


def test_build_timeout_raises_so_the_cell_is_errored(tmp_path):
    """A build that exceeds build_timeout_sec never ran the agent, so it must
    be errored like a build that fails fast — not a reward-0 failure."""
    task = load_task(_fixture_task(tmp_path))
    budget = _task_build_timeout(task)
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if call.argv[1] == "build" else None)
    with pytest.raises(RuntimeError, match=f"docker build timed out after {budget}"):
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    assert "BUILD TIMEOUT" in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert cli.calls[0].timeout == budget

    output = TaskSuiteRunner(
        tasks=[task], harnesses=["docker-oracle"], output_root=tmp_path / "runs", runner=cli,
    ).run()
    row = output["results"][0]
    assert row["reward"] is None
    assert "docker build timed out" in row["metrics"]["error"]
    assert output["summary"]["per_harness"]["docker-oracle"]["errored"] == 1


@pytest.mark.parametrize("script", ["solve.sh", "test.sh"])
def test_solve_or_verify_timeout_is_a_failed_cell(tmp_path, script):
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply("partial", timed_out=True) if (
        call.argv[1] == "exec" and script in call.argv[-1]) else None)
    result = create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    assert result.returncode == 124
    assert result.reward == 0.0
    assert result.passed is False
    assert "error" not in result.metrics
    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert "partial" in log and "TIMEOUT: after" in log  # what the step printed before the kill is kept


def test_a_docker_step_without_a_budget_that_hangs_errors_the_cell(tmp_path):
    """`docker cp` & co. get 600 s; one that runs out is a tooling failure, not a 0."""
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "cp" and call.argv[-1].endswith(":/solution")) else None)
    with pytest.raises(RuntimeError, match=r"docker cp failed \(timed out after 600s\)"):
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    hung = next(c for c in cli.calls if c.argv[1] == "cp")
    assert hung.timeout == 600 and hung.env == dict(os.environ)  # the CLI keeps the caller's env


@pytest.mark.parametrize("harness", ["docker-oracle", "docker-claude", "docker-claude-host"])
def test_every_step_runs_with_a_budget(tmp_path, harness):
    """build: build_timeout_sec; solve.sh and claude: the agent's; test.sh: the verifier's;
    every other step 600 s. Before, only build, claude and test.sh had one."""
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, usage_json=_CLAUDE_JSON)
    create_harness(harness, runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")

    def expected(argv: tuple[str, ...]) -> float:
        if cli.is_solver(argv):
            return task.timeout_sec
        if argv[1] == "build":
            return _task_build_timeout(task)
        if argv[1] == "exec" and "solve.sh" in argv[-1]:
            return task.timeout_sec
        if argv[1] == "exec" and "test.sh" in argv[-1]:
            return _task_verify_timeout(task)
        return 600

    assert task.timeout_sec == 30
    assert [(c.argv[:2], c.timeout) for c in cli.calls] == [(c.argv[:2], expected(c.argv)) for c in cli.calls]
    steps = {c.argv[-1] for c in cli.calls if c.argv[1] == "exec"}
    assert any("test.sh" in s for s in steps) and (harness != "docker-oracle" or any("solve.sh" in s for s in steps))
    assert any(cli.is_solver(c.argv) for c in cli.calls) == (harness != "docker-oracle")


@pytest.mark.parametrize("harness", ["docker-claude", "docker-claude-host"])
def test_a_stream_over_the_capture_limit_is_kept_whole(tmp_path, harness):
    """The runner keeps only the head and tail of a captured stream by default; the
    solver's stream and a docker step's output are kept whole (and in memory: neither
    is on disk before it is redacted)."""
    task = load_task(_fixture_task(tmp_path))
    chunk = "x" * (process.CAPTURE_LIMIT // 2)
    tool = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": chunk}}]}})
    stream = "\n".join([tool, tool, tool, _CLAUDE_JSON]) + "\n"
    verifier_log = chunk + "MIDDLE-OF-THE-TEST-LOG" + chunk
    cli = _pipeline(reward=1.0, usage_json=stream, answer=lambda call: reply(verifier_log) if (
        call.argv[1] == "exec" and "test.sh" in call.argv[-1]) else None)
    result = create_harness(harness, runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")
    assert result.metrics["tool_calls"] == {"Bash": 3}
    assert (tmp_path / "out" / "solver.jsonl").read_text(encoding="utf-8") == stream
    assert "MIDDLE-OF-THE-TEST-LOG" in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert all(c.capture_limit is None and c.stdout_to is None and c.stderr_to is None for c in cli.calls)

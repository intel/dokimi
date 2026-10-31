# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Harbor-backed cells are graded from Harbor's own result.json files.

The fixtures under ``tests/fixtures/harbor/`` are trimmed copies of what
``harbor run --jobs-dir <dir>`` (Harbor 0.24.0, agent ``claude-code``) wrote:
``<dir>/<job>/result.json`` and ``<dir>/<job>/<trial>/result.json``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.harnesses import TaskSuiteRunner, discover_tasks, load_task
from dokimi.harnesses.command import harbor_usage, read_harbor_job
from dokimi.harnesses.registry import create_harness
from dokimi.process import FakeRunner, reply
from tests.test_task_harnesses import _fixture_task

FIXTURES = Path(__file__).parent / "fixtures" / "harbor"
JOB = "2026-10-06__14-39-30"

# Harbor 0.24's end-of-run tables on stdout (a 6-trial job's); the stdout
# scrape `command:` keeps reads "Reward ... 1.0" off the second one.
HARBOR_SUMMARY = (
    "tasks • claude-code • m\n"
    "┃ Trials ┃ Exceptions ┃  Mean ┃ Pass@2 ┃\n"
    "│      6 │          0 │ 0.333 │  0.667 │\n"
    "┃ Reward ┃ Count ┃\n"
    "┡━━━━━━━━╇━━━━━━━┩\n"
    "│ 1.0    │     2 │\n"
    "│ 0.0    │     4 │\n"
)


def _harbor_writes(case: str | None, *, edit=None, stdout: str = HARBOR_SUMMARY, returncode: int = 0):
    """A fake ``harbor run`` that leaves fixture *case*'s job under ``--jobs-dir``
    (nothing for None) and exits *returncode*, 0 by default as Harbor does."""

    def handler(call):
        jobs_dir = Path(call.argv[call.argv.index("--jobs-dir") + 1])
        if case is not None:
            shutil.copytree(FIXTURES / case, jobs_dir, dirs_exist_ok=True)
            if edit:
                for trial in jobs_dir.glob("*/*/result.json"):
                    data = json.loads(trial.read_text(encoding="utf-8"))
                    edit(data)
                    trial.write_text(json.dumps(data), encoding="utf-8")
        return reply(stdout, returncode=returncode)

    return FakeRunner(handler)


def _run(tmp_path, runner, harness_id="claude-code"):
    task = load_task(_fixture_task(tmp_path / "tasks"))
    return create_harness(harness_id, runner=runner).run(task, model="m", output_dir=tmp_path / "cell")


def test_a_passing_trial_is_a_pass_with_its_tokens_and_cost(tmp_path):
    result = _run(tmp_path, _harbor_writes("pass"))

    assert (result.passed, result.reward) == (True, 1.0)
    assert "error" not in result.metrics
    m = result.metrics
    assert (m["prompt_tokens"], m["completion_tokens"], m["cache_read_tokens"]) == (234573, 2335, 231270)
    assert m["total_tokens"] == 234573 + 2335
    assert m["cost_usd"] == pytest.approx(0.038918)
    assert m["n_trials"] == 1
    assert m["result_json"] == str(tmp_path / "cell" / JOB / "onetbb-transpose__4LraMVa" / "result.json")


def test_a_relative_output_dir_is_recorded_as_the_caller_gave_it(tmp_path, monkeypatch):
    """`tasks run` passes a relative dir. The harness runs the command on an absolute
    copy of it, so result_json must be mapped back to the caller's relative path."""
    monkeypatch.chdir(tmp_path)
    task = load_task(_fixture_task(tmp_path / "tasks"))
    result = create_harness("claude-code", runner=_harbor_writes("pass")).run(
        task, model="m", output_dir=Path("cell"))

    assert (result.passed, result.reward) == (True, 1.0)
    assert result.metrics["result_json"] == str(Path("cell") / JOB / "onetbb-transpose__4LraMVa" / "result.json")


def test_a_failing_trial_is_a_scored_fail(tmp_path):
    result = _run(tmp_path, _harbor_writes("fail"))

    assert (result.passed, result.reward) == (False, 0.0)
    assert "error" not in result.metrics
    assert result.metrics["cost_usd"] == pytest.approx(0.03092655)


@pytest.mark.parametrize("harness_id", ["codex", "claude-code", "terminal-bench:aider"])
def test_a_trial_that_raised_is_errored_though_harbor_exits_0(tmp_path, harness_id):
    """Measured with Harbor 0.24.0: the agent install failed, Harbor exited 0
    and printed its summary table, and the cell read as PASS 1/1."""
    result = _run(tmp_path, _harbor_writes("exception"), harness_id)

    assert result.returncode == 0
    assert (result.passed, result.reward) == (False, None)
    assert result.metrics["harbor_exception"] == "NonZeroAgentExitCodeError"
    assert result.metrics["error"] == (
        "harbor: NonZeroAgentExitCodeError: Command failed (exit 100): "
        "apt-get update && apt-get install -y curl nodejs npm"
    )
    # agent_result is null for a trial that never ran the agent: no usage, not zeros.
    assert "cost_usd" not in result.metrics and "prompt_tokens" not in result.metrics


def test_a_reward_beside_an_exception_is_kept_aside_not_scored(tmp_path):
    def timed_out_then_verified(trial):
        trial["exception_info"] = {"exception_type": "AgentTimeoutError", "exception_message": ""}

    result = _run(tmp_path, _harbor_writes("pass", edit=timed_out_then_verified))

    assert (result.passed, result.reward) == (False, None)
    assert result.metrics["error"] == "harbor: AgentTimeoutError"
    assert result.metrics["reward_after_exception"] == 1.0


def test_a_step_that_raised_errors_a_multi_step_trial(tmp_path):
    """Measured with Harbor 0.20.0 (``multistep`` fixture, agent ``oracle``): step-2's
    setup.sh failed, the exception is on that step only, and the top-level reward is
    step-1's 1.0 because Harbor's mean leaves a step without a verifier result out."""
    result = _run(tmp_path, _harbor_writes("multistep"))

    assert (result.passed, result.reward) == (False, None)
    assert result.metrics["error"] == "harbor: RuntimeError: Step 'step-2' setup.sh exited with code 3: None"
    assert result.metrics["reward_after_exception"] == 1.0
    assert "prompt_tokens" not in result.metrics  # the oracle's counts are null


def test_a_verifier_timeout_is_a_zero_not_an_error(tmp_path):
    """Harbor 0.20.0 raises VerifierTimeoutError (no verifier_result) when the verifier
    overruns its timeout. As for docker-* and BenchFlow it is a failure, not missing data."""
    def verifier_timed_out(trial):
        trial["verifier_result"] = None
        trial["exception_info"] = {"exception_type": "VerifierTimeoutError",
                                   "exception_message": "Verifier execution timed out after 5.0 seconds"}

    result = _run(tmp_path, _harbor_writes("pass", edit=verifier_timed_out))

    assert (result.passed, result.reward) == (False, 0.0)
    assert result.metrics["verifier_timeout"] is True
    assert "error" not in result.metrics


def test_a_multi_step_trial_sums_its_steps_usage(tmp_path):
    def two_clean_steps(trial):
        first, second = trial["step_results"]
        first["agent_result"] = {"n_input_tokens": 1000, "n_cache_tokens": 400, "n_output_tokens": 10,
                                 "cost_usd": 0.5}
        second.update(exception_info=None, verifier_result={"rewards": {"reward": 1.0}},
                      agent_result={"n_input_tokens": 500, "n_cache_tokens": None, "n_output_tokens": 5,
                                    "cost_usd": 0.25})

    result = _run(tmp_path, _harbor_writes("multistep", edit=two_clean_steps))

    assert (result.passed, result.reward) == (True, 1.0)
    m = result.metrics
    assert (m["prompt_tokens"], m["completion_tokens"], m["total_tokens"]) == (1500, 15, 1515)
    assert m["cost_usd"] == pytest.approx(0.75)
    assert "cache_read_tokens" not in m  # unknown for step-2, so not a partial sum


@pytest.mark.parametrize("exception_info, error", [
    ("boom", "harbor: unknown"),  # not a dict: no type to report
    ({"exception_type": None, "exception_message": "x" * 300 + "\nsecond line"},
     "harbor: unknown: " + "x" * 200),  # message is cut to its first line and 200 characters
])
def test_an_exception_with_a_missing_type_or_message_is_still_errored(tmp_path, exception_info, error):
    def add_exception(trial):
        trial["exception_info"] = exception_info

    result = _run(tmp_path, _harbor_writes("pass", edit=add_exception))

    assert (result.passed, result.reward) == (False, None)
    assert result.metrics["error"] == error


@pytest.mark.parametrize("verifier_result", [None, {}, {"rewards": {}}, {"rewards": {"reward": True}},
                                             {"rewards": {"reward": "1.0"}}])
def test_a_trial_without_a_numeric_reward_is_errored(tmp_path, verifier_result):
    def drop_reward(trial):
        trial["verifier_result"] = verifier_result

    result = _run(tmp_path, _harbor_writes("pass", edit=drop_reward))

    assert (result.passed, result.reward) == (False, None)
    assert result.metrics["error"] == "harbor: trial result.json has no verifier_result.rewards.reward"


@pytest.mark.parametrize("returncode", [0, 1])
def test_no_result_json_is_errored_never_the_exit_code(tmp_path, returncode):
    # Harbor's own summary on stdout, and a stray reward file: neither is a verdict.
    def handler(call):
        jobs_dir = Path(call.argv[call.argv.index("--jobs-dir") + 1])
        (jobs_dir / "reward.txt").write_text("1.0", encoding="utf-8")
        return reply("reward: 1.0\n" + HARBOR_SUMMARY, returncode=returncode)

    result = _run(tmp_path, FakeRunner(handler))

    assert (result.passed, result.reward) == (False, None)
    assert result.metrics["error"] == f"harbor: no Harbor result.json (exit {returncode})"
    assert result.metrics["n_trials"] == 0


def test_a_job_summary_without_its_trial_file_is_errored(tmp_path):
    def handler(call):
        jobs_dir = Path(call.argv[call.argv.index("--jobs-dir") + 1])
        (jobs_dir / JOB).mkdir()
        shutil.copy(FIXTURES / "pass" / JOB / "result.json", jobs_dir / JOB / "result.json")
        return reply(HARBOR_SUMMARY)

    result = _run(tmp_path, FakeRunner(handler))

    assert result.reward is None
    assert result.metrics["error"] == "harbor: a job result.json but no trial result.json (exit 0)"


def test_more_than_one_trial_in_a_cell_is_errored(tmp_path):
    def handler(call):
        jobs_dir = Path(call.argv[call.argv.index("--jobs-dir") + 1])
        shutil.copytree(FIXTURES / "pass", jobs_dir, dirs_exist_ok=True)
        shutil.copytree(FIXTURES / "fail", jobs_dir, dirs_exist_ok=True)
        return reply(HARBOR_SUMMARY)

    result = _run(tmp_path, FakeRunner(handler))

    assert result.reward is None
    assert result.metrics["error"] == "harbor: 2 trials in one cell (expected 1)"
    assert result.metrics["n_trials"] == 2


@pytest.mark.parametrize("content, error", [
    ("{not json", "harbor: trial result.json is unreadable or not valid JSON"),
    ('{"n": ' + "9" * 5000 + "}", "harbor: trial result.json is unreadable or not valid JSON"),
    ("[1, 2]", "harbor: trial result.json is not a JSON object"),
])
def test_an_unreadable_trial_file_is_errored(tmp_path, content, error):
    trial = tmp_path / "jobs" / JOB / "t" / "result.json"
    trial.parent.mkdir(parents=True)
    trial.write_text(content, encoding="utf-8")

    reward, metrics = read_harbor_job(tmp_path / "jobs", 0)

    assert reward is None
    assert metrics["error"] == error


def test_without_an_output_dir_there_is_nothing_to_grade(tmp_path):
    reward, metrics = read_harbor_job(None, 0)
    assert reward is None and "no output dir" in metrics["error"]


def test_harbor_usage_maps_to_the_shared_metric_keys():
    assert harbor_usage({"n_input_tokens": 10, "n_cache_tokens": 4, "n_output_tokens": 3,
                         "cost_usd": 0.5}) == {
        "prompt_tokens": 10, "completion_tokens": 3, "cache_read_tokens": 4,
        "total_tokens": 13, "cost_usd": 0.5,
    }
    # Unknown stays unknown: a null count (Harbor's oracle agent) is not a 0.
    assert harbor_usage({"n_input_tokens": None, "n_cache_tokens": None, "n_output_tokens": None,
                         "cost_usd": None}) == {}
    assert harbor_usage({"n_input_tokens": 10, "n_output_tokens": True}) == {"prompt_tokens": 10}
    assert harbor_usage(None) == {}


def test_harbor_usage_never_truncates_a_fraction_or_overflows():
    # A fractional token count is dropped, not truncated to 0 and summed.
    assert harbor_usage({"n_input_tokens": 0.5, "n_output_tokens": 3.0}) == {"completion_tokens": 3}
    # A huge integer count is kept exactly; a cost beyond float range is unknown, not an OverflowError.
    usage = harbor_usage({"n_input_tokens": 10**400, "n_output_tokens": 3, "cost_usd": 10**309})
    assert usage["prompt_tokens"] == 10**400
    assert usage["completion_tokens"] == 3
    assert "cost_usd" not in usage


def test_an_errored_harbor_cell_is_out_of_the_pass_rate_and_cost_counts(tmp_path):
    """Through the runner: the trial that raised is errored, not a pass, and the
    passing trial's cost reaches per_harness.cost."""
    root = tmp_path / "tasks"
    _fixture_task(root).rename(root / "a-task")
    _fixture_task(root).rename(root / "b-task")

    def handler(call):
        jobs_dir = Path(call.argv[call.argv.index("--jobs-dir") + 1])
        case = "exception" if "a-task" in call.argv[call.argv.index("-p") + 1] else "pass"
        shutil.copytree(FIXTURES / case, jobs_dir, dirs_exist_ok=True)
        return reply(HARBOR_SUMMARY)

    output = TaskSuiteRunner(tasks=discover_tasks(root), harnesses=["claude-code"], model="m",
                             output_root=tmp_path / "runs", runner=FakeRunner(handler)).run()

    validate_artifact("task_runs", output)
    stats = output["summary"]["per_harness"]["claude-code"]
    assert (stats["errored"], stats["scored"], stats["passed"]) == (1, 1, 1)
    assert stats["pass_rate"] == 1.0
    assert stats["cost"]["total_cost_usd"] == pytest.approx(0.038918)


def test_a_command_template_keeps_its_exit_code_fallback(tmp_path):
    """Only kind="harbor" reads result.json: a custom template that prints no
    reward and exits 0 still passes, as before."""
    task = load_task(_fixture_task(tmp_path / "tasks"))
    result = create_harness("command:my-wrapper {task_path}", runner=FakeRunner()).run(
        task, output_dir=tmp_path / "cell")

    assert (result.passed, result.reward) == (True, 1.0)
    assert "error" not in result.metrics


@pytest.mark.parametrize("returncode, expected", [(0, (True, 1.0)), (3, (False, 0.0))])
def test_a_command_template_scores_its_exit_code_when_it_prints_no_reward(tmp_path, returncode, expected):
    task = load_task(_fixture_task(tmp_path / "tasks"))
    result = create_harness("command:my-wrapper {task_path}", runner=FakeRunner(lambda call: reply("", "", returncode))).run(
        task, output_dir=tmp_path / "cell")

    assert (result.passed, result.reward) == expected


@pytest.mark.parametrize("returncode", [126, 127, 137, -9])
def test_a_command_that_never_ran_or_was_killed_has_no_verdict(tmp_path, returncode):
    task = load_task(_fixture_task(tmp_path / "tasks"))
    result = create_harness("command:my-wrapper {task_path}", runner=FakeRunner(lambda call: reply("", "", returncode))).run(
        task, output_dir=tmp_path / "cell")

    assert (result.passed, result.reward) == (False, None)
    _assert_errored_not_scored(result)


def _assert_errored_not_scored(result):
    from dokimi.harnesses.runner import summarize_task_results

    stats = summarize_task_results([result.as_dict()])["per_harness"][result.harness]
    assert (stats["scored"], stats["errored"], stats["pass_rate"]) == (0, 1, None)


def test_a_command_with_an_unreadable_reward_file_is_not_scored_from_its_exit_code(tmp_path):
    task = load_task(_fixture_task(tmp_path / "tasks"))

    def handler(call):
        (Path(call.env["DOKIMI_OUTPUT_DIR"]) / "reward.txt").write_text("2.0", encoding="utf-8")
        return reply("")

    result = create_harness("command:my-wrapper {task_path}", runner=FakeRunner(handler)).run(
        task, output_dir=tmp_path / "cell")

    assert (result.passed, result.reward) == (False, None)
    assert "reward file unreadable" in result.metrics["solver_error"]
    _assert_errored_not_scored(result)


@pytest.mark.parametrize("reward_val, expect_pass, expect_reward", [
    (float("inf"), False, None),  # Non-finite: error
    (float("nan"), False, None),  # Non-finite: error
    (10**400, False, None),       # Integer beyond float range: error, not an OverflowError
    (2.0, False, None),           # Out of [0,1]: error
    (-1.0, False, None),          # Negative: error
    (0.5, False, 0.5),            # Valid fractional reward: kept, not a pass
    (0.0, False, 0.0),            # Valid: reward 0 is allowed
    (1.0, True, 1.0),             # Valid: reward 1 is a pass
])
def test_harbor_reward_validation(tmp_path, reward_val, expect_pass, expect_reward):
    """Non-finite and out-of-range rewards are errors; a reward in [0, 1] is kept, and 1 passes."""
    result = _run(tmp_path, _harbor_writes("pass", edit=lambda d: d["verifier_result"]["rewards"].update(reward=reward_val)))
    assert result.passed == expect_pass
    assert result.reward == expect_reward
    if expect_reward is None:
        assert result.reward is None and "error" in result.metrics


def test_harbor_token_count_validation():
    """Non-finite and negative token counts are rejected; null counts are skipped (unknown)."""
    from dokimi.harnesses.command import _number

    # Non-finite values should be rejected
    assert _number(float("nan")) is None
    assert _number(float("inf")) is None
    assert _number(float("-inf")) is None

    # Negative values should be rejected (corrupt sums)
    assert _number(-5) is None
    assert _number(-0.1) is None

    # Null and non-numeric should be skipped
    assert _number(None) is None
    assert _number("not a number") is None
    assert _number(True) is None  # bool is int subclass

    # Valid values should pass through
    assert _number(0) == 0
    assert _number(100) == 100
    assert _number(10**400) == 10**400  # an int is always finite, however large
    assert _number(0.5) == 0.5
    assert _number(1e6) == 1e6


def test_result_json_path_is_properly_recorded(tmp_path):
    """Regression: metrics['result_json'] correctly stores the trial result.json location."""
    result = _run(tmp_path, _harbor_writes("pass"))
    # The path should be recorded and point to the trial's result.json
    assert "result_json" in result.metrics
    result_path = result.metrics["result_json"]
    assert "result.json" in result_path
    assert Path(result_path).exists()  # verify the path is valid


def _read_trial(tmp_path, trial):
    """Grade one trial written as *trial* (a dict, or raw text) under a jobs dir."""
    jobs = tmp_path / "jobs"
    path = jobs / JOB / "t" / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(trial if isinstance(trial, str) else json.dumps(trial), encoding="utf-8")
    return read_harbor_job(jobs, 0)


VT = {"exception_type": "VerifierTimeoutError", "exception_message": "Verifier timed out"}
AT = {"exception_type": "AgentTimeoutError", "exception_message": "Agent timed out"}
PASS_REWARDS = {"reward": 1.0}


@pytest.mark.parametrize("trial", [
    {"exception_info": VT, "step_results": [{"exception_info": AT}]},
    {"step_results": [{"exception_info": VT}, {"exception_info": AT}]},
    {"step_results": [{"exception_info": AT}, {"exception_info": VT}]},
])
def test_a_verifier_timeout_does_not_hide_a_real_exception(tmp_path, trial):
    """The label is the first exception that is not a verifier timeout."""
    reward, metrics = _read_trial(tmp_path, trial)

    assert reward is None
    assert metrics["harbor_exception"] == "AgentTimeoutError"
    assert metrics["error"] == "harbor: AgentTimeoutError: Agent timed out"
    assert "verifier_timeout" not in metrics


def test_a_trial_exception_is_labelled_before_a_step_exception(tmp_path):
    trial = {"exception_info": {"exception_type": "RuntimeError", "exception_message": "boom"},
             "step_results": [{"exception_info": AT}]}
    reward, metrics = _read_trial(tmp_path, trial)

    assert reward is None
    assert metrics["harbor_exception"] == "RuntimeError"


def test_a_verifier_timeout_on_a_step_alone_is_a_zero(tmp_path):
    reward, metrics = _read_trial(tmp_path, {"step_results": [{"exception_info": VT}]})

    assert reward == 0.0
    assert metrics["verifier_timeout"] is True
    assert "error" not in metrics


def test_a_multi_step_trial_with_no_agent_result_has_no_usage_and_no_crash(tmp_path):
    trial = {"verifier_result": {"rewards": PASS_REWARDS}, "step_results": [{"exception_info": None}]}
    reward, metrics = _read_trial(tmp_path, trial)

    assert reward == 1.0
    assert "error" not in metrics
    assert not {"prompt_tokens", "cost_usd", "total_tokens"} & metrics.keys()


def test_a_non_object_step_is_skipped_not_a_crash(tmp_path):
    step = {"exception_info": None, "agent_result": {"n_input_tokens": 100, "n_output_tokens": 1}}
    trial = {"verifier_result": {"rewards": PASS_REWARDS}, "step_results": ["oops", step]}
    reward, metrics = _read_trial(tmp_path, trial)

    assert reward == 1.0
    assert metrics["prompt_tokens"] == 100


def test_a_top_level_agent_result_beside_steps_wins_over_the_step_sum(tmp_path):
    step = {"exception_info": None, "agent_result": {"n_input_tokens": 1000, "n_output_tokens": 500}}
    trial = {"verifier_result": {"rewards": PASS_REWARDS}, "step_results": [step],
             "agent_result": {"n_input_tokens": 7, "n_output_tokens": 3}}
    reward, metrics = _read_trial(tmp_path, trial)

    assert reward == 1.0
    assert (metrics["prompt_tokens"], metrics["completion_tokens"], metrics["total_tokens"]) == (7, 3, 10)


def test_a_trial_path_that_is_a_directory_is_unreadable(tmp_path):
    jobs = tmp_path / "jobs"
    (jobs / JOB / "t" / "result.json").mkdir(parents=True)

    reward, metrics = read_harbor_job(jobs, 0)

    assert reward is None
    assert metrics["error"] == "harbor: trial result.json is unreadable or not valid JSON"


@pytest.mark.parametrize("info, error", [
    ({"exception_type": "X", "exception_message": 12345}, "harbor: X: 12345"),
    ({"exception_type": 42, "exception_message": "boom"}, "harbor: 42: boom"),
])
def test_a_non_string_exception_field_is_stringified(tmp_path, info, error):
    reward, metrics = _read_trial(tmp_path, {"exception_info": info})

    assert reward is None
    assert metrics["error"] == error


def test_a_non_string_exception_type_is_kept_as_text_in_the_label(tmp_path):
    reward, metrics = _read_trial(tmp_path, {"exception_info": {"exception_type": 42, "exception_message": "boom"}})

    assert metrics["harbor_exception"] == "42"


def test_a_step_without_an_agent_result_does_not_erase_the_other_steps_usage(tmp_path):
    steps = [{"exception_info": None, "agent_result": {"n_input_tokens": 100, "n_output_tokens": 1}},
             {"exception_info": None, "agent_result": None}]
    reward, metrics = _read_trial(tmp_path, {"verifier_result": {"rewards": PASS_REWARDS}, "step_results": steps})

    assert reward == 1.0
    assert metrics["prompt_tokens"] == 100


def test_an_exception_with_no_reward_keeps_no_reward_after_exception(tmp_path):
    reward, metrics = _read_trial(tmp_path, {"exception_info": AT})

    assert reward is None
    assert "reward_after_exception" not in metrics

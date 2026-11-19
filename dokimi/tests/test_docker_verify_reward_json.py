# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A docker verifier's ``reward.json`` is its reward, read as Harbor reads it.

docs/coding-harnesses.md, "Output Artifact": a reward-file verifier's ``reward.json`` comes before its
``reward.txt``; its reward is the ``reward`` key, or the only key; a file with no such reward errors the cell.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from docker_cli_fake import DockerCLI, reply
from dokimi.harnesses import load_task
from dokimi.harnesses.command import _find_reward
from dokimi.harnesses.docker_solver import DockerSolverHarness

# What uxl-skills' evaluation/harbor/shared/structured_answer.py writes: the reward and its parts.
_ANSWER_QUALITY = json.dumps({"reward": 0.5833, "graph_design": 0.6667, "bounded_concurrency": 0.5,
                              "unsupported_claim_avoidance": 1.0, "answer_present": 1.0}, indent=2)


def _harbor(root: Path) -> Path:
    task = root / "hb-task"
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True)
    (task / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "instruction.md").write_text("Explain it in /app/answer.md.", encoding="utf-8")
    (task / "task.toml").write_text('[environment]\nworkdir = "/app"\n', encoding="utf-8")
    return task


def _verify(tmp_path: Path, cli: DockerCLI) -> float | None:
    out = tmp_path / "o"
    out.mkdir(exist_ok=True)
    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)
    return harness._verify(load_task(_harbor(tmp_path)), "c1", out, log=io.StringIO())


def test_a_reward_json_alone_is_the_reward_not_an_exit_code_verdict(tmp_path):
    # On main the verifier's exit 0 without reward.txt errored the cell (#695), the score unread.
    assert _verify(tmp_path, DockerCLI(reward=None, reward_json=_ANSWER_QUALITY)) == 0.5833
    assert json.loads((tmp_path / "o" / "reward.json").read_text(encoding="utf-8"))["graph_design"] == 0.6667


def test_a_reward_json_counts_whatever_the_verifier_exit_code(tmp_path):
    def failing(call):
        return reply(returncode=1) if "-lc" in call.argv else None

    cli = DockerCLI(reward=None, reward_json='{"reward": 0.25}', answer=failing)
    assert _verify(tmp_path, cli) == 0.25


def test_reward_json_comes_before_reward_txt_as_in_harbor(tmp_path):
    cli = DockerCLI(reward="1.0", reward_json='{"reward": 0.4}')
    assert _verify(tmp_path, cli) == 0.4
    assert [a[1] for a in cli.args if a[0] == "cp" and "/logs/verifier/" in a[1]] == [
        "c1:/logs/verifier/reward.json"]


def test_a_reward_json_with_one_key_of_another_name_is_that_reward(tmp_path):
    assert _verify(tmp_path, DockerCLI(reward=None, reward_json='{"accuracy": 1}')) == 1.0


def test_reward_txt_alone_is_read_as_before(tmp_path):
    assert _verify(tmp_path, DockerCLI(reward="0.75\n")) == 0.75


@pytest.mark.parametrize(("text", "why"), [
    ("", "is not JSON"),
    ("reward: 0.5", "is not JSON"),
    ("0.5", "is not a JSON object"),
    ("[0.5]", "is not a JSON object"),
    ("{}", "has no 'reward' key among 0"),
    ('{"accuracy": 0.5, "recall": 0.7}', "has no 'reward' key among 2"),
    ('{"reward": "0.5"}', "'reward' that is not a finite number"),
    ('{"reward": NaN}', "'reward' that is not a finite number"),
    ('{"reward": 0.5, "parts": [1, 0]}', "'parts' that is not a finite number"),
    ('{"reward": 1.5}', "not between 0 and 1: 1.5"),
    ('{"reward": -0.1}', "not between 0 and 1: -0.1"),
    ('{"reward": true}', "not between 0 and 1: True"),
])
def test_a_malformed_reward_json_errors_the_cell_even_beside_a_reward_txt(tmp_path, text, why):
    # Harbor reads reward.json when it exists and raises on a bad one, never falling back to reward.txt.
    with pytest.raises(RuntimeError, match=f"/logs/verifier/reward.json .*{why}"):
        _verify(tmp_path, DockerCLI(reward="1.0", reward_json=text))


def test_a_failed_copy_of_reward_json_errors_the_cell_and_reward_txt_does_not_stand_in(tmp_path):
    def daemon_down(call):
        if call.argv[1] == "cp" and call.argv[2].endswith(":/logs/verifier/reward.json"):
            return reply(stderr="Error response from daemon: connection refused", returncode=1)
        return None

    with pytest.raises(RuntimeError, match=r"docker cp of /logs/verifier/reward.json failed \(exit 1\)"):
        _verify(tmp_path, DockerCLI(reward="1.0", answer=daemon_down))


def test_exit_zero_without_either_reward_file_still_errors_the_cell(tmp_path):
    with pytest.raises(RuntimeError, match="wrote no /logs/verifier/reward.txt or reward.json"):
        _verify(tmp_path, DockerCLI(reward=None))


def test_a_reward_json_left_by_an_earlier_run_is_not_this_runs_reward(tmp_path):
    (tmp_path / "o").mkdir()
    (tmp_path / "o" / "reward.json").write_text('{"reward": 1.0}', encoding="utf-8")
    assert _verify(tmp_path, DockerCLI(reward="0.0")) == 0.0
    assert not (tmp_path / "o" / "reward.json").exists()


def test_an_answer_quality_cell_records_its_partial_reward_as_a_fail(tmp_path):
    cli = DockerCLI(reward=None, reward_json=_ANSWER_QUALITY)
    res = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        load_task(_harbor(tmp_path)), output_dir=tmp_path / "out")
    assert (res.reward, res.passed) == (0.5833, False)


@pytest.mark.parametrize(("text", "expected"), [
    # A label earlier in the text than "reward" no longer gives the reward (the old regex read 0.2 here).
    ('{"group_score": 0.2, "reward": 0.9}', 0.9),
    ('{"accuracy": 0.5}', 0.5),
    ('{"score": 0.3, "recall": 0.6}', None),
    ("0.5", None),
])
def test_a_command_harness_reads_reward_json_by_the_same_rule(tmp_path, text, expected):
    (tmp_path / "verifier").mkdir()
    (tmp_path / "verifier" / "reward.json").write_text(text, encoding="utf-8")
    assert _find_reward(tmp_path, "reward: 1", "") == expected

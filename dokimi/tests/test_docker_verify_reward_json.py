# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A docker verifier's ``reward.json`` is its reward, read as Harbor reads it.

docs/coding-harnesses.md, "Output Artifact": a reward-file verifier's ``reward.json`` comes before its
``reward.txt``; its reward is the ``reward`` key, or the only key; a file with no such reward errors the cell.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from docker_cli_fake import DockerCLI, reply
from dokimi.harnesses import load_task
from dokimi.harnesses.command import _find_reward
from dokimi.harnesses.docker_solver import DockerSolverHarness
from dokimi.harnesses.verifier_reward import clear_reward_files

# What uxl-skills' evaluation/harbor/shared/structured_answer.py writes: the reward and its parts.
_ANSWER_QUALITY = json.dumps({"reward": 0.5833, "graph_design": 0.6667, "bounded_concurrency": 0.5,
                              "unsupported_claim_avoidance": 1.0, "answer_present": 1.0}, indent=2)


def _harbor(root: Path) -> Path:
    task = root / "hb-task"
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True, exist_ok=True)
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
    assert [a[-2] for a in cli.args if a[0] == "cp" and "/logs/verifier/" in a[-2]] == [
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


def test_a_deeply_nested_reward_json_errors_the_cell_like_any_malformed_one(tmp_path):
    # json.loads raises RecursionError, not ValueError, past the interpreter's depth limit.
    with pytest.raises(RuntimeError, match="/logs/verifier/reward.json .*is not JSON"):
        _verify(tmp_path, DockerCLI(reward=None, reward_json="[" * 100000 + "]" * 100000))


def test_a_failed_copy_of_reward_json_errors_the_cell_and_reward_txt_does_not_stand_in(tmp_path):
    def daemon_down(call):
        if call.argv[1] == "cp" and call.argv[-2].endswith(":/logs/verifier/reward.json"):
            return reply(stderr="Error response from daemon: connection refused", returncode=1)
        return None

    with pytest.raises(RuntimeError, match=r"docker cp of /logs/verifier/reward.json failed \(exit 1\)"):
        _verify(tmp_path, DockerCLI(reward="1.0", answer=daemon_down))


def _copies_a_directory(call):
    if call.argv[1] == "cp" and call.argv[-2].endswith(":/logs/verifier/reward.json"):
        (Path(call.argv[-1]) / "x").mkdir(parents=True)
        return reply()
    return None


def test_a_reward_json_that_is_a_directory_errors_the_cell_and_reward_txt_does_not_stand_in(tmp_path):
    # Harbor's reward_json_path.exists() takes it and its read raises (harbor 0.24.0 verifier.py).
    with pytest.raises(RuntimeError, match="/logs/verifier/reward.json is not a file"):
        _verify(tmp_path, DockerCLI(reward="1", answer=_copies_a_directory))
    # Not left for a rerun: docker-claude's solve clears the reward files before any verify.
    assert not (tmp_path / "o" / "reward.json").exists()


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX modes that bind (not root)")
def test_clearing_the_reward_files_removes_a_directory_with_a_read_only_subdirectory(tmp_path):
    # docker cp keeps the verifier's modes; left there, the next copy would land inside it.
    (tmp_path / "reward.json" / "sub").mkdir(parents=True)
    (tmp_path / "reward.json" / "sub" / "x").write_text("1", encoding="utf-8")
    (tmp_path / "reward.json" / "sub").chmod(0o555)
    (tmp_path / "reward.txt").write_text("1", encoding="utf-8")
    (tmp_path / "keep.log").write_text("", encoding="utf-8")
    clear_reward_files(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["keep.log"]


def _container_links(target: str | None):
    """``docker cp`` of /logs/verifier/reward.json, a link to *target* in the container (None: dangling)."""
    def copy(call):
        if call.argv[1] != "cp" or not call.argv[-2].endswith(":/logs/verifier/reward.json"):
            return None
        if "-L" not in call.argv:  # docker cp keeps the link, whose target the host may not have
            Path(call.argv[-1]).symlink_to("/opt/verdict/reward.json")
        elif target is None:
            return reply(stderr="Error response from daemon: Could not find the file "
                                "/opt/verdict/reward.json in container c1", returncode=1)
        else:
            Path(call.argv[-1]).write_text(target, encoding="utf-8")
        return reply()
    return copy


def test_a_reward_json_that_links_to_a_file_in_the_container_is_read_as_that_file(tmp_path):
    # Harbor reads a link's target too (its docker /logs/verifier is a bind mount: harbor 0.24.0 docker.py).
    assert _verify(tmp_path, DockerCLI(reward="1", answer=_container_links('{"reward": 0.25}'))) == 0.25
    assert not (tmp_path / "o" / "reward.json").is_symlink()


def test_a_dangling_reward_json_link_is_no_reward_json(tmp_path):
    # Harbor's reward_json_path.exists() is false for it, so reward.txt is read (harbor 0.24.0 verifier.py).
    assert _verify(tmp_path, DockerCLI(reward="0.75", answer=_container_links(None))) == 0.75


def test_a_reward_json_directory_that_cannot_be_removed_errors_the_cell(tmp_path, monkeypatch):
    # Left in place, it would take the next copy (docker cp into an existing directory).
    monkeypatch.setattr("dokimi.harnesses.verifier_reward.shutil.rmtree", lambda *_a, **_k: None)
    (tmp_path / "reward.json").mkdir()
    with pytest.raises(RuntimeError, match=r"could not remove .*reward\.json, left by an earlier run"):
        clear_reward_files(tmp_path)


def _host_claude(reward: str, copy=None) -> DockerSolverHarness:
    def answer(call):
        if call.argv[0] != "docker":
            return None
        if call.argv[1] == "cp":  # the verifier's reward files come from the fake, or from *copy*
            return copy(call) if copy else None
        return reply("/app\n")  # the image's WORKDIR, for the host agent's snapshot

    cli = DockerCLI(reward=reward, solver='{"total_cost_usd": 0.42}', answer=answer)
    return DockerSolverHarness(harness_id="docker-claude-host", mode="claude", agent_location="host", runner=cli)


def test_a_docker_claude_rerun_after_a_reward_json_directory_is_scored(tmp_path):
    task = load_task(_harbor(tmp_path))
    out = tmp_path / "out"
    first = _host_claude("1", copy=_copies_a_directory).run(task, model="haiku", output_dir=out)
    assert first.reward is None and "reward.json is not a file" in first.metrics["error"]
    second = _host_claude("0.25").run(task, model="haiku", output_dir=out)
    assert (second.reward, second.metrics.get("error")) == (0.25, None)


def test_exit_zero_without_either_reward_file_still_errors_the_cell(tmp_path):
    with pytest.raises(RuntimeError, match="wrote no /logs/verifier/reward.txt or reward.json"):
        _verify(tmp_path, DockerCLI(reward=None))


@pytest.mark.parametrize(("stale", "cli"), [
    ("reward.json", DockerCLI(reward="0.0")),
    ("reward.txt", DockerCLI(reward=None, reward_json='{"reward": 0.0}')),
])
def test_a_reward_file_left_by_an_earlier_run_is_not_this_runs_reward(tmp_path, stale, cli):
    (tmp_path / "o").mkdir()
    (tmp_path / "o" / stale).write_text("1.0", encoding="utf-8")
    assert _verify(tmp_path, cli) == 0.0
    assert not (tmp_path / "o" / stale).exists()


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


@pytest.mark.parametrize(("directory", "beside", "text"), [
    ("reward.txt", "reward.json", '{"reward": 0.5}'),
    ("reward.json", "reward.txt", "0.5"),
])
def test_a_command_harness_takes_a_directory_named_like_a_reward_file_as_no_reward(
        tmp_path, directory, beside, text):
    (tmp_path / "verifier" / directory).mkdir(parents=True)
    assert _find_reward(tmp_path, "reward: 1", "") is None  # no verdict, not an IsADirectoryError
    (tmp_path / "verifier" / beside).write_text(text, encoding="utf-8")
    assert _find_reward(tmp_path, "", "") == 0.5  # a reward file beside it still counts

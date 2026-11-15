# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A docker verifier's exit code is a verdict only with a reward file behind it."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.harnesses import load_task


def _harbor(root: Path, name: str = "hb-task") -> Path:
    task = root / name
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True)
    (task / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "instruction.md").write_text("Fix it.", encoding="utf-8")
    (task / "task.toml").write_text(
        '[metadata]\ntags = ["onedal"]\n\n[environment]\nworkdir = "/app"\n', encoding="utf-8"
    )
    return task


def test_docker_verify_exit_zero_without_a_reward_file_is_an_error_not_a_pass(tmp_path):
    import io

    from docker_cli_fake import DockerCLI
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    out = tmp_path / "o"
    out.mkdir()
    task = load_task(_harbor(tmp_path))
    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle",
                                  runner=DockerCLI(reward=None))
    # The verifier exited 0 but never wrote reward.txt: no verdict, so the cell is errored.
    with pytest.raises(RuntimeError, match="reward.txt"):
        harness._verify(task, "c1", out, log=io.StringIO())


def test_docker_verify_nonzero_exit_without_a_reward_file_stays_a_fail(tmp_path):
    import io

    from docker_cli_fake import DockerCLI, reply
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    out = tmp_path / "o"
    out.mkdir()
    task = load_task(_harbor(tmp_path))

    def failing_verifier(call):
        if "-lc" in call.argv:
            return reply(returncode=1)
        return None

    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle",
                                  runner=DockerCLI(reward=None, answer=failing_verifier))
    assert harness._verify(task, "c1", out, log=io.StringIO()) == 0.0


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("text", ["PASS\n", ""])
def test_docker_verify_a_reward_file_with_no_number_is_an_error(tmp_path, text, exit_code):
    import io

    from docker_cli_fake import DockerCLI, reply
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    out = tmp_path / "o"
    out.mkdir()
    task = load_task(_harbor(tmp_path))
    verifier = (lambda call: reply(returncode=exit_code) if "-lc" in call.argv else None)
    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle",
                                  runner=DockerCLI(reward=text, answer=verifier))
    # A reward file with no reward is no verdict, whatever the exit code (as in a command harness).
    with pytest.raises(RuntimeError, match="reward.txt has no reward"):
        harness._verify(task, "c1", out, log=io.StringIO())


_NO_FILE = "Error response from daemon: Could not find the file /logs/verifier/reward.txt in container c1"


@pytest.mark.parametrize(("stderr", "exit_code", "outcome"), [
    (_NO_FILE, 1, 0.0),  # docker's own "could not find the file": still a verifier that wrote none
    (_NO_FILE, 0, "wrote no /logs/verifier/reward.txt"),
    ("Error response from daemon: connection refused", 0, r"docker cp .* failed \(exit 1\)"),
    ("Error response from daemon: connection refused", 1, r"docker cp .* failed \(exit 1\)"),
    # podman's wording for a missing file (from memory; no podman on this box): still no reward file.
    ('"/logs/verifier/reward.txt" could not be found on container c1: no such file or directory', 1,
     0.0),
    ('"/logs/verifier/reward.txt" could not be found on container c1: no such file or directory', 0,
     "wrote no /logs/verifier/reward.txt"),
    # A missing Docker socket says "no such file or directory" but names no reward file.
    ("dial unix /var/run/docker.sock: connect: no such file or directory", 1,
     r"docker cp .* failed \(exit 1\)"),
    ("dial unix /var/run/docker.sock: connect: no such file or directory", 0,
     r"docker cp .* failed \(exit 1\)"),
])
def test_docker_verify_a_failed_reward_copy_is_not_a_missing_reward_file(
        tmp_path, stderr, exit_code, outcome):
    import io

    from docker_cli_fake import DockerCLI, reply
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    out = tmp_path / "o"
    out.mkdir()
    task = load_task(_harbor(tmp_path))

    def answer(call):
        if "-lc" in call.argv:
            return reply(returncode=exit_code)
        if call.argv[1] == "cp" and call.argv[2].endswith(":/logs/verifier/reward.txt"):
            return reply(stderr=stderr, returncode=1)
        return None

    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle",
                                  runner=DockerCLI(reward="1.0", answer=answer))
    if isinstance(outcome, float):
        assert harness._verify(task, "c1", out, log=io.StringIO()) == outcome
    else:
        with pytest.raises(RuntimeError, match=outcome):
            harness._verify(task, "c1", out, log=io.StringIO())

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for cleanup timeout handling in docker-claude and docker-claude-host.

These tests verify that timeouts during sidecar cleanup, pre-verifier reap, and
recompile are handled correctly:
- Sidecar removal timeout (stop_proxy): Require successful removal before advancing; retry on close.
- Pre-verifier reap timeout: Abort the cell on a timeout.
- Recompile timeout: Abort the cell on a timeout.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from dokimi.harnesses import container_agent as container_mod
from dokimi.harnesses import load_task
from dokimi.harnesses.registry import create_harness
from dokimi.process import reply
# _hermetic is autouse: no operator Claude settings, provider env or host `claude` reach a test.
from tests.test_docker_solver_harness import _fixture_task, _hermetic, _pipeline  # noqa: F401


def test_stop_proxy_timeout_aborts_before_verification(tmp_path):
    """If sidecar removal times out, abort before verification runs.

    This prevents the credential-bearing sidecar from continuing to run while
    verification is in progress.
    """
    task = load_task(_fixture_task(tmp_path))
    # Mock docker to timeout on rm (sidecar removal - dokimi-egress-* container)
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "rm" and "egress" in call.argv[-1]) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The timeout should result in returncode 124 and reward 0.0
    assert result.returncode == 124, "Should timeout when sidecar removal times out"
    assert result.reward == 0.0, "Should get reward 0.0 on timeout"

    # Verify that verify was NOT called (it comes after stop_proxy)
    test_calls = [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    assert len(test_calls) == 0, "Verification should not run after stop_proxy timeout"


def test_stop_proxy_timeout_retried_in_close(tmp_path):
    """If sidecar removal timed out during stop_proxy, close() retries as best-effort.

    The close() method should catch the TimeoutExpired from stop_proxy and
    continue with network cleanup rather than propagating the timeout.
    """
    task = load_task(_fixture_task(tmp_path))

    # Mock docker to always timeout on rm (sidecar removal)
    # This ensures both stop_proxy and close() attempts fail with timeout
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "rm" and "egress" in call.argv[-1]) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The timeout during stop_proxy will prevent verification and result in timeout returncode
    assert result.returncode == 124, "Should timeout when sidecar removal times out"
    assert result.reward == 0.0, "Should get reward 0.0 on stop_proxy timeout"

    # Verify that verify (test.sh) was NOT called
    test_calls = [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    assert len(test_calls) == 0, "Verification should not run after stop_proxy timeout"

    # Verify that rm was called for egress cleanup (stop_proxy + retry in close)
    rm_calls = [c for c in cli.calls if c.argv[1] == "rm" and "egress" in " ".join(c.argv)]
    assert len(rm_calls) == 3, f"Should attempt sidecar removal 3 times (open + stop_proxy + close), got {len(rm_calls)}"

    # close() goes on past the timed-out sidecar removal: the egress record is saved
    # and the network is removed after the sidecar's last removal attempt.
    assert (tmp_path / "out" / "egress.jsonl").exists(), "close() should still save egress.jsonl"
    last_sidecar_rm = max(i for i, c in enumerate(cli.calls)
                          if c.argv[1] == "rm" and "egress" in " ".join(c.argv))
    network_rm = [i for i, c in enumerate(cli.calls) if list(c.argv[1:3]) == ["network", "rm"]]
    assert network_rm and network_rm[-1] > last_sidecar_rm, "close() should remove the network after the sidecar"


def test_reap_timeout_aborts_cell(tmp_path):
    """If pre-verifier reap times out, abort the cell before verification.

    This prevents running agents' processes from accessing the verifier's test files
    or modifying /app while verification is in progress.
    """
    task = load_task(_fixture_task(tmp_path))

    # Mock docker to timeout on the kill command (reap)
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and "kill -9" in call.argv[-1]) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The reap timeout should result in returncode 124 and reward 0.0
    assert result.returncode == 124, "Should timeout when reap times out"
    assert result.reward == 0.0, "Should get reward 0.0 on reap timeout"

    # Verify that verify (test.sh) was NOT called
    test_calls = [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    assert len(test_calls) == 0, "Verification should not run after reap timeout"

    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert "TIMEOUT" in log or "timed out" in log

    # The rest of the cleanup still ran: the agent's tree is kept, the credential files
    # are removed, and the solve's stream and cost are recorded before the cleanup.
    assert (tmp_path / "out" / "work").is_dir()
    assert any(c.argv[1] == "cp" and ":/app/." in c.argv[2] for c in cli.calls)
    assert any(c.argv[1] == "exec" and "-rf" in c.argv and "files" in c.argv[-1] for c in cli.calls)
    assert (tmp_path / "out" / "solver.jsonl").is_file()


def test_workdir_inspect_timeout_refuses_the_cell(tmp_path):
    """A timed-out ``docker inspect`` of the image WORKDIR must not fall back to /app.

    /app is only a guess, and no agent ran, so there is no verdict: the cell is
    errored (the harness raises), not billed as a reward-0 failure.
    """
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "inspect") else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # An errored cell: no verdict (reward None, not 0), and the error is recorded.
    assert result.reward is None and result.passed is False
    assert "WORKDIR" in result.metrics["error"]
    assert not [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]


def test_state_inspect_timeout_errors_the_cell(tmp_path):
    """A timed-out ``{{.State.Running}}`` inspect at verify time is no answer, not "stopped".

    The verifier failed with no reward file, so the container's state decides between an
    errored cell and a scored failure. A timeout must error the cell rather than fall
    through to the verifier's exit code.
    """
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=None, answer=lambda call: (
        reply(returncode=1) if call.argv[1] == "exec" and "test.sh" in " ".join(call.argv) else
        reply(timed_out=True) if call.argv[1] == "inspect" and "{{.State.Running}}" in call.argv else None))

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.reward is None and result.passed is False
    assert "state inspect timed out" in result.metrics["error"]


def test_workdir_presence_timeout_errors_the_cell(tmp_path):
    """A timed-out ``test -d`` of the WORKDIR is no answer: the cell is errored before any agent runs.

    Exit 124 is not exit 1 ("no such directory"), so it must not read as "present" either.
    """
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and "test" in call.argv and "-d" in call.argv) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.reward is None and result.passed is False
    assert "test -d" in result.metrics["error"]
    assert not [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]


def test_sidecar_log_kept_when_teardown_retry_cannot_read_it(tmp_path):
    """A failed second ``docker logs`` (the sidecar already removed) keeps the first read's decisions.

    Otherwise egress.jsonl is written empty and a denied request disappears from the record.
    """
    row = json.dumps({"host": "evil.example", "port": 443, "decision": "denied", "reason": "x"})
    logs = iter([reply(row + "\n"), reply(stderr="No such container", returncode=1)])
    rms = iter([reply(timed_out=True), reply()])

    def docker(args, log=None, *, check=True, timeout=None):
        if args[0] == "logs":
            return next(logs)
        if args[0] == "rm":
            return next(rms)
        if args[:2] == ["network", "rm"]:  # close() drops the internal network last
            return reply()
        raise AssertionError(f"unexpected docker call: {args}")

    sandbox = container_mod.ContainerSandbox(network="dokimi-net-x", proxy_name="dokimi-egress-x",
                                             allow=[], binary=None)
    with pytest.raises(subprocess.TimeoutExpired):
        sandbox.stop_proxy(docker, None)
    sandbox.close(docker, None, tmp_path)

    assert [r["host"] for r in sandbox.log] == ["evil.example"]
    assert sandbox.egress_summary()["status"] == "violated"
    saved = (tmp_path / "egress.jsonl").read_text(encoding="utf-8")
    assert "evil.example" in saved


def test_recompile_timeout_aborts_cell(tmp_path):
    """If recompile times out, abort the cell before verification.

    This prevents stale or partially-built binaries from being verified,
    and stops compilation from continuing to consume resources.

    Note: recompile is called only in claude modes (not oracle or probe).
    """
    task = load_task(_fixture_task(tmp_path))

    # Mock docker to timeout on g++ command (recompile) after solve
    # The g++ command is part of the recompile phase which happens after claude solve
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and "-lc" in call.argv and "g++" in " ".join(call.argv)) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The recompile timeout should result in returncode 124 and reward 0.0
    assert result.returncode == 124, f"Should timeout when recompile times out; got {result.returncode}"
    assert result.reward == 0.0, "Should get reward 0.0 on recompile timeout"

    # Verify that verify (test.sh) was NOT called
    test_calls = [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    assert len(test_calls) == 0, "Verification should not run after recompile timeout"

    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert "TIMEOUT" in log or "timed out" in log


def test_credential_file_removal_timeout_aborts_cell(tmp_path):
    """Credential-file removal timeout aborts the cell before verification.

    The pre-verifier cleanup removes credential files at FILES_DIR. If this
    operation times out, verification must not proceed, as test.sh runs as
    root and could access leftover credential files.
    """
    task = load_task(_fixture_task(tmp_path))

    # Mock docker to timeout on rm of FILES_DIR (credential file removal)
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and "-rf" in call.argv and "files" in " ".join(call.argv)) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The timeout should result in returncode 124 and reward 0.0
    assert result.returncode == 124, f"Should timeout when credential removal times out; got {result.returncode}"
    assert result.reward == 0.0, "Should get reward 0.0 on credential removal timeout"

    # Verify that verify (test.sh) was NOT called
    test_calls = [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    assert len(test_calls) == 0, "Verification should not run after credential removal timeout"


def test_reward_file_copy_timeout_fails_task(tmp_path):
    """Docker cp of reward.txt timeout fails the task (reward 0.0, rc 124).

    If the copy of reward.txt from the container times out, the verifier's
    decision is lost. The task must fail as reward 0.0 rather than proceeding
    with an empty reward file or an incorrect assumption.
    """
    task = load_task(_fixture_task(tmp_path))

    # Mock docker to timeout on cp of reward.txt from container
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "cp" and "reward.txt" in " ".join(call.argv)) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The timeout should result in returncode 124 and reward 0.0
    assert result.returncode == 124, f"Should timeout when reward.txt copy times out; got {result.returncode}"
    assert result.reward == 0.0, "Should get reward 0.0 when reward.txt copy times out"


def test_plugin_removal_timeout_aborts_cell(tmp_path):
    """A plugin-directory removal that times out aborts the cell before verification."""
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and "-rf" in call.argv and "abskill" in call.argv[-1]) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.returncode == 124
    assert result.reward == 0.0
    assert not [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]


def test_reward_copy_timeout_does_not_fall_back_to_the_exit_code(tmp_path):
    """A verifier that exits 0 must not pass because the reward.txt copy timed out."""
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=0.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "cp" and "reward.txt" in " ".join(call.argv)) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    assert (result.returncode, result.reward, result.passed) == (124, 0.0, False)

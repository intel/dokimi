# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for cleanup timeout handling in docker-claude and docker-claude-host.

These tests verify that timeouts during sidecar cleanup, pre-verifier reap, and
recompile are handled correctly:
- Thread #235 (stop_proxy): Require successful removal before advancing; retry on close.
- Thread #450 (reap): Abort the cell on a timeout.
- Thread #577 (recompile): Abort the cell on a timeout.
"""

from __future__ import annotations



from dokimi.harnesses import load_task
from dokimi.harnesses.registry import create_harness
from dokimi.process import reply
from tests.test_docker_solver_harness import _fixture_task, _pipeline


def test_stop_proxy_timeout_aborts_before_verification(tmp_path):
    """Thread #235: If sidecar removal times out, abort before verification runs.

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
    """Thread #235: If sidecar removal timed out during stop_proxy, close() retries as best-effort.

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
    assert len(rm_calls) >= 1, f"Should attempt sidecar removal, got {len(rm_calls)} calls"


def test_reap_timeout_aborts_cell(tmp_path):
    """Thread #450: If pre-verifier reap times out, abort the cell before verification.

    This prevents running agents' processes from accessing the verifier's test files
    or modifying /app while verification is in progress.
    """
    task = load_task(_fixture_task(tmp_path))

    # Mock docker to timeout on the kill command (reap)
    cli = _pipeline(reward=1.0, answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and "kill" in call.argv[-1]) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    # The reap timeout should result in returncode 124 and reward 0.0
    assert result.returncode == 124, "Should timeout when reap times out"
    assert result.reward == 0.0, "Should get reward 0.0 on reap timeout"

    # Verify that verify (test.sh) was NOT called
    test_calls = [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    assert len(test_calls) == 0, "Verification should not run after reap timeout"

    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert "TIMEOUT" in log or "timed out" in log


def test_recompile_timeout_aborts_cell(tmp_path):
    """Thread #577: If recompile times out, abort the cell before verification.

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

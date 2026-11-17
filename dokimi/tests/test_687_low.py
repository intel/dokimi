# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#687 Low: state that lies after a rerun or a failed step (https://github.com/intel/dokimi/issues/687)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from dokimi.harnesses import container_agent as container_mod
from dokimi.harnesses import load_task
from dokimi.harnesses.registry import create_harness
from dokimi.personas.analyzer import PersonaAnalyzer
from dokimi.process import reply
# _hermetic is autouse: no operator Claude settings, provider env or host `claude` reach a test.
from tests.test_docker_solver_harness import _fixture_task, _hermetic, _pipeline  # noqa: F401

# ── personas/analyzer.py: the use-case section parser ─────────────────────────


def test_a_use_case_bullet_that_names_the_keyword_is_kept():
    readme = "## Use cases\n- Web applications\n- Batch jobs\n\n## Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["web applications", "batch jobs"]


def test_a_bullet_that_names_the_keyword_opens_no_section():
    readme = "## Features\n- Runs any application\n- Fast\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == []


def test_a_bold_use_case_line_still_opens_a_section():
    readme = "**Use cases**\n* Robotics\n\n## Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["robotics"]


def test_the_pygithub_path_keeps_a_keyword_bullet_too():
    repo = Mock()
    repo.get_readme.return_value.decoded_content = b"## When to use\n- Desktop applications\n- CLIs\n"
    assert PersonaAnalyzer()._extract_use_cases(repo) == ["desktop applications", "clis"]


# ── harnesses/container_agent.py: a sidecar whose removal failed is not stopped ─


def _sidecar_rm(call) -> bool:
    """A removal of the solve's sidecar, not of the verifier's that open() also clears."""
    return call.argv[1] == "rm" and "egress" in call.argv[-1] and not call.argv[-1].endswith("-verify")


def test_a_failed_sidecar_removal_errors_the_cell_and_close_retries_it(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply(stderr="Error response from daemon: busy", returncode=1)
                    if _sidecar_rm(call) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    assert (result.returncode, result.reward, result.passed) == (1, None, False)
    assert "docker rm dokimi-egress-" in result.metrics["error"]
    assert not [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    # open() clears a leftover, stop_proxy() fails, close() tries again and still drops the network
    removals = [i for i, c in enumerate(cli.calls) if _sidecar_rm(c)]
    assert len(removals) == 3
    assert [i for i, c in enumerate(cli.calls) if list(c.argv[1:3]) == ["network", "rm"]][-1] > removals[-1]


def test_a_verifier_sidecar_whose_removal_failed_is_removed_again_on_close():
    removals: list[str] = []

    def docker(args, log=None, *, check=True, timeout=None):
        if args[0] == "create":
            raise RuntimeError("docker create failed (exit 1); see run.log")
        if args[0] == "rm":
            removals.append(args[-1])
            if args[-1] == "p-verify" and removals.count("p-verify") == 2:  # the removal after the failed start
                return reply(stderr="Error response from daemon: busy", returncode=1)
        return reply()

    sandbox = container_mod.ContainerSandbox(network="n", proxy_name="p", allow=[], binary=None)
    with pytest.raises(RuntimeError, match="docker create failed"):
        sandbox.verifier_sidecar(docker, None, ["pypi.org"])
    sandbox.close(docker, None, None)

    assert removals.count("p-verify") == 3

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The per-phase network policy of docker-* runs: where it comes from, and what each phase gets.

Docker is never run: :class:`DockerCLI` records the argv of every docker step,
so the tests read which network the container is on when the oracle and the
verifier execute.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from dokimi.harnesses import TaskSuiteRunner, load_task
from dokimi.harnesses.network_policy import parse_override, resolve
from dokimi.harnesses.registry import create_harness
from docker_cli_fake import DockerCLI


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    for name in list(os.environ):
        if name.lower() in ("http_proxy", "https_proxy", "no_proxy", "all_proxy"):
            monkeypatch.delenv(name)


def _task(tmp_path: Path, toml: str = "") -> Path:
    task = tmp_path / "net-task"
    for d in ("tests", "environment", "solution"):
        (task / d).mkdir(parents=True, exist_ok=True)
    (task / "instruction.md").write_text("x", encoding="utf-8")
    (task / "task.toml").write_text("[metadata]\ntags = []\n" + toml, encoding="utf-8")
    return task


def _modes(plan) -> dict[str, str]:
    return {p: getattr(plan, p).label() for p in ("build", "solve", "verify")}


# ── resolution ──────────────────────────────────────────────────────────────


def test_a_task_that_says_nothing_gets_no_network_after_the_build(tmp_path):
    plan = resolve(load_task(_task(tmp_path)))
    assert _modes(plan) == {"build": "public", "solve": "none", "verify": "none"}
    assert plan.verify.source == "default"


def test_environment_public_reaches_the_oracle_and_verifier_but_not_an_agent(tmp_path):
    task = load_task(_task(tmp_path, '[environment]\nnetwork_mode = "public"\n'))
    assert _modes(resolve(task)) == {"build": "public", "solve": "public", "verify": "public"}
    agent = resolve(task, agent=True)
    assert agent.solve.mode == "none" and agent.verify.mode == "public"
    assert agent.verify.source == "task [environment] network_mode"


def test_harbor_phase_overrides_and_allowlists_are_honoured(tmp_path):
    task = load_task(_task(tmp_path, (
        '[environment]\nnetwork_mode = "no-network"\n'
        '[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = ["pypi.org", "files.pythonhosted.org"]\n'
        '[verifier]\nnetwork_mode = "public"\n')))
    plan = resolve(task, agent=True)
    assert plan.solve.hosts == ("files.pythonhosted.org:443", "pypi.org:443")
    assert plan.solve.source == "task [agent] network_mode"
    assert plan.verify.mode == "public"


def test_harbor_allow_internet_false_is_none_and_true_is_public(tmp_path):
    off = load_task(_task(tmp_path / "a", "[environment]\nallow_internet = false\n"))
    on = load_task(_task(tmp_path / "b", "[environment]\nallow_internet = true\n"))
    assert resolve(off).verify.mode == "none"
    assert resolve(on).verify.label() == "public"


def test_dokimi_egress_widens_the_agent_solve_into_an_allowlist(tmp_path):
    task = load_task(_task(tmp_path, '[environment]\negress = ["huggingface.co"]\n'))
    plan = resolve(task, agent=True)
    assert plan.solve.label() == "allowlist:huggingface.co:443"
    assert resolve(task).solve.mode == "none"  # the oracle is not the agent


def test_cli_overrides_win_over_the_task(tmp_path):
    task = load_task(_task(tmp_path, '[environment]\nnetwork_mode = "public"\n'))
    plan = resolve(task, ["verify=none", "solve=allowlist:httpbin.org:80"])
    assert _modes(plan) == {"build": "public", "solve": "allowlist:httpbin.org:80", "verify": "none"}
    assert plan.verify.source == "cli"
    assert _modes(resolve(task, ["all=none"])) == {"build": "none", "solve": "none", "verify": "none"}


@pytest.mark.parametrize("spec, why", [
    ("verify", "expected PHASE=MODE"),
    ("grade=none", "unknown phase grade"),
    ("verify=bridge", "expected one of none, allowlist, public"),
    ("verify=allowlist", "allowlist needs hosts"),
    ("build=allowlist:pypi.org", "the image build takes none or public"),
    ("verify=public:pypi.org", "only valid with mode allowlist"),
    ("verify=allowlist:https://pypi.org/simple", "expected host"),
])
def test_bad_overrides_are_refused_with_the_reason(spec, why):
    with pytest.raises(ValueError, match=why):
        parse_override(spec)


def test_an_unknown_task_network_mode_refuses_the_cell(tmp_path):
    task = load_task(_task(tmp_path, '[environment]\nnetwork_mode = "host"\n'))
    with pytest.raises(ValueError, match="network mode 'host'"):
        create_harness("docker-oracle").run(task, output_dir=tmp_path / "out", dry_run=True)


# ── what each phase gets ────────────────────────────────────────────────────


def _oracle(tmp_path: Path, *overrides: str, toml: str = "") -> tuple[DockerCLI, dict]:
    cli = DockerCLI(reward="1.0")
    harness = create_harness("docker-oracle", runner=cli)
    harness = replace(harness, network=overrides)
    result = harness.run(load_task(_task(tmp_path, toml)), output_dir=tmp_path / "out")
    assert result.reward == 1.0
    return cli, result.metrics


def _index(args: list[list[str]], pred) -> int:
    return next(i for i, a in enumerate(args) if pred(a))


def _exec_of(script: str):
    return lambda a: a[0] == "exec" and script in a[-1]


def test_default_oracle_run_is_offline_from_start_to_verdict(tmp_path):
    cli, metrics = _oracle(tmp_path)
    run = next(a for a in cli.args if a[0] == "run")
    assert run[run.index("--network") + 1] == "none"
    assert not any(a[0] == "network" for a in cli.args)
    build = next(a for a in cli.args if a[0] == "build")
    assert build[build.index("--network") + 1] == "host"
    assert metrics["network"]["verify"] == {"mode": "none", "hosts": [], "source": "default"}


def test_a_public_verifier_joins_bridge_only_after_an_offline_solve(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@proxy.corp:912")
    cli, metrics = _oracle(tmp_path, "verify=public")
    args = cli.args
    run = next(a for a in args if a[0] == "run")
    assert run[run.index("--network") + 1] == "bridge"
    leave = _index(args, lambda a: a[:2] == ["network", "disconnect"])
    oracle = _index(args, _exec_of("solve.sh"))
    join = _index(args, lambda a: a[:2] == ["network", "connect"])
    verify = _index(args, _exec_of("test.sh"))
    assert leave < oracle < join < verify
    assert "HTTPS_PROXY=http://user:pw@proxy.corp:912" in args[verify]
    assert not any("PROXY" in x for x in args[oracle])
    assert metrics["network"]["verify"]["source"] == "cli"


def test_an_allowlisted_verifier_goes_through_its_own_sidecar(tmp_path):
    cli, metrics = _oracle(tmp_path, "verify=allowlist:pypi.org,files.pythonhosted.org")
    args = cli.args
    assert any(a[:3] == ["network", "create", "--internal"] for a in args)
    side = next(a for a in args if a[0] == "create" and a[a.index("--name") + 1].endswith("-verify"))
    assert side[side.index("--allow") + 1] == "files.pythonhosted.org:443,pypi.org:443"
    verify = args[_index(args, _exec_of("test.sh"))]
    assert "HTTPS_PROXY=http://egress:3128" in verify
    oracle = args[_index(args, _exec_of("solve.sh"))]
    assert not any("PROXY" in x for x in oracle)
    assert metrics["network"]["verify_egress"]["status"] == "ok"
    saved = json.loads((tmp_path / "out" / "network.json").read_text(encoding="utf-8"))
    assert saved["verify_egress"] == metrics["network"]["verify_egress"]  # the cell record has it too


def test_build_none_builds_offline(tmp_path):
    cli, _ = _oracle(tmp_path, "build=none")
    build = next(a for a in cli.args if a[0] == "build")
    assert build[build.index("--network") + 1] == "none" and "--build-arg" not in build


def test_a_task_public_oracle_keeps_its_network_unless_the_operator_closes_it(tmp_path):
    toml = '[environment]\nnetwork_mode = "public"\n'
    cli, _ = _oracle(tmp_path / "a", toml=toml)
    assert not any(a[:2] == ["network", "disconnect"] for a in cli.args)
    cli, metrics = _oracle(tmp_path / "b", "all=none", toml=toml)
    run = next(a for a in cli.args if a[0] == "run")
    assert run[run.index("--network") + 1] == "none"
    assert metrics["network"]["verify"]["source"] == "cli"


@pytest.mark.parametrize("arm", ["docker-claude-host", "docker-probe-host"])
def test_a_verify_allowlist_leaves_a_host_arm_on_the_host(tmp_path, arm):
    toml = '[verifier]\nnetwork_mode = "allowlist"\nallowed_hosts = ["pypi.org"]\n'
    cli = DockerCLI(reward="1.0", solver='{"type": "result", "result": "ok"}')
    result = create_harness(arm, runner=cli).run(load_task(_task(tmp_path, toml)), output_dir=tmp_path / "out")
    assert result.metrics["isolation"]["agent_location"] == "host"
    assert not any(a[0] == "exec" and "-c" in a and "--env-file" in a for a in cli.args)  # no probe inside
    assert all(c.argv[0] != "docker" for c in cli.solver_calls())

def test_the_suite_runner_refuses_network_for_a_harness_it_cannot_apply_to(tmp_path):
    task = load_task(_task(tmp_path))
    with pytest.raises(ValueError, match="docker-\\* harnesses only"):
        TaskSuiteRunner(tasks=[task], harnesses=["codex"], network=("verify=none",)).validate_harnesses()
    with pytest.raises(ValueError, match="unknown phase"):
        TaskSuiteRunner(tasks=[task], harnesses=["docker-oracle"], network=("x=none",)).validate_harnesses()
    runner = TaskSuiteRunner(tasks=[task], harnesses=["docker-oracle"], network=("verify=public",),
                             output_root=tmp_path / "r", dry_run=True)
    row = runner.run()["results"][0]
    assert row["metrics"]["network"]["verify"]["mode"] == "public"


def _skill(tmp_path: Path, network: str) -> Path:
    skill = tmp_path / "docs-fetch"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: docs-fetch\ndescription: Fetch the upstream docs.\n"
        "compatibility: needs network access to pypi.org\n"
        f"metadata:\n  network: \"{network}\"\n---\nbody\n", encoding="utf-8")
    return skill


def test_a_skill_declares_its_hosts_and_the_run_says_which_it_did_not_grant(tmp_path):
    from dokimi.harnesses.network_policy import skill_needs

    task = load_task(_task(tmp_path))
    skill = _skill(tmp_path, "allowlist:pypi.org,files.pythonhosted.org")
    closed = skill_needs(skill, resolve(task, agent=True))
    assert closed == {"declared": "allowlist:files.pythonhosted.org:443,pypi.org:443",
                      "missing": ["files.pythonhosted.org:443", "pypi.org:443"]}
    granted = skill_needs(skill, resolve(task, ["solve=allowlist:pypi.org,files.pythonhosted.org"], agent=True))
    assert granted is not None and granted["missing"] == []
    assert skill_needs(_skill(tmp_path / "p", "public"), resolve(task, agent=True))["missing"] == ["*"]
    harness = create_harness(f"docker-claude-skill:{skill}")
    metrics = harness.run(task, output_dir=tmp_path / "out", dry_run=True).metrics
    assert metrics["network"]["skill"]["missing"] == closed["missing"]  # recorded, not widened
    assert metrics["network"]["solve"]["mode"] == "none"


@pytest.mark.parametrize("value", ["pypi.org", "true", "allowlist:bad host!"])
def test_an_unreadable_skill_network_is_recorded_and_the_arm_still_runs(tmp_path, value):
    skill = _skill(tmp_path, value)
    harness = create_harness(f"docker-claude-skill:{skill}")
    metrics = harness.run(load_task(_task(tmp_path)), output_dir=tmp_path / "out", dry_run=True).metrics
    got = metrics["network"]["skill"]
    assert got["declared"] == value and got["missing"] is None and got["error"]


def test_an_errored_cell_still_records_its_network(tmp_path, monkeypatch):
    harness = replace(create_harness("docker-oracle", runner=DockerCLI()), network=("verify=public",))

    def broken(*_a, **_k):
        raise RuntimeError("docker build failed (exit 1); see run.log")

    monkeypatch.setattr(type(harness), "_build_image", broken)
    with pytest.raises(RuntimeError, match="docker build failed"):
        harness.run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert json.loads((tmp_path / "out" / "network.json").read_text())["verify"]["mode"] == "public"

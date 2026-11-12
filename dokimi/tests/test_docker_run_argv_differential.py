# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Differential pin of every ``docker run`` argv the package builds, byte for byte.

``tests/fixtures/docker/run_argv_golden.json`` holds what each call site built before its
flags moved into :mod:`dokimi.docker_cmd` and :class:`dokimi.container_policy.ContainerPolicy`.
:func:`capture` replays the same inputs through the public entry points and must reproduce
that file exactly: flag order, values and the argv tail included. A diff here is an argv
change, which is a behaviour change of the docker command, not a refactor. A planned change is
re-taken with ``scripts/refresh_docker_run_golden.py``, and the diff of that file is reviewed.

Call sites covered: the repo container (``DockerRunner.argv``), the example runner's docker
backend, the solver's task container (``_start_container``, with and without an egress
sandbox, every network mode, cpus and memory spelling), the fidelity capture's client
(``run_docker``) and the sandbox's own ``run_args``. Errors are captured as values too.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from dokimi.harnesses import fidelity
from dokimi.harnesses.container_agent import ContainerSandbox
from dokimi.harnesses.docker_solver import DockerSolverHarness
from dokimi.harnesses.tasks import load_task
from dokimi.metrics.example_runner import EXECUTORS, ExecutionPolicy, run_example
from dokimi.process import FakeRunner, reply
from dokimi.repos import container
from dokimi.repos.container import CONTENT_DIR, ContainerSpec, DockerRunner, Mount

from docker_cli_fake import DockerCLI
from test_tbench_v1 import _tb1  # a terminal-bench task: the one layout whose container_env is not empty

GOLDEN = Path(__file__).parent / "fixtures" / "docker" / "run_argv_golden.json"
_TMP = "<tmp>"


def _guarded(fn, *args, **kwargs) -> Any:
    """The call's result, or the error it raised, as a JSON-able value."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the error text is part of what is pinned
        return {"raises": f"{type(exc).__name__}: {exc}"}


def _repo_cases(out: dict[str, Any]) -> None:
    full = ContainerSpec(
        image="img",
        argv=["python3", "x"],
        mounts=[Mount(Path("/h/run"), "/eval/run"), Mount(Path("/h/content"), CONTENT_DIR, read_only=True)],
        env={"HOME": "/eval/run/home"},
        pass_env=("AWS_BEARER_TOKEN_BEDROCK",),
        network="none",
        name="dokimi-x",
        user="1000:1000",
    )
    specs = {
        "full": full,
        "unknown-network": ContainerSpec(image="i", argv=[], network="wide-open"),
        "comma-in-mount": ContainerSpec(image="i", argv=[], mounts=[Mount(Path("/h/a,b"), "/eval/w")]),
        "writable-mount": ContainerSpec(image="i", argv=[], mounts=[Mount(Path("/h/w"), "/eval/w")]),
        "workdir-only": ContainerSpec(image="img", argv=["true"]),
    }
    for net in ("none", "bridge", "host"):
        specs[f"network-{net}"] = ContainerSpec(image="img", argv=["make"], network=net)
        specs[f"network-{net}-named"] = ContainerSpec(image="img", argv=["make"], network=net, name="n")
    for label, uid_gid in (("no-uid", None), ("uid-4242", "4242:4343")):
        for name, spec in specs.items():
            with mock.patch.object(container, "host_user", lambda u=uid_gid: u):
                out[f"repo/{label}/{name}"] = _guarded(DockerRunner().argv, spec)


def _example_outcome(lang: str, policy: ExecutionPolicy) -> tuple[Any, list[list[str]], list[float]]:
    """The outcome, the argv (per-run temp dir masked) and the timeout of one docker-backend run."""
    runner = FakeRunner(lambda call: reply())

    def run() -> None:
        run_example(lang, "print(1)", policy, runner)

    outcome = _guarded(run)
    argv = [[_TMP if a.endswith(":/work:ro") else a for a in c.argv] for c in runner.calls]
    return outcome, argv, [c.timeout for c in runner.calls]


def _example_cases(out: dict[str, Any]) -> None:
    variants = {
        "default": ExecutionPolicy(backend="docker", timeout=7),
        "resources": ExecutionPolicy(backend="docker", timeout=3, docker_image="repo/img:1", mem_mb=1024, cpus=2.5),
        "small": ExecutionPolicy(backend="docker", timeout=1, mem_mb=1, cpus=0.5),
        "no-memory-cap": ExecutionPolicy(backend="docker", timeout=5, mem_mb=0),
    }
    for vname, policy in variants.items():
        for lang in sorted(EXECUTORS):
            outcome, argv, timeouts = _example_outcome(lang, policy)
            out[f"example/{vname}/{lang}"] = {"argv": argv, "timeout": timeouts, "outcome": outcome}


_TASK_NETWORKS = {
    "absent": "",
    "none": 'network_mode = "no-network"',
    "public": 'network_mode = "public"',
    "allowlist": 'network_mode = "allowlist"\nallowed_hosts = ["pypi.org"]',
}
_TASK_CPUS = {"absent": "", "zero": "cpus = 0", "two": "cpus = 2", "string": 'cpus = "3"'}
_TASK_MEMORY = {"absent": "", "int": "memory_mb = 512", "suffixed": 'memory_mb = "512m"'}


def _task_dir(root: Path, name: str, env_lines: list[str]) -> Path:
    task = root / name
    (task / "tests").mkdir(parents=True)
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("x", encoding="utf-8")
    body = "\n".join(line for line in env_lines if line)
    (task / "task.toml").write_text(f'[metadata]\ntags = []\n\n[environment]\n{body}\n', encoding="utf-8")
    return task


def _solver_run_calls(task, *, mode: str, network: tuple[str, ...] = (), agent_location: str = "container",
                      sandbox: ContainerSandbox | None = None) -> list[list[str]]:
    cli = DockerCLI()
    harness = DockerSolverHarness(harness_id=f"docker-{mode}", mode=mode, runner=cli, network=network,
                                  agent_location=agent_location)
    harness._start_container(task, "img:latest", "container-x", io.StringIO(), sandbox=sandbox)
    return [c for c in cli.args if c[0] == "run"]


def _task_cases(root: Path, out: dict[str, Any]) -> None:
    for nname, net in _TASK_NETWORKS.items():
        for cname, cpus in _TASK_CPUS.items():
            for mname, mem in _TASK_MEMORY.items():
                task = load_task(_task_dir(root, f"{nname}-{cname}-{mname}", [net, cpus, mem]))
                out[f"task/oracle/{nname}/{cname}/{mname}"] = _guarded(_solver_run_calls, task, mode="oracle")
    task = load_task(_task_dir(root, "override", [_TASK_NETWORKS["allowlist"], _TASK_CPUS["two"]]))
    for net in (("solve=public",), ("verify=none",), ("solve=public", "verify=allowlist:example.org")):
        out[f"task/oracle/override/{'+'.join(net)}"] = _guarded(_solver_run_calls, task, mode="oracle", network=net)
    for mode, location in (("claude", "container"), ("claude", "host")):
        for nname, net in _TASK_NETWORKS.items():
            task = load_task(_task_dir(root, f"agent-{location}-{nname}", [net, _TASK_CPUS["two"]]))
            key = f"task/{mode}-{location}/{nname}"
            out[key] = _guarded(_solver_run_calls, task, mode=mode, agent_location=location)
    sandbox = ContainerSandbox(network="dokimi-net-r1", proxy_name="dokimi-egress-r1", allow=["pypi.org"],
                               binary=None, label="dokimi.run=r1")
    for nname in ("allowlist", "public"):
        task = load_task(_task_dir(root, f"sandboxed-{nname}", [_TASK_NETWORKS[nname], _TASK_CPUS["two"]]))
        out[f"task/sandboxed/{nname}"] = _guarded(_solver_run_calls, task, mode="claude", sandbox=sandbox)
    out["sandbox/run_args"] = sandbox.run_args()
    tbench = load_task(_tb1(root / "tbench"))
    out["task/oracle/terminal-bench"] = _guarded(_solver_run_calls, tbench, mode="oracle")


def _fidelity_cases(out: dict[str, Any]) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(ok=True, returncode=0, timed_out=False, stdout="", stderr="")

    with mock.patch.object(fidelity.process, "run", fake_run):
        for plugin in (None, Path("/p/plugin")):
            for uid, gid in ((1000, 1000), (0, 0)):
                calls.clear()
                runner = fidelity.run_docker("img:1", "/host/claude", ["-p", "hi"], workdir=Path("/w/run"),
                                             plugin_dir=plugin, uid=uid, gid=gid)
                runner("http://127.0.0.1:9/")
                out[f"fidelity/plugin-{plugin is not None}/uid-{uid}"] = list(calls)


def capture(root: Path) -> dict[str, Any]:
    """Every docker-run argv of the grid, keyed by call site and input. ``root`` holds task dirs."""
    out: dict[str, Any] = {}
    _repo_cases(out)
    _example_cases(out)
    _task_cases(root, out)
    _fidelity_cases(out)
    return json.loads(json.dumps(out))  # plain JSON values, as the golden stores them


def test_every_docker_run_argv_is_byte_identical(tmp_path):
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = capture(tmp_path)
    assert sorted(got) == sorted(want)
    changed = sorted(k for k in want if got[k] != want[k])
    assert changed == [], f"argv changed for: {changed[:10]}"


def test_a_task_container_env_is_pinned_before_the_image(tmp_path):
    # Moving the -e flags after the image keeps the argv's words and starts a container that exits at
    # once; only the position pins that, so the grid must carry a case with container_env.
    env_argvs = [(key, argv) for key, calls in capture(tmp_path).items() if isinstance(calls, list)
                 for argv in calls if isinstance(argv, list) and "TEST_DIR=/tests" in argv]
    assert env_argvs, "no grid case carries container_env"
    for key, argv in env_argvs:
        image_at = argv.index("sleep") - 1
        assert argv.index("TEST_DIR=/tests") < image_at, key

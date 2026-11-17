# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Harbor task's prebuilt ``[environment] docker_image`` in the ``docker-*`` harnesses.

The contract is docs/coding-harnesses.md "Prebuilt images": the image Harbor 0.24 would start
(``should_use_prebuilt_docker_image``), pulled only when missing, the loose ``environment/``
files copied into the workdir (``should_upload_environment_dir``), and ``metrics.image``.
Docker is a :class:`DockerCLI` fake: the tests read the argv the harness would run.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from dokimi.harnesses import TaskSuiteRunner, load_task
from dokimi.harnesses.registry import create_harness
from dokimi.harnesses.tasks import FORMAT_SWE_BENCH, TaskSpec, _task_build_timeout
from dokimi.process import reply
from docker_cli_fake import DockerCLI

IMAGE = "python:3.12-slim"
RECORD = {"id": "sha256:5e2b", "repo_digests": ["python@sha256:5e2b"]}


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """No developer proxy or provider reaches the argv; the host `claude` is a path never run."""
    import dokimi.harnesses.container_agent as container_mod

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-operator-config"))
    for name in list(os.environ):
        if name.lower() in ("http_proxy", "https_proxy", "no_proxy", "all_proxy") or name.startswith(
                ("ANTHROPIC_", "CLAUDE_CODE_", "AWS_", "CLOUD_ML_", "GOOGLE_", "VERTEX_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(container_mod, "claude_binary", lambda: tmp_path / "claude")


def _task(tmp_path: Path, *, image: str | None = IMAGE, dockerfile: bool = False,
          env_files: tuple[str, ...] = (".keep",), workdir: str | None = "/app") -> Path:
    """A uxl answer-quality task (onetbb-bounded-image-flow-graph's shape at uxlfoundation/skills 6feb3f3)."""
    task = tmp_path / "answer-task"
    for d in ("environment", "tests", "solution"):
        (task / d).mkdir(parents=True)
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "solution" / "solve.sh").write_text("cp /solution/answer.md /app/answer.md\n", encoding="utf-8")
    (task / "instruction.md").write_text("Write /app/answer.md.", encoding="utf-8")
    for name in env_files:
        (task / "environment" / name).parent.mkdir(parents=True, exist_ok=True)
        (task / "environment" / name).write_text("", encoding="utf-8")
    if dockerfile:
        (task / "environment" / "Dockerfile").write_text("FROM ubuntu:24.04\n", encoding="utf-8")
    env = [f'docker_image = "{image}"'] if image else []
    env += [f'workdir = "{workdir}"'] if workdir else []
    (task / "task.toml").write_text(
        "[agent]\ntimeout_sec = 30\n[environment]\nbuild_timeout_sec = 77\n" + "\n".join(env) + "\n",
        encoding="utf-8")
    return task


def _daemon(*, has_image: bool = False, answer=None) -> DockerCLI:
    """A daemon that has *IMAGE* only after a pull (or from the start, with *has_image*)."""
    state = {"present": has_image}

    def handle(call):
        args = call.argv[1:]
        if answer is not None and (got := answer(call)) is not None:
            return got
        if args[:1] == ("pull",):
            state["present"] = True
        if args[:2] == ("image", "inspect") and args[-1] == IMAGE:
            if not state["present"]:
                return reply(stderr=f"Error: No such image: {IMAGE}", returncode=1)
            return reply(json.dumps(RECORD) + "\n")
        return None

    return DockerCLI(answer=handle)


def _run_argv(cli: DockerCLI) -> list[str]:
    return next(a for a in cli.args if a[0] == "run" and "sleep" in a)


@pytest.mark.parametrize("harness", ["docker-oracle", "docker-claude", "docker-probe"])
def test_a_docker_image_task_pulls_and_runs_that_image_without_a_build(tmp_path, harness):
    """docs/coding-harnesses.md: "a docker-* run starts from that image instead of building"."""
    task = load_task(_task(tmp_path))
    cli = _daemon()
    result = create_harness(harness, runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")

    assert not [a for a in cli.args if a[0] == "build"]
    assert ["pull", "-q", IMAGE] in cli.args
    run = _run_argv(cli)
    assert run[run.index("sleep") - 1] == IMAGE
    pull = next(c for c in cli.calls if c.argv[1] == "pull")
    assert pull.timeout == _task_build_timeout(task) == 77
    assert result.metrics["image"] == {"source": "docker_image", "ref": IMAGE, "pulled": True, **RECORD}


def test_an_image_the_daemon_has_is_not_pulled(tmp_path):
    """docs/coding-harnesses.md: "pulled only when the daemon does not have it", as compose up does."""
    task = load_task(_task(tmp_path))
    cli = _daemon(has_image=True)
    result = create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    assert not [a for a in cli.args if a[0] in ("pull", "build")]
    assert result.metrics["image"]["pulled"] is False
    assert result.metrics["image"]["repo_digests"] == RECORD["repo_digests"]


def test_the_loose_environment_files_land_in_the_workdir_before_the_solve(tmp_path):
    """docs/coding-harnesses.md: the files of environment/ are copied into the workdir after the start.

    python:3.12-slim has no /app; Harbor's copy of environment/.keep is what creates it."""
    path = _task(tmp_path, env_files=(".keep", "probe.py"))
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")

    copy = [a for a in cli.args if a[0] == "cp" and a[1] == f"{path / 'environment'}/."]
    assert len(copy) == 1 and copy[0][2].endswith(":/app")
    order = [a[0] if a[0] != "cp" else a[1] for a in cli.args]
    assert order.index("run") < order.index(f"{path / 'environment'}/.") < order.index(f"{path / 'solution'}/.")
    assert ["exec", "-u", "root", copy[0][2].split(":")[0], "mkdir", "-p", "/app"] in cli.args


def test_the_task_container_works_in_the_declared_workdir(tmp_path):
    """Harbor's DockerEnvironment.exec runs in environment.workdir, so a solve.sh or test.sh with a
    relative path (`cp seed.txt answer.txt`) finds the uploaded files; python:3.12-slim has no WORKDIR."""
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    run = _run_argv(cli)
    assert run[run.index("-w") + 1] == "/app" and run.index("-w") < run.index(IMAGE)


def test_without_a_declared_workdir_the_container_keeps_the_image_workdir(tmp_path):
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(_task(tmp_path, workdir=None)), output_dir=tmp_path / "out")
    assert "-w" not in _run_argv(cli)


def test_a_built_task_keeps_its_image_workdir_despite_a_declared_one(tmp_path):
    """Left as on main on purpose (unlike Harbor, which overrides WORKDIR with it): see the PR's Not in this PR."""
    path = _task(tmp_path, image=None, dockerfile=True)
    assert load_task(path).environment["workdir"] == "/app"
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")
    assert "-w" not in _run_argv(cli)


def test_without_a_declared_workdir_the_files_go_to_the_image_workdir_else_root(tmp_path):
    """Harbor uploads into environment.workdir, else the container's `pwd` (the image's WORKDIR, or /)."""
    path = _task(tmp_path, workdir=None)
    for workdir, dest in (("/srv/task\n", "/srv/task"), ("\n", "/")):
        cli = _daemon(answer=lambda call, w=workdir: reply(w) if "{{.Config.WorkingDir}}" in call.argv else None)
        create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")
        copy = next(a for a in cli.args if a[0] == "cp" and a[1] == f"{path / 'environment'}/.")
        assert copy[2].split(":", 1)[1] == dest


def test_a_failed_upload_still_removes_the_task_container(tmp_path):
    """The task container (`sleep` >= 1800 s) is removed whatever step fails after its start."""
    path = _task(tmp_path)
    cli = _daemon(answer=lambda call: reply(stderr="cp failed", returncode=1) if (
        call.argv[1] == "cp" and call.argv[2] == f"{path / 'environment'}/.") else None)
    with pytest.raises(RuntimeError, match=r"docker cp failed \(exit 1\)"):
        create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")
    run = next(i for i, a in enumerate(cli.args) if a[0] == "run")
    container = cli.args[run][cli.args[run].index("--name") + 1]
    assert ["rm", "-f", container] in cli.args[run + 1:]


def test_bundled_skills_are_not_copied_into_the_workdir(tmp_path):
    """environment/skills stays out of the container, as it stays out of a build context."""
    path = _task(tmp_path, env_files=("probe.py", "skills/s/SKILL.md"))
    seen: list[list[str]] = []

    def record(call):
        if call.argv[1] == "cp" and call.argv[-1].endswith(":/app"):
            src = Path(call.argv[2].rstrip("."))
            seen.append(sorted(str(p.relative_to(src)) for p in src.rglob("*")))
        return None

    create_harness("docker-oracle", runner=_daemon(answer=record)).run(load_task(path), output_dir=tmp_path / "out")
    assert seen == [["probe.py"]]


def test_an_empty_environment_dir_is_not_copied(tmp_path):
    """Harbor's should_upload_environment_dir: nothing to upload from an empty environment/."""
    path = _task(tmp_path, env_files=())
    assert load_task(path).environment_upload is None
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")
    assert not [a for a in cli.args if a[0] == "cp" and a[1] == f"{path / 'environment'}/."]


def test_a_dockerfile_only_task_still_builds_its_own_image(tmp_path):
    """docs/coding-harnesses.md: a task without docker_image builds dokimi/<task>:latest, as before."""
    path = _task(tmp_path, image=None, dockerfile=True, env_files=("src.cpp",))
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")

    builds = [a for a in cli.args if a[0] == "build"]
    env = path / "environment"
    assert builds == [["build", "-q", "--network", "host", "-t", "dokimi/answer-task:latest",
                       "-f", str(env / "Dockerfile"), str(env)]]
    assert not [a for a in cli.args if a[0] == "pull" or a[:2] == ["image", "inspect"]]
    assert not [a for a in cli.args if a[0] == "cp" and a[1] == f"{env}/."]
    run = _run_argv(cli)
    assert run[run.index("sleep") - 1] == "dokimi/answer-task:latest"


def test_a_built_image_is_recorded_as_built(tmp_path):
    """docs/coding-harnesses.md: `metrics.image` says which path ran."""
    path = _task(tmp_path, image=None, dockerfile=True)
    result = create_harness("docker-oracle", runner=_daemon()).run(load_task(path), output_dir=tmp_path / "out")
    assert result.metrics["image"] == {"source": "build", "ref": "dokimi/answer-task:latest"}


def test_with_both_the_docker_image_wins_and_nothing_is_uploaded(tmp_path):
    """Harbor 0.24 should_use_prebuilt_docker_image: docker_image wins unless --force-build;
    should_upload_environment_dir: no upload beside a Dockerfile."""
    path = _task(tmp_path, dockerfile=True, env_files=("src.cpp",))
    cli = _daemon()
    create_harness("docker-oracle", runner=cli).run(load_task(path), output_dir=tmp_path / "out")
    assert not [a for a in cli.args if a[0] == "build"]
    assert ["pull", "-q", IMAGE] in cli.args
    assert not [a for a in cli.args if a[0] == "cp" and a[1] == f"{path / 'environment'}/."]
    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert f"Dockerfile not built: [environment] docker_image {IMAGE} wins" in log


def test_a_docker_image_only_task_says_nothing_about_a_dockerfile(tmp_path):
    create_harness("docker-oracle", runner=_daemon()).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert "not built" not in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")


def test_only_a_harbor_task_reads_docker_image(tmp_path):
    """A staged SWE-bench task's Dockerfile starts FROM its docker_image: it is built, not pulled."""
    spec = TaskSpec(name="t", path=tmp_path, instruction="", environment={"docker_image": IMAGE},
                    format=FORMAT_SWE_BENCH)
    assert spec.docker_image is None
    assert replace(spec, format="harbor").docker_image == IMAGE


def test_the_pull_happens_under_network_build_none(tmp_path):
    """docs/coding-harnesses.md: the pull is the daemon's, like a build's FROM; build=none does not stop it."""
    harness = replace(create_harness("docker-oracle", runner=_daemon()), network=("build=none",))
    result = harness.run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert ["pull", "-q", IMAGE] in harness.runner.args
    assert result.metrics["network"]["build"]["mode"] == "none"
    assert result.metrics["image"]["pulled"] is True


def test_a_pull_that_runs_out_of_time_errors_the_cell(tmp_path):
    """Like a build timeout: no agent ran, so there is no verdict (docs/coding-harnesses.md)."""
    task = load_task(_task(tmp_path))
    cli = _daemon(answer=lambda call: reply(timed_out=True) if call.argv[1] == "pull" else None)
    with pytest.raises(RuntimeError, match="docker pull timed out after 77"):
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    assert "PULL TIMEOUT" in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")

    row = TaskSuiteRunner(tasks=[task], harnesses=["docker-oracle"], output_root=tmp_path / "runs",
                          runner=cli).run()["results"][0]
    assert row["reward"] is None and "docker pull timed out" in row["metrics"]["error"]


def test_a_failed_pull_errors_the_cell_before_any_container(tmp_path):
    """docs/coding-harnesses.md "Prebuilt images": a pull fails the cell like a build, before any run."""
    task = load_task(_task(tmp_path))
    cli = _daemon(answer=lambda call: reply(stderr="manifest unknown", returncode=1)
                  if call.argv[1] == "pull" else None)
    with pytest.raises(RuntimeError, match=r"docker pull failed \(exit 1\)"):
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    assert not [a for a in cli.args if a[0] == "run"]


@pytest.mark.parametrize("value", ["5", "true", '["python:3.12-slim"]'])
def test_a_docker_image_that_is_not_a_string_is_refused_at_load(tmp_path, value):
    """docs/coding-harnesses.md: refused as Harbor's EnvironmentConfig (docker_image: str | None) refuses it."""
    path = _task(tmp_path, image=None, dockerfile=True)
    (path / "task.toml").write_text(f"[environment]\ndocker_image = {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="docker_image must be a string"):
        load_task(path)


@pytest.mark.parametrize("image", ["--privileged", "python:3.12 -slim"])
def test_a_docker_image_that_is_not_a_reference_is_refused(tmp_path, image):
    """distribution/reference: a reference starts with a letter or digit and has no space;
    "--privileged" would reach docker as a flag."""
    task = load_task(_task(tmp_path, image=image))
    cli = _daemon()
    with pytest.raises(ValueError, match="is not a docker image reference"):
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / "out")
    assert not [a for a in cli.args if image in a]

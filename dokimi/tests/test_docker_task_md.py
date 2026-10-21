# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``docker-*`` harnesses on a BenchFlow ``task.md`` task.

Docker is never invoked: the harness gets a :class:`DockerCLI` runner that
records every call, so the tests cover which paths the oracle and verifier are
mounted at, what env reaches them, the build context and the workdir.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from dokimi.harnesses import load_task
from dokimi.harnesses.docker_solver import (
    DockerSolverHarness,
    _exec_env,
    _task_workdir,
    unresolved_env,
)
from dokimi.process import reply
from docker_cli_fake import DockerCLI

_TASK_MD = """---
schema_version: '1.3'
metadata:
  author_name: Someone
  difficulty: medium
  category: software-engineering
  tags:
  - parallel
  - onetbb
verifier:
  type: test-script
  timeout_sec: 900.0
  env:
    LITERAL: "1"
    SECRET: ${HOST_SECRET}
agent:
  timeout_sec: 1800.0
sandbox:
  network_mode: public
  cpus: 8
  memory_mb: 4096
  gpus: 0
oracle:
  env:
    MODE: oracle
---

# Parallel TF-IDF Similarity Search

In `/root/workspace/`, parallelize the engine.
"""


def _task_md(root: Path, name: str = "sb-task", text: str = _TASK_MD) -> Path:
    task = root / name
    for sub in ("environment/skills/python-parallelization", "oracle", "verifier"):
        (task / sub).mkdir(parents=True, exist_ok=True)
    (task / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (task / "environment" / "skills" / "python-parallelization" / "SKILL.md").write_text(
        "---\nname: python-parallelization\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (task / "oracle" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "verifier" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "task.md").write_text(text, encoding="utf-8")
    return task


def _harbor(root: Path, name: str = "hb-task") -> Path:
    task = root / name
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True)
    (task / "instruction.md").write_text("Fix it.", encoding="utf-8")
    (task / "task.toml").write_text(
        '[metadata]\ntags = ["onedal"]\n\n[environment]\nworkdir = "/app"\n', encoding="utf-8"
    )
    return task


# ── docker solver on a task.md task ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """No developer provider or Claude settings reach a launch (a test sets its own
    proxy); the host `claude` an in-container agent copies is a path, never run."""
    import dokimi.harnesses.container_agent as container_mod

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-operator-config"))
    for name in list(os.environ):
        if name.lower() == "all_proxy" or name.startswith(
                ("ANTHROPIC_", "CLAUDE_CODE_", "AWS_", "CLOUD_ML_", "GOOGLE_", "VERTEX_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(container_mod, "claude_binary", lambda: tmp_path / "claude")


def _capture(stdout="") -> DockerCLI:
    """A docker CLI where every call exits 0 and prints ``stdout`` (no reward file)."""
    return DockerCLI(answer=lambda call: reply(stdout))


def _docker_via(cli: DockerCLI):
    """The docker callable a harness hands its helpers, on ``cli``."""
    return DockerSolverHarness(harness_id="h", runner=cli)._docker


def _build_context(task, inspect) -> None:
    """Run ``_build_image``; ``inspect(context)`` looks at the context while `docker build` runs."""
    def answer(call):
        if call.argv[1] == "build":
            inspect(Path(call.argv[-1]))
        return None

    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=DockerCLI(answer=answer))._build_image(
        task, "img", io.StringIO())


def test_oracle_is_mounted_at_both_paths_from_oracle_dir(tmp_path, monkeypatch):
    task = load_task(_task_md(tmp_path))
    cli = _capture()
    calls = cli.args
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)._solve_oracle(task, "c1", log=io.StringIO())
    cps = [c for c in calls if c[0] == "cp"]
    assert cps == [
        ["cp", f"{task.path / 'oracle'}/.", "c1:/solution"],
        ["cp", f"{task.path / 'oracle'}/.", "c1:/oracle"],
    ]
    execs = [c for c in calls if c[0] == "exec"]
    assert execs[0] == ["exec", "-u", "root", "c1", "mkdir", "-p", "/solution", "/oracle"]
    assert "MODE=oracle" in execs[-1]


def test_verifier_is_mounted_at_both_paths(tmp_path, monkeypatch):
    """A SkillsBench test.sh runs `pytest /verifier/...` even after export."""
    task = load_task(_task_md(tmp_path))
    cli = _capture()
    calls = cli.args
    out = tmp_path / "out"
    out.mkdir()
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)._verify(task, "c1", out, log=io.StringIO())
    cps = [c for c in calls if c[0] == "cp" and c[2].startswith("c1:")]
    assert [c[2] for c in cps] == ["c1:/tests", "c1:/verifier"]
    assert all(c[1] == f"{task.path / 'verifier'}/." for c in cps)
    # into, not nested under, a /tests the image already has
    assert calls[0][-4:] == ["mkdir", "-p", "/tests", "/verifier"]


def _clear_proxy(monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)


def _oracle_run(tmp_path, monkeypatch, text):
    """A whole docker-oracle ``run()`` with ``_docker`` recorded."""
    task = load_task(_task_md(tmp_path, text=text))
    cli = _capture()
    calls = cli.args
    res = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        task, output_dir=tmp_path / "out")
    solve = next(c for c in calls if c[0] == "exec" and c[-1].endswith("/solution/solve.sh"))
    verify = next(c for c in calls if c[0] == "exec" and c[-1].endswith("/tests/test.sh"))
    return res, solve, verify


def test_run_gives_the_verifier_the_proxy_on_a_public_task_without_env_table(tmp_path, monkeypatch):
    """The fix this PR measured: most public tasks have no env table at all."""
    _clear_proxy(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://proxy.example:912/")
    text = _TASK_MD.replace('  env:\n    LITERAL: "1"\n    SECRET: ${HOST_SECRET}\n', "").replace(
        "oracle:\n  env:\n    MODE: oracle\n", "")
    res, solve, verify = _oracle_run(tmp_path, monkeypatch, text)
    assert "error" not in res.metrics
    assert "https_proxy=http://proxy.example:912/" in verify
    assert "https_proxy=http://proxy.example:912/" in solve


def test_run_gives_each_step_its_own_env_table(tmp_path, monkeypatch):
    _clear_proxy(monkeypatch)
    text = _TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "")
    res, solve, verify = _oracle_run(tmp_path, monkeypatch, text)
    assert "error" not in res.metrics
    assert "LITERAL=1" in verify and "MODE=oracle" not in verify
    assert "MODE=oracle" in solve and "LITERAL=1" not in solve


def test_docker_log_line_is_redacted(tmp_path, monkeypatch):
    """What reaches run.log (and so stdout_tail) through the real ``_docker``."""
    import dokimi.harnesses.docker_solver as mod
    from dokimi.process import FakeRunner

    fake = FakeRunner(lambda call: reply("ok\n"))
    log = tmp_path / "run.log"
    with log.open("w", encoding="utf-8") as fh:
        mod._docker(["exec", "-e", "http_proxy=u:secret@proxy:912",
                     "-e", "https_proxy=http://user:p@ss@proxy:912", "c1", "true"], fh, runner=fake)
    seen = [list(c.argv) for c in fake.calls]
    text = log.read_text(encoding="utf-8")
    assert "secret" not in text and "p@ss" not in text and "ss@" not in text
    assert text.startswith("$ docker exec -e 'http_proxy=***@proxy:912' -e ")
    assert "ok\n" in text
    # the real argv still carries the credentials; only the log is redacted
    assert "http_proxy=u:secret@proxy:912" in seen[0]


def test_exec_env_forwards_literals_not_host_references(tmp_path, monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    task = load_task(_task_md(tmp_path))
    assert _exec_env(task, "verifier") == ["-e", "LITERAL=1"]
    assert _exec_env(load_task(_harbor(tmp_path)), "verifier") == []


def test_public_task_steps_get_proxy_env(tmp_path, monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("https_proxy", "http://proxy.example:912/")
    task = load_task(_task_md(tmp_path))
    proxy = ["-e", "https_proxy=http://proxy.example:912/"]
    if os.name == "nt":
        # os.environ upper-cases names on Windows, where the lower-case variable
        # then appears as HTTPS_PROXY too: compare the pairs, not the exact list.
        verifier = [a for a in _exec_env(task, "verifier") if a != "-e"]
        assert proxy[1].lower() in [a.lower() for a in verifier] and verifier[-1] == "LITERAL=1"
        assert proxy[1].lower() in [a.lower() for a in _exec_env(task, "oracle")]
        return
    assert _exec_env(task, "verifier") == [*proxy, "-e", "LITERAL=1"]
    assert _exec_env(task, "oracle") == [*proxy, "-e", "MODE=oracle"]


def test_offline_task_steps_get_no_proxy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy.example:912/")
    assert _exec_env(load_task(_harbor(tmp_path)), "verifier") == []


def test_container_start_carries_no_proxy_env(tmp_path, monkeypatch):
    """The proxy is scoped to the oracle/verifier exec, not the container."""
    task = load_task(_task_md(tmp_path))
    monkeypatch.setenv("https_proxy", "http://proxy.example:912/")
    cli = _capture()
    calls = cli.args
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)._start_container(
        task, "img", "c1", log=io.StringIO()
    )
    run = next(c for c in calls if c[0] == "run")
    assert not any("proxy" in arg for arg in run)


def test_build_context_drops_bundled_skills(tmp_path, monkeypatch):
    """A `COPY . /x` must not bake task skills into the no-skill image."""
    task = load_task(_task_md(tmp_path))
    seen: dict[str, list[str]] = {}

    def inspect(context):
        seen["entries"] = sorted(p.name for p in context.iterdir())
        seen["skills_empty"] = not any((context / "skills").iterdir())
        seen["context"] = [str(context)]

    _build_context(task, inspect)
    assert seen["entries"] == ["Dockerfile", "skills"]
    assert seen["skills_empty"] is True
    assert seen["context"][0] != str(task.path / "environment")
    # The task tree itself is untouched.
    assert (task.path / "environment" / "skills").is_dir()


def test_build_context_is_the_task_dir_without_skills(tmp_path, monkeypatch):
    task = load_task(_harbor(tmp_path))
    cli = _capture()
    calls = cli.args
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)._build_image(task, "img", log=io.StringIO())
    build = next(c for c in calls if c[0] == "build")
    assert build[-1] == str(task.path / "environment")


def test_workdir_prefers_declared_then_image_then_app(tmp_path, monkeypatch):
    log = io.StringIO()
    assert _task_workdir(load_task(_harbor(tmp_path)), "c1", log, _docker_via(DockerCLI())) == "/app"
    sb = load_task(_task_md(tmp_path))
    assert _task_workdir(sb, "c1", log, _docker_via(_capture(stdout="/root\n"))) == "/root"
    assert _task_workdir(sb, "c1", log, _docker_via(_capture(stdout="/\n"))) == "/app"


def test_unresolved_env_lists_host_references(tmp_path):
    task = load_task(_task_md(tmp_path))
    assert unresolved_env(task, "verifier") == ["SECRET"]
    assert unresolved_env(task, "oracle") == []


def _run_with_reward(tmp_path, monkeypatch, mode, reward):
    """A run whose verifier would score ``reward``; no docker step may start."""
    task = load_task(_task_md(tmp_path))
    cli = DockerCLI(reward=str(reward))
    res = DockerSolverHarness(harness_id=f"docker-{mode}", mode=mode, runner=cli).run(
        task, output_dir=tmp_path / "out")
    assert cli.calls == []  # decided from the task file, before the build
    return res


def test_failed_cell_with_unforwarded_verifier_env_is_missing_data(tmp_path, monkeypatch):
    res = _run_with_reward(tmp_path, monkeypatch, "oracle", 0.0)
    assert res.reward is None and not res.passed
    assert res.metrics["unresolved_env"] == {"verifier": ["SECRET"]}
    assert res.metrics["error"] == "task needs host env not forwarded: verifier.env SECRET"


def test_passing_cell_with_unforwarded_env_is_missing_data_too(tmp_path, monkeypatch):
    """Keeping passes but dropping misses would make the task look solved."""
    res = _run_with_reward(tmp_path, monkeypatch, "oracle", 1.0)
    assert res.reward is None and not res.passed
    assert "error" in res.metrics


def test_unresolved_env_errors_before_the_build_and_in_dry_run(tmp_path, monkeypatch):
    task = load_task(_task_md(tmp_path))
    cli = _capture()
    calls = cli.args
    h = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli)
    res = h.run(task, output_dir=tmp_path / "out")
    assert res.reward is None and res.metrics["unresolved_env"] == {"verifier": ["SECRET"]}
    assert calls == []  # no build, no container, no solve
    dry = h.run(task, output_dir=tmp_path / "out", dry_run=True)
    assert dry.metrics["dry_run"] and "error" in dry.metrics


def test_oracle_only_host_reference_errors_the_oracle_cell(tmp_path, monkeypatch):
    """Four SkillsBench tasks reference host secrets in oracle.env only."""
    text = _TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "").replace(
        "    MODE: oracle\n", "    MODE: oracle\n    KEY: ${OPENAI_API_KEY}\n")
    task = load_task(_task_md(tmp_path, text=text))
    cli = _capture()
    calls = cli.args
    res = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        task, output_dir=tmp_path / "out")
    assert res.metrics["unresolved_env"] == {"oracle": ["KEY"]} and res.reward is None
    assert res.metrics["error"] == "task needs host env not forwarded: oracle.env KEY"
    assert calls == []


def test_exec_env_yaml_bool_and_non_scalars(tmp_path, monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    text = _TASK_MD.replace('    LITERAL: "1"\n', '    LITERAL: "1"\n    FLAG: true\n    LIST: [a, b]\n    DFLT: ${K:-x}\n')
    task = load_task(_task_md(tmp_path, text=text))
    assert _exec_env(task, "verifier") == ["-e", "LITERAL=1", "-e", "FLAG=true"]
    assert unresolved_env(task, "verifier") == ["DFLT", "LIST", "SECRET"]


def test_declared_root_workdir_is_refused(tmp_path, monkeypatch):
    for i, spelled in enumerate(("/", "/.", "/app/..", "//")):
        text = _TASK_MD.replace("  network_mode: public\n", f"  network_mode: public\n  workdir: {spelled}\n")
        task = load_task(_task_md(tmp_path, name=f"t{i}", text=text))
        assert _task_workdir(task, "c1", io.StringIO(), _docker_via(_capture(stdout="/\n"))) == "/app", spelled


def _harbor_with(root: Path, toml_env: str, instruction: str = "Fix it.") -> Path:
    task = root / "exported"
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True, exist_ok=True)
    (task / "instruction.md").write_text(instruction, encoding="utf-8")
    (task / "task.toml").write_text(f"[metadata]\n\n[environment]\n{toml_env}", encoding="utf-8")
    return task


def _host_cli(image_workdir: str, answer=None) -> DockerCLI:
    """Docker with an image working in ``image_workdir``; the host claude reports a cost."""
    def respond(call):
        got = answer(call) if answer else None
        if got is not None:
            return got
        return reply(image_workdir) if call.argv[0] == "docker" else None

    return DockerCLI(answer=respond, solver='{"total_cost_usd": 0.42}')


def _launched(cli: DockerCLI) -> list[list[str]]:
    return [list(c.argv) for c in cli.calls if c.argv[0] != "docker"]


def _host_run(tmp_path, monkeypatch, task, image_workdir):
    """A whole host-mode ``run()``: the docker calls (after ``docker``), the launches, a refusal."""
    cli = _host_cli(image_workdir)
    error = None
    try:
        DockerSolverHarness(harness_id="docker-claude-host", mode="claude", agent_location="host", runner=cli).run(
            task, model="haiku", output_dir=tmp_path / "out")
    except ValueError as exc:
        error = str(exc)
    return cli.args, _launched(cli), error


def test_host_mode_refuses_an_exported_task_whose_image_works_in_root(tmp_path, monkeypatch):
    """`bench tasks export --target harbor` gives a task.toml task with WORKDIR /root.

    A /root snapshot lets the copy-back rewrite /root/.profile, which the root
    verifier's `bash -lc` sources: a reward written without doing the task.
    """
    task = load_task(_harbor_with(tmp_path, 'network_mode = "public"\n'))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, "/root\n")
    assert error and "/root" in error
    assert not any(Path(c[0]).name == "claude" for c in launched)
    assert not any(c[0] == "cp" and ":/root" in " ".join(c) for c in calls)


def test_host_mode_refuses_a_declared_non_app_workdir_before_the_build(tmp_path, monkeypatch):
    task = load_task(_harbor_with(tmp_path, 'workdir = "/root"\n'))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, "/app\n")
    assert error and "environment.workdir" in error
    assert calls == [] and launched == []


def test_host_mode_snapshots_app_and_recompiles_app_lines(tmp_path, monkeypatch):
    task = load_task(_harbor_with(
        tmp_path, "", "Build with `g++ -O2 /app/bench.cpp -o /app/bench` and run it."))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, "/app\n")
    assert error is None
    assert any(Path(c[0]).name == "claude" for c in launched)
    cps = [c for c in calls if c[0] == "cp"]
    assert cps[0][1].endswith(":/app/.") and cps[1][2].endswith(":/app/")
    assert any(c[-1] == "g++ -O2 /app/bench.cpp -o /app/bench" for c in calls if c[0] == "exec")


def test_build_copies_symlinks_as_links(tmp_path, monkeypatch):
    task = load_task(_task_md(tmp_path))
    env = task.path / "environment"
    (env / "dangling").symlink_to(tmp_path / "missing")
    (env / "hostfile").symlink_to("/etc/hostname")
    seen: dict = {}
    _build_context(task, lambda ctx: seen.update(links=sorted(p.name for p in ctx.iterdir() if p.is_symlink())))
    assert seen["links"] == ["dangling", "hostfile"]


def _credentialed_proxy(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("https_proxy", "http://u:pa=s3cret@proxy.example:912/")
    monkeypatch.setenv("http_proxy", "u:s3cret@proxy.example:912")  # scheme-less


def test_exec_env_can_drop_proxy_credentials(tmp_path, monkeypatch):
    _credentialed_proxy(monkeypatch)
    task = load_task(_task_md(tmp_path))
    kept = " ".join(_exec_env(task, "verifier"))
    dropped_args = _exec_env(task, "verifier", credentials=False)
    dropped = " ".join(dropped_args)
    assert "s3cret" in kept and "s3cret" not in dropped
    assert {"https_proxy=http://proxy.example:912/", "http_proxy=proxy.example:912"} <= set(dropped_args)


@pytest.mark.parametrize("mode, location, credentials", [
    ("claude", "container", False),  # the verifier runs the agent's /app code; the sidecar kept them
    ("claude", "host", True),        # that agent ran with the operator's env anyway
    ("oracle", "container", True),   # no agent code at all
])
def test_verifier_gets_proxy_credentials_only_without_in_container_agent_code(
        tmp_path, monkeypatch, mode, location, credentials):
    _credentialed_proxy(monkeypatch)
    task = load_task(_no_secret_task_md(tmp_path))
    cli = _capture()
    calls = cli.args
    DockerSolverHarness(harness_id="h", mode=mode, agent_location=location, runner=cli)._verify(
        task, "c1", tmp_path, log=io.StringIO())
    verify = next(c for c in calls if c[0] == "exec" and c[-1].endswith("/tests/test.sh"))
    assert ("s3cret" in " ".join(verify)) is credentials
    assert any(a.startswith("https_proxy=") for a in verify)


def test_docker_output_in_run_log_is_redacted(monkeypatch):
    """A verifier (or a build step) that prints the proxy it was given."""
    import dokimi.harnesses.docker_solver as mod
    from dokimi.process import FakeRunner

    fake = FakeRunner(lambda call: reply("LEAK https_proxy=http://u:s3cret@h:912\n",
                                         "curl: proxy http://u:s3cret@h:912 refused\n"))
    log = io.StringIO()
    proc = mod._docker(["exec", "c1", "true"], log, runner=fake)
    assert "s3cret" not in log.getvalue() and "http://***@h:912" in log.getvalue()
    assert "s3cret" in proc.stdout  # the caller still gets the real output


def test_run_log_redacts_proxy_credentials():
    from dokimi.harnesses.docker_solver import _redact

    assert _redact("https_proxy=http://user:pw@proxy:912/") == "https_proxy=http://***@proxy:912/"
    assert _redact("https_proxy=http://proxy:912/") == "https_proxy=http://proxy:912/"
    assert _redact("http_proxy=u:secret@proxy:912") == "http_proxy=***@proxy:912"
    assert _redact("https_proxy=http://user:p@ss@proxy:912") == "https_proxy=http://***@proxy:912"
    assert _redact("no_proxy=localhost,.intel.com") == "no_proxy=localhost,.intel.com"
    assert _redact("https://pypi.org/simple/") == "https://pypi.org/simple/"
    # "=" is legal in userinfo; base64 tokens end in "=="
    assert _redact("http_proxy=http://user:dXNlcjpwYXNz==@proxy:912") == "http_proxy=http://***@proxy:912"
    assert _redact("http_proxy=http://u:pa=secret@proxy:912") == "http_proxy=http://***@proxy:912"
    assert _redact("https_proxy=https://u:s3cret@proxy:912/") == "https_proxy=https://***@proxy:912/"


def test_host_mode_does_not_launch_claude_on_a_task_md_task(tmp_path, monkeypatch):
    """Its instruction names container paths; a host agent would write there on the host."""
    # No ${VAR} in the fixture: the unresolved-env row would come first and
    # hide whether the task.md refusal fires.
    task = load_task(_task_md(tmp_path, text=_TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "")))
    cli = _capture()
    calls = cli.args
    with pytest.raises(ValueError, match="task.md"):
        DockerSolverHarness(harness_id="docker-claude-host", mode="claude", agent_location="host", runner=cli).run(
            task, model="haiku", output_dir=tmp_path / "out"
        )
    assert cli.calls == []  # refused from the task file: no build, no claude
    assert not any("claude" in " ".join(c) for c in calls)


def test_oracle_mode_still_runs_a_task_md_task(tmp_path, monkeypatch):
    task = load_task(_task_md(tmp_path, text=_TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "")))
    cli = _capture()
    calls = cli.args
    result = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        task, output_dir=tmp_path / "out"
    )
    assert result.metrics["mode"] == "oracle" and "error" not in result.metrics
    assert [c[0] for c in calls][:2] == ["build", "rm"]
    assert any(c[0] == "exec" and c[-1].endswith("/solution/solve.sh") for c in calls)


# ── review round 2026-10-12 ───────────────────────────────────────────────────


def test_timeout_lines_in_run_log_are_redacted(tmp_path, monkeypatch):
    """`str(TimeoutExpired)` holds the raw argv, proxy credentials included."""
    _clear_proxy(monkeypatch)
    monkeypatch.setenv("http_proxy", "http://u:s3cret@proxy.example:912")
    text = _TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "")
    task = load_task(_task_md(tmp_path, text=text))
    cli = DockerCLI(answer=lambda call: reply(timed_out=True) if (
        call.argv[1] == "exec" and call.argv[-1].endswith("/tests/test.sh")) else None)
    res = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        task, output_dir=tmp_path / "out")
    assert res.returncode == 124 and res.reward == 0.0
    assert "TIMEOUT: after 900.0s: docker exec" in res.stdout_tail
    assert "http_proxy=http://***@proxy.example:912" in res.stdout_tail
    assert "s3cret" not in res.stdout_tail
    assert "s3cret" not in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")


def test_build_timeout_line_in_run_log_is_redacted(tmp_path, monkeypatch):
    _clear_proxy(monkeypatch)
    monkeypatch.setenv("http_proxy", "http://u:s3cret@proxy.example:912")
    task = load_task(_harbor(tmp_path))
    hung = DockerCLI(answer=lambda call: reply(timed_out=True))
    with pytest.raises(RuntimeError, match="timed out"):
        DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=hung).run(
            task, output_dir=tmp_path / "out")
    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert "BUILD TIMEOUT: after 600.0s: docker build" in log
    assert "s3cret" not in log and "http_proxy=http://***@proxy.example:912" in log


def test_container_outlives_solve_and_verify_timeouts(tmp_path, monkeypatch):
    """A verifier with timeout_sec >= 1800 must not lose its container mid-run."""
    text = _TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "").replace(
        "  timeout_sec: 900.0\n", "  timeout_sec: 2700.0\n")
    task = load_task(_task_md(tmp_path, text=text))
    cli = _capture()
    calls = cli.args
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        task, output_dir=tmp_path / "out")
    run = next(c for c in calls if c[0] == "run")
    # max(1800, agent.timeout_sec 1800 + verifier 2700 + 600)
    assert run[-2:] == ["sleep", "5100"]
    cli = _capture()
    calls = cli.args
    DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        load_task(_harbor(tmp_path, name="short")), output_dir=tmp_path / "out2")
    assert next(c for c in calls if c[0] == "run")[-2:] == ["sleep", "1800"]


def test_exec_env_forwards_numbers_and_skips_proxy_off_public(tmp_path, monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy.example:912/")
    text = _TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "    N: 3\n    F: 1.5\n").replace(
        "  network_mode: public\n", "  network_mode: no-network\n")
    task = load_task(_task_md(tmp_path, text=text))
    assert _exec_env(task, "verifier") == ["-e", "LITERAL=1", "-e", "N=3", "-e", "F=1.5"]


def _harbor_env(root: Path, tables: str) -> Path:
    task = _harbor_with(root, "")
    toml = (task / "task.toml").read_text(encoding="utf-8")
    (task / "task.toml").write_text(toml + "\n" + tables, encoding="utf-8")
    return task


def _host_result(tmp_path, monkeypatch, task, image_workdir):
    """A whole host-mode ``run()``: the result, the docker calls, the launches."""
    cli = _host_cli(image_workdir)
    res = DockerSolverHarness(harness_id="docker-claude-host", mode="claude", agent_location="host", runner=cli).run(
        task, model="haiku", output_dir=tmp_path / "out")
    return res, cli.args, _launched(cli)


def test_claude_mode_errors_on_unresolved_verifier_env_before_any_docker_call(
        tmp_path, monkeypatch):
    task = load_task(_harbor_env(tmp_path, '[verifier.env]\nKEY = "${KEY}"\n'))
    res, calls, launched = _host_result(tmp_path, monkeypatch, task, "/app\n")
    assert res.metrics["unresolved_env"] == {"verifier": ["KEY"]} and res.reward is None
    assert calls == [] and launched == []


def test_claude_mode_ignores_oracle_env(tmp_path, monkeypatch):
    """An export keeps its [oracle.env]; the docker-claude cell never runs the oracle."""
    task = load_task(_harbor_env(tmp_path, '[oracle.env]\nKEY = "${KEY}"\n'))
    res, calls, launched = _host_result(tmp_path, monkeypatch, task, "/app\n")
    assert "error" not in res.metrics
    assert any(Path(c[0]).name == "claude" for c in launched)


@pytest.mark.parametrize("image_workdir", ["\n", "work\n", "/\n", "/app/workspace\n"])
def test_host_mode_runs_in_app_when_the_image_workdir_is_app_or_none(
        tmp_path, monkeypatch, image_workdir):
    """No absolute WORKDIR falls back to /app, and a WORKDIR inside /app is in
    the snapshot: both ran on main and still do."""
    task = load_task(_harbor_with(tmp_path, ""))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, image_workdir)
    assert error is None
    assert any(Path(c[0]).name == "claude" for c in launched)
    assert [c for c in calls if c[0] == "cp"][0][1].endswith(":/app/.")


def test_host_mode_runs_in_app_when_the_image_inspect_fails(tmp_path, monkeypatch):
    task = load_task(_harbor_with(tmp_path, ""))
    cli = _host_cli("/root\n", answer=lambda call: reply("/root\n", returncode=1) if (
        call.argv[1] == "inspect") else None)
    res = DockerSolverHarness(harness_id="docker-claude-host", mode="claude", agent_location="host", runner=cli).run(
        task, model="haiku", output_dir=tmp_path / "out2")
    assert "error" not in res.metrics


@pytest.mark.parametrize("declared", ["/app/", "/app/workspace", "/app/./x/.."])
def test_host_mode_accepts_a_declared_workdir_inside_app(tmp_path, monkeypatch, declared):
    task = load_task(_harbor_with(tmp_path, f'workdir = "{declared}"\n'))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, "/app\n")
    assert error is None and any(Path(c[0]).name == "claude" for c in launched)


def test_host_mode_refuses_an_image_workdir_next_to_app(tmp_path, monkeypatch):
    task = load_task(_harbor_with(tmp_path, ""))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, "/application\n")
    assert error and "/application" in error and launched == []


def test_oracle_mode_runs_a_declared_root_workdir(tmp_path, monkeypatch):
    task = load_task(_harbor_with(tmp_path, 'workdir = "/root"\n'))
    cli = _capture()
    calls = cli.args
    res = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=cli).run(
        task, output_dir=tmp_path / "out")
    assert "error" not in res.metrics
    assert any(c[0] == "exec" and c[-1].endswith("/solution/solve.sh") for c in calls)


def _no_secret_task_md(tmp_path) -> Path:
    return _task_md(tmp_path, text=_TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", ""))


def _container_run(tmp_path, monkeypatch, task, image_workdir, mode="claude", workdir_rc=0):
    """A whole in-container ``run()`` up to the chown that hands the agent its tree, which
    fails as a sentinel. Returns the result, the docker calls and the chowns made."""
    def answer(call):
        args = call.argv[1:]
        if args[0] == "exec" and args[-3:-1] == ("test", "-d"):
            return reply(returncode=workdir_rc)
        if args[0] == "exec" and "chown -R" in args[-1] and "mkdir -p" in args[-1]:
            return reply(stderr="sentinel: reached the chown", returncode=1)
        if args[0] == "inspect":
            return reply(image_workdir)
        return None

    cli = DockerCLI(answer=answer)
    res = DockerSolverHarness(harness_id=f"docker-{mode}", mode=mode, runner=cli).run(
        task, model="haiku", output_dir=tmp_path / "out")
    chowns = [c[-1] for c in cli.args if c[0] == "exec" and "chown -R" in c[-1] and "mkdir -p" in c[-1]]
    return res, cli.args, chowns


@pytest.mark.parametrize("mode", ["claude", "probe"])
def test_in_container_agent_runs_a_task_md_task_that_works_in_app(tmp_path, monkeypatch, mode):
    """The task.md refusal is the host agent's; in the container a task in /app runs."""
    task = load_task(_no_secret_task_md(tmp_path))
    res, calls, chowns = _container_run(tmp_path, monkeypatch, task, "/app\n", mode=mode)
    assert chowns and "docker exec failed" in res.metrics["error"]
    assert "sentinel: reached the chown" in res.stdout_tail


@pytest.mark.parametrize("mode", ["claude", "probe"])
@pytest.mark.parametrize("fixture", ["task_md", "export"])
def test_in_container_agent_refuses_a_task_that_works_in_root(tmp_path, monkeypatch, mode, fixture):
    """The agent is chowned /app and starts there: in a /root task it reaches
    nothing (a paid verified 0), or the chown fails after the build."""
    task = load_task(_no_secret_task_md(tmp_path) if fixture == "task_md"
                     else _harbor_with(tmp_path, ""))
    res, calls, chowns = _container_run(tmp_path, monkeypatch, task, "/root\n", mode=mode)
    assert not chowns and res.reward is None
    assert "works in /root" in res.metrics["error"]


def test_in_container_agent_refuses_a_declared_root_workdir_before_the_build(tmp_path, monkeypatch):
    task = load_task(_harbor_with(tmp_path, 'workdir = "/root"\n'))
    cli = _capture()
    calls = cli.args
    with pytest.raises(ValueError, match="environment.workdir"):
        DockerSolverHarness(harness_id="docker-claude", mode="claude", runner=cli).run(
            task, model="haiku", output_dir=tmp_path / "out", dry_run=True)
    assert calls == []


def test_in_container_agent_works_in_the_image_workdir_inside_app(tmp_path, monkeypatch):
    task = load_task(_harbor_with(tmp_path, ""))
    res, calls, chowns = _container_run(tmp_path, monkeypatch, task, "/app/workspace\n")
    assert chowns and chowns[0].endswith("chown -R 10001:10001 /app/workspace")


def test_container_sandbox_hands_over_only_its_work_tree(tmp_path):
    """lean4-proof keeps a root-owned, read-only /app/baseline beside /app/workspace:
    a chown of all of /app gives the agent the baseline its header is checked against."""
    from dokimi.harnesses.container_agent import ContainerSandbox

    calls: list[list[str]] = []
    sandbox = ContainerSandbox(network="n", proxy_name="p", allow=[], binary=None,
                               workdir="/app/workspace", _scratch=tmp_path)
    sandbox.prepare("c1", lambda args, log, **kw: calls.append(list(args)) or reply(), None)
    chown = calls[0][-1]
    assert chown.endswith("chown -R 10001:10001 /app/workspace")
    argv, _ = sandbox.exec_argv("c1", {}, ["true"])
    assert argv[argv.index("-w") + 1] == "/app/workspace"
    assert sandbox.isolation()["work_tree"] == "/app/workspace in the task container"


@pytest.mark.parametrize("image_workdir, back", [("/app\n", ""), ("/app/workspace\n", "/workspace")])
def test_host_mode_copies_back_only_the_work_tree(tmp_path, monkeypatch, image_workdir, back):
    task = load_task(_harbor_with(tmp_path, ""))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, image_workdir)
    assert error is None
    snapshot, copy_back = [c for c in calls if c[0] == "cp"][:2]
    assert snapshot[1].endswith(":/app/.")
    assert copy_back[1].endswith(f"work{back}/.") and copy_back[2].endswith(f":/app{back}/")


def test_in_container_agent_refuses_an_image_without_app(tmp_path, monkeypatch):
    """No WORKDIR falls back to /app; an image without /app gets its name, not a failed chown."""
    task = load_task(_harbor_with(tmp_path, ""))
    res, calls, chowns = _container_run(tmp_path, monkeypatch, task, "\n", workdir_rc=1)
    assert not chowns and "has no /app" in res.metrics["error"]


def test_host_probe_refuses_a_task_md_task_like_its_claude_arm(tmp_path, monkeypatch):
    task = load_task(_no_secret_task_md(tmp_path))
    cli = _capture()
    calls = cli.args
    with pytest.raises(ValueError, match="task.md"):
        DockerSolverHarness(harness_id="docker-probe-host", mode="probe", agent_location="host", runner=cli).run(
            task, output_dir=tmp_path / "out")
    assert calls == []


def test_probe_ignores_unresolved_env_it_never_uses(tmp_path, monkeypatch):
    """A probe runs no verifier or oracle, so a ${VAR} there is not missing data."""
    task = load_task(_task_md(tmp_path))  # verifier.env SECRET: ${HOST_SECRET}
    cli = _capture()
    calls = cli.args
    res = DockerSolverHarness(harness_id="docker-probe", mode="probe", runner=cli).run(
        task, output_dir=tmp_path / "out", dry_run=True)
    assert res.metrics == {"dry_run": True, "mode": "probe"} and calls == []
    claude = DockerSolverHarness(harness_id="docker-claude", mode="claude", runner=cli).run(
        task, output_dir=tmp_path / "out", dry_run=True)
    assert claude.metrics["unresolved_env"] == {"verifier": ["SECRET"]}


def test_in_container_probe_checks_both_layouts_grader_dirs(tmp_path, monkeypatch):
    """An image that bakes a readable /verifier or /oracle must not read as closed."""
    import dokimi.harnesses.container_agent as ca
    from dokimi.process import FakeRunner

    seen: dict[str, str] = {}
    exec_client = FakeRunner(lambda call: seen.update(call.env) or reply("{}"))
    sandbox = ca.ContainerSandbox(network="n", proxy_name="p", allow=[], binary=None,
                                  _scratch=tmp_path, runner=exec_client)
    task = load_task(_no_secret_task_md(tmp_path))
    sandbox.run_probe("c1", task.path, {}, None, lambda args, log, **kw: reply(), None)
    assert exec_client.calls[0].timeout == 120
    for path in ("/solution", "/oracle", "/tests", "/verifier"):
        assert path in seen["PROBE_TASK_FILES"].splitlines()


def test_exec_env_yaml_false_and_null(tmp_path, monkeypatch):
    """YAML ``false`` is forwarded as ``false``; a null value is not forwarded but listed."""
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    text = _TASK_MD.replace('    LITERAL: "1"\n', '    LITERAL: "1"\n    OFF_FLAG: false\n    EMPTY:\n')
    task = load_task(_task_md(tmp_path, text=text))
    assert _exec_env(task, "verifier") == ["-e", "LITERAL=1", "-e", "OFF_FLAG=false"]
    assert unresolved_env(task, "verifier") == ["EMPTY", "SECRET"]


def test_build_context_keeps_a_nested_skills_directory(tmp_path, monkeypatch):
    """Only ``environment/skills`` is the task's bundled skills; ``src/skills`` is task code."""
    task = load_task(_task_md(tmp_path))
    nested = task.path / "environment" / "src" / "skills"
    nested.mkdir(parents=True)
    (nested / "code.py").write_text("x = 1\n", encoding="utf-8")
    seen: dict[str, bool] = {}
    _build_context(task, lambda ctx: seen.update(nested=(ctx / "src" / "skills" / "code.py").is_file()))
    assert seen["nested"] is True


def test_host_workdir_check_comes_before_the_app_snapshot(tmp_path, monkeypatch):
    """An image without /app gets the refusal, not a failed snapshot copy."""
    task = load_task(_harbor_with(tmp_path, ""))
    calls, launched, error = _host_run(tmp_path, monkeypatch, task, "/app/workspace\n")
    inspect = next(i for i, c in enumerate(calls) if c[0] == "inspect")
    snapshot = next(i for i, c in enumerate(calls) if c[0] == "cp")
    assert inspect < snapshot
    no_app = _host_cli("/app/workspace\n", answer=lambda call: reply(returncode=1) if (
        call.argv[1] == "exec" and call.argv[-3:-1] == ("test", "-d")) else None)
    with pytest.raises(ValueError, match="has no /app/workspace"):
        DockerSolverHarness(harness_id="docker-claude-host", mode="claude", agent_location="host", runner=no_app).run(
            task, model="haiku", output_dir=tmp_path / "out2")


def test_redaction_catches_a_bare_scheme_less_proxy_echoed_by_a_step(monkeypatch):
    """`echo "$http_proxy"` in an oracle prints `u:pw@proxy:912` with no `://` or `VAR=`."""
    from dokimi.harnesses.docker_solver import _redact

    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    assert _redact("proxy is u:s3cret@proxy:912") == "proxy is u:s3cret@proxy:912"  # not the host's
    monkeypatch.setenv("http_proxy", "u:s3cret@proxy:912")
    monkeypatch.setenv("https_proxy", "http://user:p@ss@proxy:912/")
    assert _redact("proxy is u:s3cret@proxy:912") == "proxy is ***@proxy:912"
    assert _redact("other user:p@ss@proxy:912") == "other ***@proxy:912"
    monkeypatch.setenv("http_proxy", "user@proxy:912")  # no password: nothing to hide
    assert _redact("mail user@proxy:912") == "mail user@proxy:912"

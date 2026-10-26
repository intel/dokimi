# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""terminal-bench v1 tasks (task.yaml) run as they are by the docker-* harnesses,
and through a cached Harbor export by the Harbor-backed ones."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from dokimi.harnesses import TaskSuiteRunner, discover_tasks, load_task
from dokimi.harnesses import harbor_export, tbench_v1
from dokimi.harnesses.network_policy import resolve
from dokimi.process import reply
from dokimi.task_formats import inspect_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


_COMPOSE = """services:
  client:
    build:
      dockerfile: Dockerfile
    image: ${T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME}
    container_name: ${T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME}
    command: [ "sh", "-c", "sleep infinity" ]
    environment:
      - TEST_DIR=${T_BENCH_TEST_DIR}
      - GREETING=hi there
    volumes:
      - ${T_BENCH_TASK_LOGS_PATH}:${T_BENCH_CONTAINER_LOGS_PATH}
      - ${T_BENCH_TASK_AGENT_LOGS_PATH}:${T_BENCH_CONTAINER_AGENT_LOGS_PATH}
"""


def _tb1(root: Path, name: str = "hello-world", *, compose: str = _COMPOSE, yaml_extra: str = "") -> Path:
    task = root / name
    _write(task / "task.yaml", "instruction: |\n  Create hello.txt with 'Hello, world!'.\n"
                               "author_name: a\ndifficulty: easy\ncategory: file-operations\n" + yaml_extra)
    _write(task / "Dockerfile", "FROM ubuntu:24.04\nWORKDIR /app\n")
    _write(task / "docker-compose.yaml", compose)
    _write(task / "run-tests.sh", "#!/bin/bash\ncd /tests && python3 -m pytest -rA test_outputs.py\n")
    _write(task / "solution.sh", "echo 'Hello, world!' > hello.txt\n")
    _write(task / "tests" / "test_outputs.py", "def test_hello():\n    pass\n")
    return task


_PASS = "===== short test summary info =====\nPASSED test_outputs.py::test_hello\n"
_FAIL = ("===== short test summary info =====\nPASSED test_outputs.py::test_a\n"
         "FAILED test_outputs.py::test_b - AssertionError: x\n")
_SAME_NAME = "===== short test summary info =====\nFAILED tests/a.py::test_x\nPASSED tests/b.py::test_x\n"


# ── loading ───────────────────────────────────────────────────────────────────

def test_load_reads_task_yaml_with_terminal_bench_defaults(tmp_path):
    task = load_task(_tb1(tmp_path))
    assert task.format == "terminal-bench-v1"
    assert task.instruction.startswith("Create hello.txt")
    assert task.agent["timeout_sec"] == 360.0 and task.verifier["timeout_sec"] == 60.0
    assert task.verifier["parser"] == "pytest"
    assert task.metadata["difficulty"] == "easy"
    assert task.container_env == {"GREETING": "hi there", "TEST_DIR": "/tests"}
    assert task.local_docker_problem(oracle=True) is None
    assert task.environment["memory_mb"] == tbench_v1.DEFAULT_MEMORY_MB == 8192



def test_network_is_public_for_oracle_and_verifier_not_for_an_agent(tmp_path):
    task = load_task(_tb1(tmp_path))
    oracle, agent = resolve(task), resolve(task, agent=True)
    assert oracle.solve.mode == oracle.verify.mode == agent.verify.mode == "public"
    assert agent.solve.mode != "public"


_LIMITS = "    deploy:\n      resources:\n        limits:\n          memory: {}\n"


@pytest.mark.parametrize(("limit", "mb"), [("4.0G", 4096), ("512m", 512), ("1GiB", 1024), ("junk", 8192)])
def test_memory_limit_comes_from_compose_deploy_limits_else_the_tb1_default(tmp_path, limit, mb):
    task = load_task(_tb1(tmp_path, compose=_COMPOSE + _LIMITS.format(limit)))
    assert task.environment["memory_mb"] == mb
    assert "memory_mb" not in task.environment["terminal_bench"]


def test_memory_default_is_configurable_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv(tbench_v1.MEMORY_ENV, "16384")
    assert load_task(_tb1(tmp_path)).environment["memory_mb"] == 16384
    assert load_task(_tb1(tmp_path, "b", compose=_COMPOSE + _LIMITS.format("4G"))).environment["memory_mb"] == 4096
    monkeypatch.setenv(tbench_v1.MEMORY_ENV, "8g")
    with pytest.raises(ValueError, match=tbench_v1.MEMORY_ENV):
        tbench_v1.default_memory_mb()


def test_load_reads_timeouts_and_a_subdir_build_context(tmp_path):
    compose = "services:\n  client:\n    build:\n      context: ./env\n      dockerfile: Dockerfile.x\n"
    path = _tb1(tmp_path, compose=compose, yaml_extra="max_agent_timeout_sec: 900\nmax_test_timeout_sec: 120\n")
    _write(path / "env" / "Dockerfile.x", "FROM ubuntu:24.04\n")
    task = load_task(path)
    assert task.agent["timeout_sec"] == 900.0 and task.verifier["timeout_sec"] == 120.0
    assert task.build_context == path / "env" and task.dockerfile == "Dockerfile.x"


@pytest.mark.parametrize("text, match", [
    ("instruction: [unclosed\n", "not valid YAML"),
    ("- a list\n", "must be a mapping"),
    ("author_name: a\n", "no instruction"),
])
def test_invalid_task_yaml_is_refused(tmp_path, text, match):
    path = _tb1(tmp_path)
    _write(path / "task.yaml", text)
    with pytest.raises(ValueError, match=match):
        load_task(path)


def test_discovery_finds_terminal_bench_v1_tasks(tmp_path):
    _tb1(tmp_path, "a")
    _tb1(tmp_path, "b")
    assert [(t.name, t.format) for t in discover_tasks(tmp_path)] == [
        ("a", "terminal-bench-v1"), ("b", "terminal-bench-v1")]


def test_discovery_skips_a_task_yaml_without_an_instruction(tmp_path):
    _tb1(tmp_path, "hello-world")
    _write(tmp_path / "not-a-task" / "task.yaml", "name: foo\nsteps: []\n")
    _write(tmp_path / "not-a-task" / "Dockerfile", "FROM ubuntu:24.04\n")
    with pytest.warns(UserWarning, match="not-a-task: task.yaml has no instruction"):
        assert [t.name for t in discover_tasks(tmp_path)] == ["hello-world"]
    with pytest.raises(ValueError, match="no instruction"):
        load_task(tmp_path / "not-a-task")


def _compose_with(old: str, new: str):
    return lambda p: _write(p / "docker-compose.yaml", _COMPOSE.replace(old, new))


@pytest.mark.parametrize("edit, oracle, match", [
    (lambda p: _write(p / "docker-compose.yaml", _COMPOSE + "  db:\n    image: postgres\n"), False, "service db"),
    (lambda p: _write(p / "docker-compose.yaml", _COMPOSE + "    tmpfs: [/run]\n"), False, "client.tmpfs"),
    (_compose_with("services:", "networks:\n  x: {}\nservices:"), False, "top-level networks"),
    (_compose_with("    volumes:\n", "    volumes:\n      - ./d:/d\n"), False, "client.volumes ./d:/d"),
    (_compose_with('"sleep infinity"', '"service nginx start"'), False, "client.command"),
    (_compose_with("      dockerfile:", "      args: {A: b}\n      dockerfile:"), False, "client.build.args"),
    (_compose_with("      dockerfile:", "      target: dev\n      dockerfile:"), False, "client.build.target"),
    (_compose_with("      - GREETING", "      - PASSTHRU\n      - GREETING"), False, "client.environment PASSTHRU"),
    (_compose_with("      - GREETING", "      - D=${HOME}/x\n      - GREETING"), False, "client.environment D"),
    (lambda p: _write(p / "task.yaml", (p / "task.yaml").read_text() + "run_tests_in_same_shell: true\n"),
     False, "run_tests_in_same_shell"),
    (lambda p: _write(p / "task.yaml", (p / "task.yaml").read_text() + "parser_name: swebench\n"),
     False, "parser_name is swebench"),
    (lambda p: ((p / "solution.sh").unlink(), _write(p / "solution.yaml", "- keys: ls\n")), True, "solution.yaml"),
    (lambda p: (p / "solution.sh").unlink(), True, "no solution.sh"),
])
def test_local_docker_problem_names_what_one_docker_run_cannot_do(tmp_path, edit, oracle, match):
    path = _tb1(tmp_path)
    edit(path)
    assert match in load_task(path).local_docker_problem(oracle=oracle)


@pytest.mark.parametrize("command", ['"sleep infinity"', '["sleep", "infinity"]', '["bash", "-c", "sleep infinity"]'])
def test_terminal_bench_idle_command_and_a_literal_environment_run(tmp_path, command):
    compose = _COMPOSE.replace('[ "sh", "-c", "sleep infinity" ]', command) + "    working_dir: ${WD}\n"
    task = load_task(_tb1(tmp_path, compose=compose))
    assert task.local_docker_problem(oracle=True) is None
    assert "workdir" not in task.environment, "a working_dir compose fills in is not the image's"


def test_solution_is_needed_by_the_oracle_only(tmp_path):
    path = _tb1(tmp_path)
    (path / "solution.sh").unlink()
    assert load_task(path).local_docker_problem(oracle=False) is None


# ── verdict ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("output, reward", [
    (_PASS, 1.0),
    (_FAIL, 0.0),
    (_SAME_NAME, 0.0),  # one file's pass does not hide another's failure
    ("===== short test summary info =====\nSKIPPED [1] t.py:3: why\nXFAIL t.py::test_x\n", 1.0),
    ("===== short test summary info =====\nXPASS t.py::test_x\n", 0.0),
    ("===== short test summary info =====\nERROR t.py::test_x - ImportError\n", 0.0),
    ("===== short test summary info =====\n", 1.0),  # all() of nothing, as terminal-bench
    ("1 passed in 0.1s\n", 0.0),  # no summary section
    ("", 0.0),
])
def test_reward_follows_the_pytest_summary(output, reward):
    assert tbench_v1.reward(output)[0] == reward


def test_parse_pytest_names_tests_and_strips_failure_messages():
    assert tbench_v1.parse_pytest(_FAIL) == {"test_outputs.py::test_a": True, "test_outputs.py::test_b": False}
    assert tbench_v1.parse_pytest("no summary") is None


@pytest.mark.skipif(shutil.which("awk") is None, reason="needs awk")
@pytest.mark.parametrize("output", [
    _PASS, _FAIL, _SAME_NAME, "===== short test summary info =====\n", "1 passed\n", "",
    "== SHORT TEST SUMMARY INFO ==\nERROR t.py::x\n", "===== short test summary info =====\nXPASS t.py::y\n",
])
def test_exported_awk_verdict_matches_the_python_parser(tmp_path, output):
    awk = harbor_export._VERDICT_AWK
    log = _write(tmp_path / "out.txt", output)
    got = subprocess.run(["sh", "-c", f"{awk} {log}"], capture_output=True, text=True, check=True).stdout
    assert float(got.strip()) == tbench_v1.reward(output)[0]


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="needs bash and POSIX paths")
@pytest.mark.parametrize("summary, code, reward", [(_PASS, 0, "1"), (_FAIL, 3, "0")])
def test_exported_test_sh_scores_run_tests_and_keeps_its_exit_code(tmp_path, summary, code, reward):
    tests, logs = tmp_path / "tests", tmp_path / "logs"
    _write(tests / "run-tests.sh", f'echo "TEST_DIR=$TEST_DIR"\necho "on stderr" >&2\n'
                                   f"printf %s {shlex.quote(summary)}\nexit {code}\n")
    script = harbor_export._TEST_SH.replace("/tests", str(tests)).replace("/logs", str(logs))
    env = {k: v for k, v in os.environ.items() if k != "TEST_DIR"}
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=False)
    written = (logs / "verifier" / "terminal-bench-output.txt").read_text(encoding="utf-8")
    assert proc.returncode == code
    assert (logs / "verifier" / "reward.txt").read_text(encoding="utf-8").strip() == reward
    assert f"TEST_DIR={tests}" in written and "on stderr" in written and summary in written
    assert proc.stdout == written  # tee: Harbor's own log of test.sh keeps the output


# ── docker-oracle ────────────────────────────────────────────────────────────

def _run_oracle(tmp_path, verifier_output: str, returncode: int = 0, verifier_stderr: str = ""):
    from docker_cli_fake import DockerCLI

    def answer(call):  # bash: the step's own 2>&1 puts stderr, printed first, in the one stream
        if call.argv[:2] == ("docker", "exec") and call.argv[-1].startswith("bash /tests/run-tests.sh"):
            if call.argv[-1].endswith(" 2>&1"):
                return reply(verifier_stderr + verifier_output, returncode=returncode)
            return reply(verifier_output, verifier_stderr, returncode=returncode)
        return None

    cli = DockerCLI(reward=None, answer=answer)
    task = load_task(_tb1(tmp_path / "tasks"))
    out = TaskSuiteRunner([task], ["docker-oracle"], output_root=tmp_path / "runs", runner=cli).run()
    (row,) = out["results"]
    return task, cli, row


def test_docker_oracle_runs_a_tb1_task_as_terminal_bench_does(tmp_path):
    task, cli, row = _run_oracle(tmp_path, _PASS)
    assert "error" not in row["metrics"] and row["reward"] == 1.0 and row["passed"] is True
    copies = {(c[1], c[2].split(":", 1)[1]) for c in cli.args if c[0] == "cp" and ":" in c[2]}
    assert (str(task.path / "solution.sh"), "/oracle/solution.sh") in copies
    assert (str(task.path / "run-tests.sh"), "/tests/run-tests.sh") in copies
    assert (f"{task.path / 'tests'}/.", "/tests") in copies
    (build,) = [c for c in cli.args if c[0] == "build"]
    assert build[-1] == str(task.path)
    (run,) = [c for c in cli.args if c[0] == "run" and "sleep" in " ".join(c)]
    assert "TEST_DIR=/tests" in run and "GREETING=hi there" in run
    for step in ("bash /oracle/solution.sh", "bash /tests/run-tests.sh 2>&1"):
        (call,) = [c for c in cli.args if c[0] == "exec" and c[-1] == step]
        assert "root" not in call[: call.index("bash")], "runs as the image user, as terminal-bench does"


def test_docker_oracle_verdict_is_the_summary_not_the_exit_code(tmp_path):
    _, _, row = _run_oracle(tmp_path / "a", _FAIL, returncode=0)
    assert row["reward"] == 0.0
    _, _, row = _run_oracle(tmp_path / "b", _PASS, returncode=1)
    assert row["reward"] == 1.0


def test_docker_oracle_verdict_ignores_stderr_printed_before_the_summary(tmp_path):
    pip = "ERROR: pip's dependency resolver does not currently take into account all the packages.\n"
    _, _, row = _run_oracle(tmp_path, _PASS, verifier_stderr=pip)
    assert row["reward"] == 1.0, row


def test_docker_oracle_refuses_a_multi_service_task_without_docker(tmp_path):
    from docker_cli_fake import DockerCLI

    cli = DockerCLI()
    path = _tb1(tmp_path / "tasks", compose=_COMPOSE + "  db:\n    image: postgres\n")
    out = TaskSuiteRunner([load_task(path)], ["docker-oracle"], output_root=tmp_path / "runs", runner=cli).run()
    (row,) = out["results"]
    assert row["reward"] is None and "service db" in row["metrics"]["error"]
    assert cli.args == []


# ── Harbor export ────────────────────────────────────────────────────────────

def test_export_writes_the_harbor_layout(tmp_path):
    task = load_task(_tb1(tmp_path / "tasks"))
    dest = harbor_export.export(task, tmp_path / "out")
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == [
        "environment/Dockerfile", "instruction.md", "solution/solve.sh", "task.toml",
        "tests/run-tests.sh", "tests/test.sh", "tests/test_outputs.py"]
    dockerfile = (dest / "environment" / "Dockerfile").read_text()
    assert "ENV TEST_DIR=/tests" in dockerfile and 'ENV GREETING="hi there"' in dockerfile
    toml = (dest / "task.toml").read_text()
    assert "[verifier]\ntimeout_sec = 60.0" in toml and "[agent]\ntimeout_sec = 360.0" in toml
    assert "[environment]\nmemory_mb = 8192" in toml
    assert os.access(dest / "tests" / "test.sh", os.X_OK)


def test_exported_task_toml_parses_with_a_non_bmp_character(tmp_path):
    import tomllib

    author = "Zo\u00eb \U0001f600"
    task = load_task(_tb1(tmp_path / "tasks", yaml_extra=f'author_email: "{author}"\n'))
    dest = harbor_export.export(task, tmp_path / "out")
    assert tomllib.loads((dest / "task.toml").read_text(encoding="utf-8"))["metadata"]["author_email"] == author


def test_export_puts_a_subdir_context_and_its_named_dockerfile_where_harbor_builds(tmp_path):
    compose = _COMPOSE.replace("      dockerfile: Dockerfile\n",
                               "      context: ./env\n      dockerfile: Dockerfile.x\n    working_dir: /srv\n")
    path = _tb1(tmp_path / "tasks", compose=compose)
    (path / "Dockerfile").unlink()
    _write(path / "env" / "Dockerfile.x", "FROM ubuntu:24.04\nCOPY data.txt /data.txt\n")
    _write(path / "env" / "data.txt", "x\n")
    task = load_task(path)
    assert task.environment["terminal_bench"]["workdir"] == "/srv"
    dest = harbor_export.export(task, tmp_path / "out")
    env = sorted(p.relative_to(dest / "environment").as_posix() for p in (dest / "environment").rglob("*"))
    assert env == ["Dockerfile", "data.txt"]
    dockerfile = (dest / "environment" / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM ubuntu:24.04\nCOPY data.txt") and "\nWORKDIR /srv\n" in dockerfile


def test_export_keeps_tests_and_solution_the_dockerfile_copies(tmp_path):
    path = _tb1(tmp_path / "tasks")
    _write(path / "Dockerfile", "FROM ubuntu:24.04\nCOPY tests /tests\nCOPY ./solution.sh /s.sh\n")
    dest = harbor_export.export(load_task(path), tmp_path / "out")
    assert (dest / "environment" / "tests" / "test_outputs.py").is_file()
    assert (dest / "environment" / "solution.sh").is_file()
    assert not (dest / "environment" / "run-tests.sh").exists()


def test_export_keeps_a_multi_service_compose_with_main_renamed(tmp_path):
    import yaml

    task = load_task(_tb1(tmp_path / "tasks", compose=_COMPOSE + "  db:\n    image: postgres\n"))
    dest = harbor_export.export(task, tmp_path / "out")
    compose = yaml.safe_load((dest / "environment" / "docker-compose.yaml").read_text())
    assert list(compose["services"]) == ["main", "db"]
    assert compose["services"]["main"]["image"] == "${MAIN_IMAGE_NAME}"


def test_export_refuses_solution_yaml(tmp_path):
    path = _tb1(tmp_path / "tasks")
    (path / "solution.sh").unlink()
    _write(path / "solution.yaml", "- keys: ls\n")
    with pytest.raises(ValueError, match="solution.yaml"):
        harbor_export.export(load_task(path), tmp_path / "out")
    with pytest.raises(ValueError, match="solution.yaml"):
        harbor_export.harbor_view(load_task(path), cache_root=tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob(".staging-*")), "a refused export leaves no staging dir"
    (report,) = inspect_path(path)
    assert report.consumers == ["docker-*"] and any("solution.yaml" in p for p in report.problems)


def test_harbor_view_caches_by_content_and_passes_other_formats_through(tmp_path):
    task = load_task(_tb1(tmp_path / "tasks"))
    first = harbor_export.harbor_view(task, cache_root=tmp_path / "cache")
    assert first.format == "harbor" and (first.path / "task.toml").is_file()
    assert harbor_export.harbor_view(task, cache_root=tmp_path / "cache").path == first.path
    _write(task.path / "tests" / "test_outputs.py", "def test_other():\n    pass\n")
    assert harbor_export.harbor_view(task, cache_root=tmp_path / "cache").path != first.path
    assert harbor_export.harbor_view(first) is first


def test_harbor_view_rewrites_the_export_when_the_memory_default_changes(tmp_path, monkeypatch):
    path = _tb1(tmp_path / "tasks")
    harbor_export.harbor_view(load_task(path), cache_root=tmp_path / "cache")
    monkeypatch.setenv("DOKIMI_TBENCH_V1_MEMORY_MB", "16384")
    view = harbor_export.harbor_view(load_task(path), cache_root=tmp_path / "cache")
    assert "memory_mb = 16384" in (view.path / "task.toml").read_text()


def test_harbor_view_ignores_an_export_planted_in_the_shared_temp_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(harbor_export.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    task = load_task(_tb1(tmp_path / "tasks"))
    key = harbor_export._content_key(task.path, harbor_export._task_toml(task))
    planted = tmp_path / "tmp" / "dokimi-harbor-export" / key / task.name
    _write(planted / "task.toml", "[verifier]\ntimeout_sec = 1.0\n")
    view = harbor_export.harbor_view(task)
    assert "timeout_sec = 60.0" in (view.path / "task.toml").read_text()
    assert (view.path.parents[1].stat().st_mode & 0o777) == 0o700


def test_harbor_harness_runs_the_export_not_the_source(tmp_path, monkeypatch):
    from docker_cli_fake import DockerCLI

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cli = DockerCLI(reward="1")
    task = load_task(_tb1(tmp_path / "tasks"))
    out = TaskSuiteRunner([task], ["terminal-bench:oracle"], output_root=tmp_path / "runs", runner=cli).run()
    (row,) = out["results"]
    assert row["reward"] == 1.0, row
    (harbor,) = [c for c in cli.calls if Path(c.argv[0]).name == "harbor"]
    argv = " ".join(harbor.argv)
    assert str(tmp_path / "home" / ".cache" / "dokimi" / "harbor-export") in argv and str(task.path) + " " not in argv + " "


def test_benchflow_harness_runs_the_export_not_the_source(tmp_path, monkeypatch):
    from dokimi.harnesses.registry import create_harness

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    task = load_task(_tb1(tmp_path / "tasks"))
    res = create_harness("benchflow:oracle").run(task, output_dir=tmp_path / "o", dry_run=True)
    tasks_dir = Path(res.command[res.command.index("--tasks-dir") + 1])
    assert tasks_dir != task.path and (tasks_dir / "task.toml").is_file()


# ── real docker ──────────────────────────────────────────────────────────────

@pytest.mark.timeout(900)
@pytest.mark.skipif(not os.environ.get("DOKIMI_DOCKER_TESTS") or shutil.which("docker") is None,
                    reason="set DOKIMI_DOCKER_TESTS=1 with docker available")
def test_docker_oracle_passes_and_a_no_op_fails_on_real_docker(tmp_path):
    path = _tb1(tmp_path / "tasks")
    _write(path / "Dockerfile", "FROM python:3.12-slim\nRUN pip install -q pytest==8.3.3\nWORKDIR /app\n")
    _write(path / "run-tests.sh", "#!/bin/bash\necho \"ERROR: pip's resolver warning\" >&2\n"
           "cd /tests && python3 -m pytest -rA test_outputs.py\n")
    _write(path / "tests" / "test_outputs.py",
           "from pathlib import Path\n\ndef test_hello():\n"
           "    assert Path('/app/hello.txt').read_text().strip() == 'Hello, world!'\n")
    task = load_task(path)
    out = TaskSuiteRunner([task], ["docker-oracle"], output_root=tmp_path / "runs").run()
    (row,) = out["results"]
    assert row["reward"] == 1.0, row
    _write(path / "solution.sh", "true\n")
    out = TaskSuiteRunner([load_task(path)], ["docker-oracle"], output_root=tmp_path / "runs2").run()
    (row,) = out["results"]
    assert row["reward"] == 0.0, row

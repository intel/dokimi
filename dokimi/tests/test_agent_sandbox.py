# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the solver launch environment and the isolation probe.

The probe tests run the real probe script with ``sh`` on the host; Docker is
never invoked, and the network check points at a closed loopback port so no
test leaves the machine.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import dokimi.harnesses.agent_sandbox as sandbox
from dokimi.harnesses import load_task
from dokimi.harnesses.agent_sandbox import (
    LEAK_CLASSES,
    AgentWorkspace,
    agent_env,
    denied_env_names,
    default_forbidden,
    parse_probe_output,
    probe_reward,
    run_probe,
    solver_cli_flags,
)
from dokimi.harnesses.registry import create_harness
from dokimi.process import reply
from docker_cli_fake import DockerCLI

_CLOSED_URL = "http://127.0.0.1:9/"
_MARKERS = (".git", "task.toml", "CLAUDE.md", "skills")


def _has_marker_above(path: Path) -> bool:
    return any((p / m).exists() for p in path.parents for m in _MARKERS)


@pytest.fixture
def tmp_path(tmp_path):
    """pytest's ``tmp_path``, unless a checkout marker or ``skills/`` sits above it.

    The probe and ``default_forbidden`` walk up from the work tree and the task,
    so a basetemp under a checkout (or beside a ``skills/`` dir) would change
    their answers. Then a fresh dir under a clean well-known temp root is used.
    """
    if not _has_marker_above(tmp_path / "x"):
        yield tmp_path
        return
    # Well-known roots only as a fallback; the dir itself comes from mkdtemp.
    for base in ("/var/tmp", "/dev/shm", "/tmp"):  # nosec B108
        if Path(base).is_dir() and not _has_marker_above(Path(base) / "x" / "y"):
            root = Path(tempfile.mkdtemp(prefix="dokimi-sandbox-test-", dir=base))
            yield root
            shutil.rmtree(root, ignore_errors=True)
            return
    pytest.skip("no temp dir without a checkout marker above it")


@pytest.fixture(autouse=True)
def _scratch_under_tmp(tmp_path, monkeypatch):
    """Keep scratch roots inside the test's tmp dir, and the probe off the network."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(sandbox, "PROBE_URL", _CLOSED_URL)
    monkeypatch.setattr(sandbox, "PROBE_DIRECT", "127.0.0.1 9")
    monkeypatch.setattr(sandbox, "PROBE_DOCKER_SOCKETS", str(tmp_path / "no-docker.sock"))
    monkeypatch.setattr(sandbox, "PROBE_HOST_GATEWAY", "127.0.0.1")  # nothing on 2375/2376
    monkeypatch.setattr(sandbox, "PROBE_DNS_NAME", "no-such-host.invalid")
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-operator-config"))


def _content_checkout(tmp_path: Path) -> Path:
    """A content repo shaped like uxlfoundation/skills: skills/ next to the tasks."""
    root = tmp_path / "content"
    (root / ".git").mkdir(parents=True)
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Use for demo things.\n---\nthe answer\n", encoding="utf-8")
    task = root / "evaluation" / "harbor" / "tasks" / "demo-task"
    (task / "solution").mkdir(parents=True)
    (task / "solution" / "solve.sh").write_text("cp fixed.cpp /app/", encoding="utf-8")
    (task / "tests").mkdir()
    (task / "tests" / "test.sh").write_text("exit 0", encoding="utf-8")
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("Fix it. `g++ main.cpp -o /app/bench`", encoding="utf-8")
    (task / "task.toml").write_text("[agent]\ntimeout_sec = 30\n", encoding="utf-8")
    return task


# ── launch environment ──────────────────────────────────────────────────────


def test_agent_env_is_an_allowlist():
    source = {
        "PATH": "/usr/bin", "LC_ALL": "C", "HTTPS_PROXY": "http://p:1",
        "AWS_BEARER_TOKEN_BEDROCK": "tok", "CLAUDE_CODE_USE_BEDROCK": "1",
        "GITHUB_TOKEN": "ghp_secret", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s",
        "HOME": "/home/operator",
    }
    env = agent_env(source, {"EXTRA": "kept"})
    assert env == {
        "PATH": "/usr/bin", "LC_ALL": "C", "HTTPS_PROXY": "http://p:1",
        "AWS_BEARER_TOKEN_BEDROCK": "tok", "CLAUDE_CODE_USE_BEDROCK": "1", "EXTRA": "kept",
        # Bundled skills off in both arms, so the Skill tool offers only the treatment.
        "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1", "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL": "1",
        # Managed skills under /etc/claude-code load whatever --setting-sources says.
        "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1",
    }


def test_cli_flags_restrict_the_tool_set_and_load_no_settings():
    flags = solver_cli_flags()
    tools = flags[flags.index("--tools") + 1].split(",")
    # Exactly these: a denylist check would miss a new Agent, Skill or Workflow.
    assert tools == ["Read", "Edit", "Write", "Bash", "Glob", "Grep", "Skill"]
    # --allowedTools only pre-approves; it must not be mistaken for the restriction.
    assert "--allowedTools" not in flags
    assert flags[flags.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in flags
    # Both arms carry the Skill tool; the built-in plugins' skills are switched off.
    assert "Skill" in tools
    settings = json.loads(flags[flags.index("--settings") + 1])
    # By name: without the plugin-authoring key the CLI offers that skill to both arms.
    assert settings == {"enabledPlugins": {"cc-plugin-agents-md@builtin": False,
                                           "cc-plugin-plugin-authoring@builtin": False}}


def test_names_the_harness_sets_itself_are_not_probed_as_leaks():
    """An operator who exports a seal variable has leaked nothing: the harness sets it too."""
    source = {"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1", "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL": "1",
              "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1", "GITHUB_TOKEN": "x"}
    assert denied_env_names(source) == ["GITHUB_TOKEN"]


def test_workspace_is_scratch_with_its_own_home_and_config(tmp_path):
    ws = AgentWorkspace.create()
    env = ws.env({"PATH": "/usr/bin", "HOME": "/home/operator"})
    assert ws.work.is_dir() and ws.work.parent == ws.root
    assert env["HOME"] == str(ws.root / "home")
    assert env["CLAUDE_CONFIG_DIR"] == str(ws.root / "config")
    assert not any((p / ".git").exists() for p in (ws.work, *ws.work.parents))
    ws.cleanup()
    assert not ws.root.exists()


def test_default_forbidden_names_grader_files_and_content_skills(tmp_path):
    task = _content_checkout(tmp_path)
    forbidden = default_forbidden(task)
    assert forbidden["task_files"][:2] == [task / "solution", task / "tests"]
    assert forbidden["treatment"] == [tmp_path / "content" / "skills"]
    assert Path.home() / ".claude" in forbidden["operator_config"]


def test_default_forbidden_names_the_task_md_grader_dirs(tmp_path):
    """A BenchFlow task.md task ships oracle/ and verifier/, not solution/ and tests/."""
    task = tmp_path / "sb-task"
    for sub in ("oracle", "verifier"):
        (task / sub).mkdir(parents=True)
        (task / sub / "x.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (task / "task.md").write_text("---\n---\nDo it.\n", encoding="utf-8")
    task_files = default_forbidden(task)["task_files"]
    assert task / "oracle" in task_files and task / "verifier" in task_files


def test_host_probe_reports_task_md_grader_dirs_open(tmp_path):
    task = tmp_path / "sb-task"
    (task / "verifier").mkdir(parents=True)
    (task / "verifier" / "test.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    ws = AgentWorkspace.create(forbidden=default_forbidden(task))
    result = run_probe(ws, {"PATH": "/usr/bin:/bin"})
    ws.cleanup()
    assert result["task_files"] == "open"  # the host cannot hide an absolute path


def test_default_forbidden_without_a_checkout_has_no_treatment(tmp_path):
    task = tmp_path / "loose-task"
    task.mkdir()
    assert "treatment" not in default_forbidden(task)


# ── probe ───────────────────────────────────────────────────────────────────


_RAW_TOOLS = all(shutil.which(tool, path="/usr/bin:/bin") for tool in ("bash", "timeout"))
_PROC_ENV = Path("/proc/self/environ").is_file()


def test_probe_on_host_reports_absolute_paths_open_and_the_rest_closed(tmp_path):
    task = _content_checkout(tmp_path)
    forbidden = default_forbidden(task)
    forbidden["operator_config"] = [tmp_path / "no-such-home" / ".claude"]
    ws = AgentWorkspace.create(forbidden=forbidden)
    # A name no developer exports: the probe matches the host *value* in the
    # parent chain, and the pytest process is in that chain.
    result = run_probe(ws, {"PATH": "/usr/bin:/bin", "DOKIMI_TEST_ONLY_TOKEN": "ghp_secret"})
    ws.cleanup()
    assert result == {
        "task_files": "open",        # the host cannot hide an absolute path
        "treatment": "open",
        "operator_config": "closed",
        "checkout_walk": "closed",   # the work tree is no longer inside a checkout
        "network": "closed" if _RAW_TOOLS else "untested",
        "env": "closed" if _PROC_ENV else "untested",
        "docker_api": "closed" if _RAW_TOOLS else "untested",
        "dns": "closed",             # the pinned name never resolves
    }
    assert probe_reward(result) == 0.0


def test_probe_sees_a_variable_that_slips_through(tmp_path):
    ws = AgentWorkspace.create()
    result = run_probe(ws, {"PATH": "/usr/bin:/bin", "GITHUB_TOKEN": "x"},
                       extra_env={"GITHUB_TOKEN": "x"})
    ws.cleanup()
    assert result["env"] == ("open" if _PROC_ENV else "untested")
    assert result.get("env_leaked", "") == ("GITHUB_TOKEN" if _PROC_ENV else "")


@pytest.mark.parametrize("marker", [".git", "task.toml", "CLAUDE.md"])
def test_probe_sees_a_work_tree_inside_a_checkout(tmp_path, marker):
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / marker).mkdir()
    ws = AgentWorkspace(root=checkout / "results" / "run")
    ws.work.mkdir(parents=True)
    result = run_probe(ws, {"PATH": "/usr/bin:/bin"})
    assert result["checkout_walk"] == "open"


def _needs_a_fetch_client():
    if not any(shutil.which(c, path="/usr/bin:/bin") for c in ("curl", "wget", "python3")):
        pytest.skip("no curl, wget or python3 in /usr/bin:/bin")


def test_probe_network_open_when_something_answers(tmp_path):
    _needs_a_fetch_client()
    # The with block closes the stdout pipe (and waits) once the server is stopped.
    with subprocess.Popen(
        ["python3", "-u", "-m", "http.server", "0", "--bind", "127.0.0.1"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=tmp_path,
    ) as server:
        try:
            line = server.stdout.readline()  # "Serving HTTP on 127.0.0.1 port N ..."
            port = line.split(" port ")[1].split()[0]
            ws = AgentWorkspace.create()
            result = run_probe(ws, {"PATH": "/usr/bin:/bin"}, url=f"http://127.0.0.1:{port}/")
            ws.cleanup()
        finally:
            server.terminate()
            server.wait(timeout=10)
    assert result["network"] == "open"


def _probe_script_run(tmp_path, **env) -> dict[str, str]:
    """The probe script itself, with every variable it reads set (it runs under set -u)."""
    full = {"PATH": "/usr/bin:/bin", **sandbox.probe_env({}, [], _CLOSED_URL, ""),
            "PROBE_GATEWAY": "127.0.0.1", **env}
    proc = subprocess.run(["sh", "-c", sandbox.probe_script()], cwd=str(tmp_path), env=full,
                          text=True, capture_output=True, timeout=60, check=False)
    return parse_probe_output(proc.stdout)


_needs_bash_and_timeout = pytest.mark.skipif(
    not (shutil.which("bash", path="/usr/bin:/bin") and shutil.which("timeout", path="/usr/bin:/bin")),
    reason="the raw-socket checks need bash and timeout in /usr/bin:/bin")


@_needs_bash_and_timeout
def test_probe_gateway_unknown_is_untested(tmp_path):
    """A failed gateway lookup never tried the daemon ports: not a pass."""
    result = _probe_script_run(tmp_path, PROBE_GATEWAY="", PROBE_DNS_NAME="no-such-host.invalid")
    assert result["network"] == "closed"  # the script ran to the end
    assert result["docker_api"] == "untested"
    assert probe_reward(result) == 0.0


@_needs_bash_and_timeout
def test_probe_gateway_known_and_unreachable_is_closed(tmp_path):
    result = _probe_script_run(tmp_path, PROBE_GATEWAY="127.0.0.1")
    assert result["docker_api"] == "closed"


def test_probe_dns_open_when_a_name_resolves(tmp_path):
    # A numeric address resolves from nothing but itself; "localhost" can send an
    # AAAA query through the search domains and outlast the probe's 5 s budget.
    result = _probe_script_run(tmp_path, PROBE_DNS_NAME="127.0.0.1")
    assert result["dns"] == "open"
    assert probe_reward({**{k: "closed" for k in LEAK_CLASSES}, "dns": "open"}) == 0.0


def test_probe_dns_closed_when_nothing_resolves(tmp_path):
    result = _probe_script_run(tmp_path, PROBE_DNS_NAME="no-such-host.invalid")
    assert result["dns"] == "closed"
    assert result["network"] == ("closed" if _RAW_TOOLS else "untested")


def test_probe_env_carries_the_dns_name(monkeypatch):
    assert sandbox.probe_env({}, [], dns_name="x.test")["PROBE_DNS_NAME"] == "x.test"
    monkeypatch.setattr(sandbox, "PROBE_DNS_NAME", "y.test")
    assert sandbox.probe_env({}, [])["PROBE_DNS_NAME"] == "y.test"


def test_parse_probe_output_marks_garbage_untested():
    assert parse_probe_output("noise\n") == {k: "untested" for k in LEAK_CLASSES}
    assert probe_reward(parse_probe_output("")) == 0.0
    sealed = json.dumps({k: "closed" for k in LEAK_CLASSES})
    assert probe_reward(parse_probe_output("x\n" + sealed + "\n")) == 1.0


# ── harness integration (Docker steps patched out) ──────────────────────────


def _docker_cli(**kwargs) -> DockerCLI:
    """The docker CLI and the host claude; the host probe's `sh` runs for real."""
    return DockerCLI(real=("sh",), **kwargs)


def test_probe_harness_scores_the_probe_and_skips_the_verifier(tmp_path, monkeypatch):
    task = load_task(_content_checkout(tmp_path))
    cli = _docker_cli(answer=lambda call: pytest.fail("probe must not run the verifier") if (
        "test.sh" in call.argv[-1]) else None)
    calls = cli.args

    result = create_harness("docker-probe-host", runner=cli).run(task, output_dir=tmp_path / "out")

    assert result.reward == 0.0 and result.passed is False
    probe = result.metrics["isolation_probe"]
    assert probe["task_files"] == "open" and probe["checkout_walk"] == "closed"
    assert result.metrics["isolation"]["agent_location"] == "host"
    saved = json.loads((tmp_path / "out" / "isolation_probe.json").read_text(encoding="utf-8"))
    assert saved == probe
    assert not any(["cp", str(task.path / "tests")] == c[:2] for c in calls)


@pytest.mark.parametrize("arm", ["skill", "skill-discover"])
def test_claude_solve_runs_in_scratch_with_sealed_env(tmp_path, monkeypatch, arm):
    task = load_task(_content_checkout(tmp_path))
    skill = tmp_path / "content" / "skills" / "demo"
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    config = Path(sandbox.os.environ["CLAUDE_CONFIG_DIR"])
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}}),
                                          encoding="utf-8")
    seen: dict = {}

    def fake_claude(call):
        cmd = list(call.argv)
        cwd = call.cwd
        plugin = Path(cmd[cmd.index("--plugin-dir") + 1])
        seen.update(cmd=cmd, cwd=cwd, env=call.env, plugin=plugin, stdin=call.input,
                    timeout=call.timeout,
                    skill_seen=(plugin / "skills" / "demo" / "SKILL.md").is_file(),
                    manifest=json.loads((plugin / ".claude-plugin" / "plugin.json").read_text()))
        (cwd / "main.cpp").write_text("fixed", encoding="utf-8")
        stream = "\n".join([
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": "demo:demo"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {}}]}}),
            json.dumps({"type": "result", "num_turns": 2, "total_cost_usd": 0.01}),
        ])
        return reply(stream)

    out = tmp_path / "out"
    result = create_harness(f"docker-claude-host-{arm}:{skill}",
                            runner=_docker_cli(solver=fake_claude)).run(task, output_dir=out)

    cwd = seen["cwd"]
    assert out not in cwd.parents and tmp_path / "content" not in cwd.parents
    assert seen["env"]["HOME"] == str(cwd.parent / "home")
    assert "GITHUB_TOKEN" not in seen["env"]
    assert seen["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"  # the operator's settings env block
    assert "--tools" in seen["cmd"] and "--allowedTools" not in seen["cmd"]
    # The skill is a plugin beside the work tree, never inside it.
    assert seen["skill_seen"] is True and seen["manifest"]["name"] == "demo"
    assert cwd not in seen["plugin"].parents
    prompt = seen["cmd"][seen["cmd"].index("-p") + 1]
    # The discovery arm installs the same plugin and only drops the notice.
    notice = arm == "skill"
    assert prompt.startswith("A skill named `demo:demo` is installed." if notice
                             else "Solve this coding task.")
    assert ("demo" in prompt.split("Solve this coding task.")[0]) is notice
    assert result.metrics["skill_notice"] is notice
    assert seen["cmd"][seen["cmd"].index("--output-format") + 1] == "stream-json"
    # stream-json under -p exits 1 with "requires --verbose" without it.
    assert "--verbose" in seen["cmd"]
    assert seen["stdin"] is None  # the runner closes stdin
    assert result.metrics["treatment"] == {"skill": "demo", "plugin": "demo",
                                           "delivery": "plugin-dir"}
    assert result.metrics["n_skill_invocations"] == 1
    assert result.metrics["tool_calls"] == {"Skill": 1, "Edit": 1}
    assert result.metrics["cost_usd"] == 0.01
    assert json.loads((out / "solver.json").read_text())["type"] == "result"
    # The tree is kept with the run, and the scratch root is gone.
    assert (out / "work" / "main.cpp").read_text(encoding="utf-8") == "fixed"
    assert not list((out / "work").rglob("SKILL.md"))
    assert not cwd.parent.exists()
    assert result.metrics["isolation"]["network"] == "open"


def test_probe_reports_treatment_n_a_when_nothing_is_declared(tmp_path):
    loose = tmp_path / "loose-task"
    loose.mkdir()
    ws = AgentWorkspace.create(forbidden=default_forbidden(loose))
    result = run_probe(ws, {"PATH": "/usr/bin:/bin"})
    ws.cleanup()
    assert result["treatment"] == "n/a"  # not a "closed" it never tested
    assert probe_reward({**{k: "closed" for k in LEAK_CLASSES}, "treatment": "n/a"}) == 1.0


def test_probe_with_a_skill_forbids_it_even_outside_the_task_checkout(tmp_path, monkeypatch):
    elsewhere = tmp_path / "other-tree" / "skills" / "demo"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text("x", encoding="utf-8")
    loose = tmp_path / "loose-task"
    for sub in ("tests", "environment", "solution"):
        (loose / sub).mkdir(parents=True)
    (loose / "instruction.md").write_text("x", encoding="utf-8")
    (loose / "task.toml").write_text("", encoding="utf-8")
    result = create_harness(f"docker-probe-host:{elsewhere / 'SKILL.md'}", runner=_docker_cli()).run(
        load_task(loose), output_dir=tmp_path / "out")
    assert result.metrics["isolation_probe"]["treatment"] == "open"


def test_a_timed_out_solve_keeps_the_agent_tree(tmp_path, monkeypatch):
    task = load_task(_content_checkout(tmp_path))

    def slow_claude(call):
        (call.cwd / "half-done.cpp").write_text("partial", encoding="utf-8")
        return reply(timed_out=True)

    out = tmp_path / "out"
    result = create_harness("docker-claude-host", runner=_docker_cli(solver=slow_claude)).run(
        task, output_dir=out)
    assert result.returncode == 124
    assert (out / "work" / "half-done.cpp").read_text(encoding="utf-8") == "partial"


def _alive(pid: int) -> bool:
    """True while ``pid`` runs; an unreaped zombie is not running."""
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:  # no /proc (macOS): kill(0) succeeded
        return sys.platform != "linux"
    except OSError:
        return False


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands for claude")
def test_a_timed_out_host_agent_leaves_none_of_its_tools_running(tmp_path, monkeypatch):
    """docker-claude-host: a timeout kills claude's whole process tree. On main only
    claude was killed, and a tool process it had started ran on after the cell."""
    task = load_task(_content_checkout(tmp_path))
    pid_file = tmp_path / "tool.pid"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"  # resolved first on PATH; the real one is never reached
    fake.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "tool = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(str(tool.pid))\n"
        "time.sleep(60)\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    cli = _docker_cli()
    cli.real = ("sh", "claude")

    result = create_harness("docker-claude-host", runner=cli).run(
        task, output_dir=tmp_path / "out", timeout_sec=1.5)

    assert result.returncode == 124 and result.reward == 0.0
    assert [Path(c.argv[0]).name for c in cli.solver_calls()] == ["claude"]
    tool = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(tool) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(tool), "the agent's tool process outlived the cell"


def _run_fake_claude(tmp_path, monkeypatch, stdout, *, edit=False, fail_copy_back=False,
                     stderr=""):
    task = load_task(_content_checkout(tmp_path))

    def copy_back(call):
        if fail_copy_back and call.argv[1] == "cp" and call.argv[3].endswith(":/app/"):
            return reply(stderr="copy back failed", returncode=1)
        return None

    def claude(call):
        if edit:
            (call.cwd / "fixed.cpp").write_text("the fix", encoding="utf-8")
        return reply(stdout, stderr)

    cli = _docker_cli(solver=claude, answer=copy_back)
    return create_harness("docker-claude-host", runner=cli).run(task, output_dir=tmp_path / "out")


def test_a_crash_with_no_result_after_real_edits_is_scored(tmp_path, monkeypatch):
    result = _run_fake_claude(tmp_path, monkeypatch, "", edit=True)
    assert "solver_error" not in result.metrics
    assert result.metrics["terminal_reason"] == "no_result"
    assert result.passed is True


def test_a_crash_with_no_result_and_an_untouched_tree_is_a_solver_error(tmp_path, monkeypatch):
    result = _run_fake_claude(tmp_path, monkeypatch, "")
    assert result.metrics["solver_error"] == "no result object on stdout"


def test_a_failed_copy_back_keeps_the_model_and_a_failing_returncode(tmp_path, monkeypatch):
    obj = {"type": "result", "subtype": "success", "num_turns": 3, "total_cost_usd": 0.1,
           "usage": {"output_tokens": 50}, "modelUsage": {"claude-sonnet-4-5": {"costUSD": 0.1}}}
    result = _run_fake_claude(tmp_path, monkeypatch, json.dumps(obj), fail_copy_back=True)
    assert result.metrics["error"] == "RuntimeError: docker cp failed (exit 1); see run.log"
    assert result.metrics["model"] == "claude-sonnet-4-5" and result.metrics["cost_usd"] == 0.1
    assert result.returncode == 1 and result.reward is None


# ── review round on #70 ─────────────────────────────────────────────────────


def test_a_relative_probe_skill_path_is_resolved(tmp_path, monkeypatch):
    skill = tmp_path / "cwd" / "myskill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "cwd")
    loose = tmp_path / "loose-task"
    for sub in ("tests", "environment", "solution"):
        (loose / sub).mkdir(parents=True)
    (loose / "instruction.md").write_text("x", encoding="utf-8")
    (loose / "task.toml").write_text("", encoding="utf-8")
    result = create_harness("docker-probe-host:myskill", runner=_docker_cli()).run(
        load_task(loose), output_dir=tmp_path / "out")
    assert result.metrics["isolation_probe"]["treatment"] == "open"


def test_probe_handles_paths_with_spaces(tmp_path):
    spaced = tmp_path / "a b" / "sol"
    spaced.mkdir(parents=True)
    (spaced / "solve.sh").write_text("x", encoding="utf-8")
    ws = AgentWorkspace.create(forbidden={"task_files": [spaced]})
    result = run_probe(ws, {"PATH": "/usr/bin:/bin"})
    ws.cleanup()
    assert result["task_files"] == "open"


def test_operator_settings_env_block_is_merged_over_the_environment(tmp_path, caplog):
    config = tmp_path / "cfg"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"env": {
        "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2", "SOME_TOKEN": "x"}}), encoding="utf-8")
    merged = sandbox.operator_source_env({"CLAUDE_CONFIG_DIR": str(config), "AWS_REGION": "us-east-2"})
    assert merged["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert merged["AWS_REGION"] == "us-west-2"  # settings win, as in Claude Code itself
    (config / "settings.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert sandbox.operator_source_env({"CLAUDE_CONFIG_DIR": str(config)}) == {
            "CLAUDE_CONFIG_DIR": str(config)}
    assert "settings.json" in caplog.text
    env = agent_env(merged)
    assert "CLAUDE_CODE_USE_BEDROCK" in env and "SOME_TOKEN" not in env
    assert sandbox.operator_source_env({"CLAUDE_CONFIG_DIR": str(tmp_path / "none")}) == {
        "CLAUDE_CONFIG_DIR": str(tmp_path / "none")}


def test_scratch_root_is_removed_when_the_snapshot_fails(tmp_path, monkeypatch):
    failing = DockerCLI(answer=lambda call: reply(stderr="docker cp failed", returncode=1))
    task = load_task(_content_checkout(tmp_path))
    with pytest.raises(RuntimeError):
        create_harness("docker-probe-host", runner=failing)._prepare_workspace(task, "c1", log=io.StringIO())
    assert not list((tmp_path / "scratch").iterdir())


# ── review round 4 on #70 ───────────────────────────────────────────────────


def _probe_under_parent(tmp_path, parent_env: dict, deny_source: dict) -> dict:
    """Run the probe as the child of an ``sh`` whose environ is *parent_env*; the
    probe itself gets none of it, like an agent under a harness that holds a token."""
    ws = AgentWorkspace.create()
    env = ws.env({"PATH": "/usr/bin:/bin"})
    deny = sandbox.env_fingerprints(deny_source, sorted(deny_source))
    env.update(sandbox.probe_env(ws.forbidden, deny, _CLOSED_URL))
    names = " ".join(f"-u {k}" for k in parent_env)
    proc = subprocess.run(
        ["env", *(f"{k}={v}" for k, v in parent_env.items()), "sh", "-c",
         f'env {names} sh -c "$0"; :', sandbox.probe_script()],
        cwd=str(ws.work), env=env, text=True, capture_output=True, timeout=60, check=False)
    ws.cleanup()
    return parse_probe_output(proc.stdout)


@pytest.mark.skipif(not Path("/proc/self/environ").exists(), reason="needs /proc")
def test_probe_finds_a_dropped_value_in_a_parent_environ(tmp_path):
    secret = {"DOKIMI_TEST_SENTINEL": "SENTINEL_pr70_proof"}
    assert _probe_under_parent(tmp_path, secret, secret)["env"] == "open"
    # Same name, another value (an image that sets it itself): no host value leaked.
    other = _probe_under_parent(tmp_path, {"DOKIMI_TEST_SENTINEL": "image-default"}, secret)
    assert other["env"] == "closed"


def test_probe_env_is_n_a_when_the_allowlist_drops_nothing(tmp_path):
    ws = AgentWorkspace.create()
    result = run_probe(ws, {"PATH": "/usr/bin:/bin"})
    ws.cleanup()
    assert result["env"] == "n/a"


def test_env_fingerprints_match_posix_cksum():
    if not shutil.which("cksum"):
        pytest.skip("no cksum")
    out = subprocess.run(["cksum"], input=b"tok-123", capture_output=True, check=True).stdout.split()
    assert sandbox.env_fingerprints({"T": "tok-123\nsecond line", "E": ""}, ["T", "E"]) == [
        f"T:{out[0].decode()}:{out[1].decode()}"]


def test_parse_probe_output_defaults_a_missing_class_to_untested():
    partial = json.dumps({k: "closed" for k in LEAK_CLASSES if k != "env"})
    result = parse_probe_output(partial)
    assert result["env"] == "untested" and probe_reward(result) == 0.0


def test_allowlist_keeps_the_provider_variables_the_cli_reads():
    names = ["AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "CLAUDE_CODE_CLIENT_CERT", "CLAUDE_CODE_CLIENT_KEY",
             "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
             "AWS_CONTAINER_CREDENTIALS_FULL_URI", "NODE_OPTIONS", "VERTEX_REGION_CLAUDE_4_5_SONNET",
             "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
             "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_GATEWAY", "ANTHROPIC_AWS_WORKSPACE_ID",
             "ANTHROPIC_GOOGLE_CLOUD_PROJECT", "ANTHROPIC_GOOGLE_CLOUD_LOCATION",
             "ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID", "ANTHROPIC_DEFAULT_FABLE_MODEL"]
    assert set(agent_env({n: "x" for n in names})) == set(names) | set(sandbox._SEAL_ENV)


def test_isolation_states_what_the_host_launch_leaves_readable():
    iso = AgentWorkspace(root=Path("/nonexistent")).isolation()
    assert "/proc" in iso["env"]
    assert "env block" in iso["operator_config"]
    assert "nested claude" in iso["tools"]


def test_default_forbidden_adds_claude_config_dir_and_the_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    forbidden = default_forbidden(tmp_path / "t", output_dir=tmp_path / "runs" / "t" / "docker-claude")
    assert tmp_path / "cfg" in forbidden["operator_config"]
    assert tmp_path / "cfg" / ".claude.json" in forbidden["operator_config"]
    assert tmp_path / "runs" / "t" in forbidden["task_files"]


def test_the_output_entry_is_the_absolute_task_dir_for_every_repeat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for rel in ("results/demo/docker-probe", "results/demo/docker-probe/run2"):
        entry = default_forbidden(tmp_path / "t", output_dir=Path(rel))["task_files"][-1]
        assert entry == tmp_path / "results" / "demo"


def test_default_forbidden_finds_skills_without_a_git_checkout(tmp_path):
    root = tmp_path / "tarball"
    (root / "skills" / "demo").mkdir(parents=True)
    task = root / "tasks" / "t"
    task.mkdir(parents=True)
    assert default_forbidden(task)["treatment"] == [root / "skills"]


def test_an_inner_skills_dir_does_not_hide_the_content_repos(tmp_path):
    repo = tmp_path / "repo"
    for rel in ("skills/real", "evaluation/skills/tooling", "evaluation/tasks/t/skills"):
        (repo / rel).mkdir(parents=True)
    treatment = default_forbidden(repo / "evaluation" / "tasks" / "t")["treatment"]
    assert set(treatment) == {repo / "skills", repo / "evaluation" / "skills",
                              repo / "evaluation" / "tasks" / "t" / "skills"}


@pytest.mark.parametrize("harness", ["docker-probe:", "docker-probe:/nonexistent/skill",
                                     "docker-claude-skill:/nonexistent/SKILL.md"])
def test_registry_rejects_a_missing_or_empty_skill_path(harness):
    with pytest.raises(ValueError):
        create_harness(harness)


def test_a_probe_timeout_kills_the_hung_child_not_only_the_shell(tmp_path, monkeypatch):
    marker = tmp_path / "child-survived"
    # Plain sh and sleep: the probe env has no LD_LIBRARY_PATH, so a toolcache python may not start.
    monkeypatch.setattr(sandbox, "_PROBE_SH", '( sleep 4; : > "$DOKIMI_MARKER" ) & wait')
    ws = AgentWorkspace.create()
    with pytest.raises(subprocess.TimeoutExpired):
        run_probe(ws, {"PATH": "/usr/bin:/bin"}, extra_env={"DOKIMI_MARKER": marker.as_posix()}, timeout=1)
    ws.cleanup()
    time.sleep(5)
    assert not marker.exists()


def test_a_probe_timeout_records_every_class_untested(tmp_path, monkeypatch):
    hung = DockerCLI(answer=lambda call: reply(timed_out=True) if call.argv[0] == "sh" else None)
    out = tmp_path / "out"
    result = create_harness("docker-probe-host", runner=hung).run(load_task(_content_checkout(tmp_path)),
                                                                  output_dir=out)
    assert result.metrics["isolation_probe"] == {k: "untested" for k in LEAK_CLASSES}
    assert result.reward == 0.0 and result.returncode == 0
    assert json.loads((out / "isolation_probe.json").read_text(encoding="utf-8"))["env"] == "untested"
    assert not list((tmp_path / "scratch").iterdir())


def test_a_symlinked_skill_md_exposes_the_linking_dir(tmp_path):
    shared = tmp_path / "skills" / "shared"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("x", encoding="utf-8")
    foo = tmp_path / "skills" / "foo"
    foo.mkdir()
    (foo / "SKILL.md").symlink_to(shared / "SKILL.md")
    harness = create_harness(f"docker-claude-skill:{foo / 'SKILL.md'}")
    assert harness._resolve_skill_dir() == foo


def test_scratch_root_is_removed_when_the_skill_copy_fails(tmp_path, monkeypatch):
    import dokimi.harnesses.docker_solver as mod

    skill = tmp_path / "content" / "skills" / "demo"
    task = load_task(_content_checkout(tmp_path))

    def interrupted(src, dest):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "build_plugin", interrupted)
    with pytest.raises(KeyboardInterrupt):
        create_harness(f"docker-claude-skill:{skill}", runner=DockerCLI())._prepare_workspace(
            task, "c1", log=io.StringIO())
    assert not list((tmp_path / "scratch").iterdir())


def test_a_rerun_does_not_see_its_previous_tree_or_verdict(tmp_path, monkeypatch):
    task = load_task(_content_checkout(tmp_path))
    out = tmp_path / "out"
    (out / "work").mkdir(parents=True)
    (out / "work" / "notes.txt").write_text("previous attempt", encoding="utf-8")
    (out / "reward.txt").write_text("1.0", encoding="utf-8")
    seen: dict = {}

    def fake_claude(call):
        seen.update(work=(out / "work").exists(), reward=(out / "reward.txt").exists())
        return reply('{"num_turns": 1}')

    create_harness("docker-claude-host", runner=_docker_cli(solver=fake_claude, reward="0.0")).run(
        task, output_dir=out)
    assert seen == {"work": False, "reward": False}


def test_a_failed_copy_back_keeps_the_paid_solve_metrics(tmp_path, monkeypatch):
    task = load_task(_content_checkout(tmp_path))
    paid = json.dumps({"type": "result", "num_turns": 5, "total_cost_usd": 1.25,
                       "usage": {"input_tokens": 10, "output_tokens": 20}})
    cli = _docker_cli(solver=paid, answer=lambda call: reply(returncode=1) if (
        call.argv[1] == "cp" and call.argv[-1].endswith(":/app/")) else None)
    result = create_harness("docker-claude-host", runner=cli).run(task, output_dir=tmp_path / "out")
    assert result.reward is None and result.passed is False
    assert "docker cp failed" in result.metrics["error"]
    assert result.metrics["cost_usd"] == 1.25
    assert result.metrics["isolation"]["agent_location"] == "host"


def test_a_skill_path_to_another_file_is_refused(tmp_path):
    skill = tmp_path / "skills" / "foo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x", encoding="utf-8")
    (skill / "README.md").write_text("x", encoding="utf-8")
    assert create_harness(f"docker-claude-skill:{skill / 'SKILL.md'}")._resolve_skill_dir() == skill
    with pytest.raises(ValueError, match="skill directory or its SKILL.md"):
        create_harness(f"docker-claude-skill:{skill / 'README.md'}")._resolve_skill_dir()


# The names agent_sandbox treats as secrets, spelled out so dropping one turns this red.
_CREDENTIAL_NAMES = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FOUNDRY_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE", "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
)
# Built, not spelled out, so no line of this file reads like a credential assignment. Two values
# are shaped on purpose: a multi-line header and a quote plus backslash, which JSON escapes.
_SHAPED = {
    "ANTHROPIC_CUSTOM_HEADERS": chr(10).join(f"X-Fake-{c}: hdr{c * 4}{n}" for c, n in (("A", 1111), ("B", 2222))),
    "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE": 'pass"phrase' + chr(92) + "0006",
}
_SECRETS = {name: _SHAPED.get(name, f"{name.lower()}-{i:04d}") for i, name in enumerate(_CREDENTIAL_NAMES, 1)}


def _pieces():
    return [piece for value in _SECRETS.values() for piece in value.splitlines()]


def _leaky_stream():
    printed = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": json.dumps(_SECRETS)}]}})
    return "\n".join([printed, json.dumps({"type": "result", "num_turns": 2,
                                           "result": " ".join(_SECRETS.values())})])


def test_a_host_solve_redacts_every_credential_variable_everywhere_it_saves_output(
        tmp_path, monkeypatch):
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    _run_fake_claude(tmp_path, monkeypatch, _leaky_stream(), stderr=" ".join(_SECRETS.values()))
    out = tmp_path / "out"
    for name in ("solver.jsonl", "solver.json", "run.log"):
        saved = (out / name).read_text(encoding="utf-8")
        assert [p for p in _pieces() if p in saved] == [], name
    assert "[REDACTED]" in (out / "solver.jsonl").read_text(encoding="utf-8")


def test_a_timed_out_host_solve_redacts_the_stream_it_kept(tmp_path, monkeypatch):
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    out = tmp_path / "out"
    cli = _docker_cli(solver=lambda call: reply(_leaky_stream(), timed_out=True))
    result = create_harness("docker-claude-host", runner=cli).run(
        load_task(_content_checkout(tmp_path)), output_dir=out)
    assert result.returncode == 124 and "solver_timeout" in result.metrics
    saved = (out / "solver.jsonl").read_text(encoding="utf-8")
    assert saved and [p for p in _pieces() if p in saved] == []


def test_state_sees_a_file_the_agent_writes_under_a_dot_skill_dir():
    ws = AgentWorkspace.create()
    try:
        before = ws.state()
        (ws.work / ".skill").mkdir()
        (ws.work / ".skill" / "x.txt").write_text("x", encoding="utf-8")
        assert ws.state() != before
    finally:
        ws.cleanup()


def test_run_probe_timeout_raises_and_runs_with_exactly_the_agent_env(tmp_path, monkeypatch):
    """A hung probe check showed nothing closed: the caller records every class untested."""
    from dokimi import process

    monkeypatch.setenv("PARENT_ONLY_SECRET", "x")  # in the parent, not in the agent's env
    fake = process.FakeRunner(lambda call: process.reply('{"env": "closed"}', timed_out=True))
    ws = AgentWorkspace.create()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_probe(ws, {"PATH": "/usr/bin:/bin", "DOKIMI_TEST_ONLY_TOKEN": "ghp_secret"},
                      timeout=5, runner=fake)
    finally:
        ws.cleanup()
    (call,) = fake.calls
    assert call.argv[:2] == ("sh", "-c") and call.timeout == 5 and call.cwd == ws.work
    assert "DOKIMI_TEST_ONLY_TOKEN" not in call.env and call.env["PATH"] == "/usr/bin:/bin"
    assert "PARENT_ONLY_SECRET" not in call.env


def test_missing_raw_socket_tools_cannot_certify_network_closed(tmp_path):
    # Keep real host tools except timeout: deterministic on Linux and macOS.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("sh", "bash", "dirname", "curl", "id", "sed", "tr", "head", "cksum", "ls"):
        if path := shutil.which(name, path="/usr/bin:/bin"):
            (bindir / name).symlink_to(path)
    if not (bindir / "curl").exists() or (bindir / "timeout").exists():
        pytest.skip("needs curl and no timeout in /usr/bin:/bin")
    result = _probe_script_run(tmp_path, PATH=str(bindir), PROBE_DNS_NAME="127.0.0.1")
    assert result["network"] == "untested"
    assert result["docker_api"] == "untested"
    assert probe_reward(result) == 0.0


def test_missing_raw_socket_tools_keep_a_proven_open_network(tmp_path):
    # Without bash and timeout only "closed" may weaken to "untested": a fetch that got through stays open.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("sh", "dirname", "curl", "id", "sed", "tr", "head", "cksum", "ls"):
        if path := shutil.which(name, path="/usr/bin:/bin"):
            (bindir / name).symlink_to(path)
    if not (bindir / "curl").exists():
        pytest.skip("needs curl in /usr/bin:/bin")
    with subprocess.Popen(
        [sys.executable, "-u", "-m", "http.server", "0", "--bind", "127.0.0.1"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=tmp_path,
    ) as server:
        try:
            port = server.stdout.readline().split(" port ")[1].split()[0]
            result = _probe_script_run(tmp_path, PATH=str(bindir), PROBE_DNS_NAME="127.0.0.1",
                                       PROBE_URL=f"http://127.0.0.1:{port}/")
        finally:
            server.terminate()
            server.wait(timeout=10)
    assert result["network"] == "open"

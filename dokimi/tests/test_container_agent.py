# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the in-container agent: the egress proxy and the sandbox lifecycle.

The proxy tests use real sockets on loopback only. The harness tests replace
``docker`` with a recorder, so no container is started.
"""

from __future__ import annotations

import io
import json
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

import dokimi.harnesses.agent_sandbox as sandbox_mod
import dokimi.harnesses.container_agent as container_mod
from dokimi.harnesses import load_task
from dokimi.harnesses.agent_sandbox import AgentWorkspace, run_probe
from dokimi.harnesses.container_agent import AGENT_HOME, AGENT_UID, FILES_DIR, ContainerSandbox
from dokimi.harnesses.docker_solver import DockerSolverHarness
from dokimi.harnesses.egress_proxy import (
    EgressProxy,
    _upstream,
    llm_hosts,
    loopback_base_urls,
    normalize_entry,
    parse_log,
)
from dokimi.harnesses.registry import create_harness
from dokimi.process import reply
from docker_cli_fake import DockerCLI


@pytest.fixture(autouse=True)
def _no_developer_env(tmp_path, monkeypatch):
    """The lifecycle tests read the env and settings through operator_source_env(),
    then run the preflight: a developer's own proxy, gateway or provider must not
    change them, and their real token must not land in a dokimi-env-* file."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-operator-config"))
    for name in list(__import__("os").environ):
        if name.lower() in ("http_proxy", "https_proxy", "no_proxy", "all_proxy") or name.startswith(
                ("ANTHROPIC_", "CLAUDE_CODE_", "AWS_", "CLOUD_ML_", "GOOGLE_", "VERTEX_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _probe_dns_stays_local(monkeypatch):
    """A numeric name needs no resolver at all, so no test sends a query out (and
    none waits on an AAAA lookup of "localhost" through the search domains)."""
    monkeypatch.setattr(sandbox_mod, "PROBE_DNS_NAME", "127.0.0.1")


def _echo_server() -> tuple[socket.socket, int]:
    """A loopback server that echoes one message per connection."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            data = conn.recv(1024)
            conn.sendall(b"echo:" + data)
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


def _connect(proxy: EgressProxy, target: str) -> tuple[bytes, socket.socket]:
    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)
        if not chunk:
            break
        head += chunk
    return head, sock


# ── egress proxy ────────────────────────────────────────────────────────────


def test_proxy_tunnels_an_allowed_host():
    srv, port = _echo_server()
    proxy = EgressProxy(bind="127.0.0.1", allow=[f"127.0.0.1:{port}"]).start()
    try:
        head, sock = _connect(proxy, f"127.0.0.1:{port}")
        assert b" 200 " in head
        sock.sendall(b"ping")
        assert sock.recv(64) == b"echo:ping"
        sock.close()
    finally:
        proxy.stop()
        srv.close()
    assert [r["decision"] for r in proxy.log] == ["allowed"]


def test_proxy_refuses_a_host_off_the_allowlist_and_plain_http():
    proxy = EgressProxy(bind="127.0.0.1", allow=["bedrock-runtime.us-east-2.amazonaws.com"]).start()
    try:
        head, sock = _connect(proxy, "example.com:443")
        sock.close()
        assert head.startswith(b"HTTP/1.1 403")
        plain = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        plain.sendall(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        assert plain.recv(64).startswith(b"HTTP/1.1 403")
        plain.close()
    finally:
        proxy.stop()
    assert {(r["host"], r["decision"]) for r in proxy.log} == {
        ("example.com", "denied")}
    assert len(proxy.log) == 2


def test_proxy_chains_allowed_tunnels_through_the_host_proxy():
    srv, port = _echo_server()
    seen: list[bytes] = []
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen()

    def fake_upstream():  # answer CONNECT, then forward one message to the echo server
        conn, _ = upstream.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(1)
        seen.append(head.split(b"\r\n", 1)[0])
        conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
        with socket.create_connection(("127.0.0.1", port)) as target:
            target.sendall(conn.recv(64))
            conn.sendall(target.recv(64))
        conn.close()

    threading.Thread(target=fake_upstream, daemon=True).start()
    env = {"HTTPS_PROXY": f"http://127.0.0.1:{upstream.getsockname()[1]}"}
    proxy = EgressProxy(bind="127.0.0.1", allow=["llm.example"], upstream_env=env).start()
    try:
        head, sock = _connect(proxy, "llm.example:443")
        assert b" 200 " in head
        sock.sendall(b"hi")
        assert sock.recv(64) == b"echo:hi"
        sock.close()
    finally:
        proxy.stop()
        srv.close()
        upstream.close()
    assert seen == [b"CONNECT llm.example:443 HTTP/1.1"]


def test_upstream_honours_no_proxy_suffixes():
    env = {"HTTPS_PROXY": "http://proxy.corp:912", "NO_PROXY": "localhost,.intel.com"}
    assert _upstream(env, "bedrock-runtime.us-east-2.amazonaws.com") == ("proxy.corp", 912)
    assert _upstream(env, "api.intel.com") is None
    assert _upstream({}, "api.anthropic.com") is None


def test_llm_hosts_follow_the_provider():
    assert llm_hosts({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-2"}) == [
        "bedrock-runtime.us-east-2.amazonaws.com:443", "bedrock.us-east-2.amazonaws.com:443"]
    assert llm_hosts({}) == ["api.anthropic.com:443"]
    assert llm_hosts({"ANTHROPIC_BASE_URL": "https://gw.corp:8443/v1"}) == [
        "api.anthropic.com:443", "gw.corp:8443"]
    assert llm_hosts({"ANTHROPIC_BASE_URL": "http://litellm.internal/"}) == [
        "api.anthropic.com:443", "litellm.internal:80"]
    assert {"us-east5-aiplatform.googleapis.com:443"} <= set(llm_hosts({"CLAUDE_CODE_USE_VERTEX": "1"}))


# ── probe: raw-socket network checks ────────────────────────────────────────


def test_probe_finds_a_proxy_that_tunnels_anything(tmp_path, monkeypatch):
    """No curl needed: a CONNECT over bash /dev/tcp to an open proxy is a leak."""
    monkeypatch.setattr(sandbox_mod.tempfile, "tempdir", str(tmp_path))
    open_proxy = EgressProxy(bind="127.0.0.1", allow=["example.com"])
    open_proxy._open = lambda host, port: socket.create_connection(  # type: ignore[method-assign]
        ("127.0.0.1", _echo_server()[1]))
    open_proxy.start()
    try:
        ws = AgentWorkspace.create()
        result = run_probe(ws, {"PATH": "/usr/bin:/bin", "HTTPS_PROXY": open_proxy.url},
                           url="http://127.0.0.1:9/", direct="127.0.0.1 9")
        ws.cleanup()
    finally:
        open_proxy.stop()
    assert result["network"] == "open"


def test_probe_network_closed_behind_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_mod.tempfile, "tempdir", str(tmp_path))
    proxy = EgressProxy(bind="127.0.0.1", allow=["llm.example"]).start()
    try:
        ws = AgentWorkspace.create()
        result = run_probe(ws, {"PATH": "/usr/bin:/bin", "HTTPS_PROXY": proxy.url},
                           url="https://example.com/", direct="127.0.0.1 9")
        ws.cleanup()
    finally:
        proxy.stop()
    assert result["network"] == "closed"
    assert {r["host"] for r in proxy.log} == {"example.com"}


# ── sandbox ─────────────────────────────────────────────────────────────────


def _sandbox(tmp_path) -> ContainerSandbox:
    scratch = tmp_path / "envfiles"
    scratch.mkdir(exist_ok=True)
    return ContainerSandbox(network="dokimi-net-x", proxy_name="dokimi-egress-x", allow=["llm.example"],
                            binary=tmp_path / "claude", label="dokimi.run=x", _scratch=scratch)


def _exec_env(argv: list[str], client_env: dict | None = None) -> dict[str, str]:
    """The agent env a `docker exec --env-file` argv carries (plus `-e NAME` from the client)."""
    path = Path(argv[argv.index("--env-file") + 1])
    env = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())
    named = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    env.update({n: (client_env or {})[n] for n in named})
    return env


def test_sandbox_env_points_at_the_proxy_and_drops_host_paths(tmp_path):
    sb = _sandbox(tmp_path)
    env = sb.env({"PATH": "/host/bin", "HTTPS_PROXY": "http://corp:912", "NO_PROXY": "intel.com",
                  "AWS_BEARER_TOKEN_BEDROCK": "tok", "AWS_CONFIG_FILE": "/home/op/.aws/config",
                  "GITHUB_TOKEN": "ghp", "ALL_PROXY": "http://corp:8080", "ftp_proxy": "http://corp:8080"})
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://egress:3128"
    # Whatever the Docker client injected must not reach the agent either.
    assert {env[n] for n in ("ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy")} == {"http://egress:3128"}
    # The host's NO_PROXY is replaced, never dropped: Docker's own proxies.default
    # NO_PROXY would otherwise reach the agent and send an LLM host around egress.
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,127.0.0.1,::1"
    assert "PATH" not in env and "AWS_CONFIG_FILE" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == "tok"
    assert env["HOME"] == AGENT_HOME
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_exec_argv_carries_the_agent_env_in_a_private_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker:2375")
    env = {"AWS_BEARER_TOKEN_BEDROCK": "secret", "HOME": "/run/abhome",
           "HTTPS_PROXY": "http://egress:3128", "ANTHROPIC_CUSTOM_HEADERS": "A: 1\nB: 2"}
    argv, client_env = _sandbox(tmp_path).exec_argv("c1", env, ["true"])
    assert argv[:6] == ["docker", "exec", "-u", AGENT_UID, "-w", "/app"]
    assert "secret" not in " ".join(argv)
    envfile = Path(argv[argv.index("--env-file") + 1])
    assert oct(envfile.stat().st_mode & 0o777) == "0o600"
    assert _exec_env(argv, client_env) == env
    # The docker CLI keeps the operator's HOME, proxy and DOCKER_HOST; only the
    # multi-line value, which an env file cannot hold, travels by name.
    assert client_env.get("HOME") != "/run/abhome" and client_env["DOCKER_HOST"] == "tcp://docker:2375"
    assert client_env.get("HTTPS_PROXY") != "http://egress:3128"
    assert argv[argv.index("-e") + 1] == "ANTHROPIC_CUSTOM_HEADERS"
    assert client_env["ANTHROPIC_CUSTOM_HEADERS"] == "A: 1\nB: 2"


def test_run_args_put_the_container_on_the_internal_network_with_no_mounts(tmp_path):
    args = _sandbox(tmp_path).run_args()
    assert args[args.index("--network") + 1] == "dokimi-net-x"
    assert "-v" not in args  # a bind mount breaks on a Docker-in-Docker runner
    assert any(a.startswith("/run/abhome:") for a in args)  # the image's /tmp is left alone
    assert not any(a.startswith("/tmp:") for a in args)  # nosec B108 - asserts /tmp is not mounted
    assert args[args.index("--label") + 1] == "dokimi.run=x"


# ── harness lifecycle (docker recorded, not run) ────────────────────────────


def _task(tmp_path: Path, *, public: bool = False, egress: str = "") -> Path:
    task = tmp_path / "content" / "tasks" / "demo-task"
    (task / "tests").mkdir(parents=True)
    (task / "solution").mkdir()
    (task / "solution" / "solve.sh").write_text("true", encoding="utf-8")
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("Fix it.", encoding="utf-8")
    env = ['network_mode = "public"'] if public else []
    if egress:
        env.append(f'egress = ["{egress}"]')
    (task / "task.toml").write_text("[environment]\n" + "\n".join(env) + "\n", encoding="utf-8")
    return task


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The docker CLI and the in-container claude; the sidecar denies one CONNECT."""
    monkeypatch.setattr(container_mod, "claude_binary", lambda: tmp_path / "claude")
    return DockerCLI(sidecar_log=json.dumps(
        {"t": 1.0, "host": "evil.example", "port": 443, "decision": "denied", "reason": "x"}))


@pytest.fixture
def docker_calls(cli):
    """Each docker call's argv after ``docker``, in order."""
    return cli.args


def _fake_claude(cli: DockerCLI, seen: dict):
    def solve(call):
        seen["argv"], seen["env"], seen["stdin"] = list(call.argv), call.env, call.input
        return reply(seen.get("stream", '{"num_turns": 1}'))

    cli.solver = solve


def _probe_answer(seen: dict, out: str):
    """The in-container probe prints ``out``; ``seen`` gets its argv and agent env."""
    def answer(call):
        if not DockerCLI.is_probe(call.argv):
            return None
        seen.update(argv=list(call.argv), env=_exec_env(list(call.argv), call.env))
        return reply(out)

    return answer


def test_container_solve_lifecycle(tmp_path, cli, docker_calls, monkeypatch):
    seen: dict = {}
    _fake_claude(cli, seen)
    task = load_task(_task(tmp_path, egress="pypi.org"))
    out = tmp_path / "out"
    result = create_harness("docker-claude", runner=cli).run(task, model="haiku", output_dir=out)

    verbs = [c[:2] for c in docker_calls]
    assert ["network", "create"] in verbs and "--internal" in docker_calls[verbs.index(["network", "create"])]
    sidecar = next(c for c in docker_calls if c[0] == "create")
    assert sidecar[sidecar.index("--network") + 1] == "bridge"
    assert sidecar[sidecar.index("--allow") + 1].endswith(",pypi.org:443")
    connect = next(c for c in docker_calls if c[:2] == ["network", "connect"])
    assert connect[2:4] == ["--alias", "egress"]
    run_call = next(c for c in docker_calls if c[0] == "run")
    assert run_call[run_call.index("--network") + 1].startswith("dokimi-net-")
    assert any(c[0] == "cp" and c[-1].endswith(":/opt/dokimi-agent/claude") for c in docker_calls)
    assert seen["argv"][:4] == ["docker", "exec", "-u", AGENT_UID]
    assert "/opt/dokimi-agent/claude" in seen["argv"] and "--tools" in seen["argv"]
    # Whatever the agent backgrounded is killed before the snapshot and the verifier's /tests.
    reap = next(i for i, c in enumerate(docker_calls)
                if c[:3] == ["exec", "-u", AGENT_UID] and "kill -9 -1" in c[-1])
    snapshot = next(i for i, c in enumerate(docker_calls) if c[0] == "cp" and c[1].endswith(":/app/."))
    assert reap < snapshot
    # Copied credential/CA files are gone before the verifier runs the agent's code.
    wipe = next(i for i, c in enumerate(docker_calls) if c[-3:] == ["rm", "-rf", FILES_DIR])
    assert reap < wipe and docker_calls[wipe][:3] == ["exec", "-u", "root"]
    # Network removed last; the task container never joins bridge when not public.
    assert docker_calls[-1][:2] == ["network", "rm"]
    assert not any(c[:3] == ["network", "connect", "bridge"] for c in docker_calls)
    assert result.metrics["isolation"]["agent_location"] == "container"
    network = result.metrics["isolation"]["network"]
    assert {"pypi.org:443"} <= set(network.removeprefix("internal; egress proxy to ").split(","))
    assert result.metrics["egress"] == {"status": "violated", "allowed": [], "denied": ["evil.example"],
                                        "errors": [], "denied_entries": ["evil.example:443"]}
    assert json.loads((out / "egress.jsonl").read_text(encoding="utf-8"))["host"] == "evil.example"


def test_a_timed_out_sidecar_log_is_unknown_egress_not_none(tmp_path, cli, docker_calls, monkeypatch):
    """A teardown ``docker logs`` that times out leaves an empty log. The cell still
    passes (close() swallows it by design), but the egress record must say unknown,
    not an empty summary that reads as "no requests"."""
    _fake_claude(cli, {})
    logs_calls = []

    def answer(call):
        if call.argv[:2] == ("docker", "logs"):
            logs_calls.append(call)
            # The first logs is the start-up readiness poll; the second is stop_proxy's.
            if len(logs_calls) == 2:
                return reply(timed_out=True)
        return None

    cli.answer = answer
    result = create_harness("docker-claude", runner=cli).run(
        load_task(_task(tmp_path, egress="pypi.org")), model="haiku", output_dir=tmp_path / "out")

    assert len(logs_calls) == 2
    assert result.reward == 1.0 and result.returncode == 0
    assert result.metrics["egress"]["status"] == "unknown"
    assert "egress is unknown" in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")


def test_container_skill_arm_installs_a_plugin_outside_app(tmp_path, cli, docker_calls, monkeypatch):
    seen: dict = {}
    _fake_claude(cli, seen)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Use for demo things.\n---\n# Demo\n",
        encoding="utf-8")
    task = load_task(_task(tmp_path))
    out = tmp_path / "out"
    create_harness(f"docker-claude-skill:{skill}", runner=cli).run(task, model="haiku", output_dir=out)

    def at(pred):
        return next(i for i, c in enumerate(docker_calls) if pred(c))

    mkdir = at(lambda c: c[-3:] == ["mkdir", "-p", "/opt/abskill"])
    cp_plugin = at(lambda c: c[0] == "cp" and c[-1].endswith(":/opt/abskill"))
    chmod = at(lambda c: c[-4:] == ["chmod", "-R", "u=rwX,go=rX", "/opt/abskill"])
    rm_plugin = at(lambda c: c[-3:] == ["rm", "-rf", "/opt/abskill"])
    # Copy the plugin's contents into a target that exists, so it never nests.
    assert docker_calls[cp_plugin][1].endswith("/plugin/.")
    assert mkdir < cp_plugin < chmod < rm_plugin
    # Gone before anything the verifier runs is copied in.
    tests_in = [i for i, c in enumerate(docker_calls) if c[0] == "cp" and c[-1].endswith(":/tests")]
    assert all(rm_plugin < i for i in tests_in)
    argv = seen["argv"]
    assert argv[argv.index("--plugin-dir") + 1] == "/opt/abskill"
    prompt = argv[argv.index("-p") + 1]
    assert prompt.startswith("A skill named `demo-skill:demo-skill` is installed.")
    assert "/app/.skill" not in " ".join(" ".join(c) for c in docker_calls)
    assert cp_plugin < rm_plugin
    assert not (out / "plugin").exists()   # the staged copy is not kept with the run


def _demo_skill(tmp_path, extra=""):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: demo-skill\ndescription: Use for demo things.\n{extra}---\n# Demo\n",
        encoding="utf-8")
    return skill


def _stream(*plugins, skill="demo-skill:demo-skill"):
    return "\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "skills": ["doctor"],
         "plugins": [{"name": p} for p in plugins]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Skill", "input": {"skill": skill}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a"}]}},
        {"type": "result", "num_turns": 2, "total_cost_usd": 0.01},
    ])


def test_container_skill_arm_counts_the_treatment_and_flags_one_not_offered(
        tmp_path, cli, docker_calls, monkeypatch):
    skill = _demo_skill(tmp_path)
    task = load_task(_task(tmp_path))
    seen: dict = {"stream": _stream("demo-skill")}
    _fake_claude(cli, seen)
    ok = create_harness(f"docker-claude-skill:{skill}", runner=cli).run(task, model="haiku",
                                                            output_dir=tmp_path / "ok")
    assert ok.metrics["n_skill_invocations"] == 1 and "solver_error" not in ok.metrics
    assert ok.metrics["treatment"]["plugin"] == "demo-skill"
    assert ok.metrics["offered_plugins"] == ["demo-skill"]
    assert "--verbose" in seen["argv"] and seen["stdin"] is None  # the runner closes stdin
    seen["stream"] = _stream()  # the plugin did not load
    bad = create_harness(f"docker-claude-skill:{skill}", runner=cli).run(task, model="haiku",
                                                             output_dir=tmp_path / "bad")
    assert bad.metrics["solver_error"].startswith("treatment not offered")
    base = create_harness("docker-claude", runner=cli).run(task, model="haiku", output_dir=tmp_path / "base")
    assert "n_skill_invocations" not in base.metrics and "treatment" not in base.metrics


def test_container_discover_arm_is_the_skill_arm_without_the_notice(
        tmp_path, cli, docker_calls, monkeypatch):
    skill = _demo_skill(tmp_path)
    task = load_task(_task(tmp_path))
    runs = {}
    for arm in ("skill", "skill-discover"):
        seen: dict = {"stream": _stream("demo-skill")}
        _fake_claude(cli, seen)
        result = create_harness(f"docker-claude-{arm}:{skill}", runner=cli).run(
            task, model="haiku", output_dir=tmp_path / f"out-{arm}")
        runs[arm] = (seen["argv"], result.metrics)
    (skill_argv, skill_m), (disc_argv, disc_m) = runs["skill"], runs["skill-discover"]
    prompt = disc_argv[disc_argv.index("-p") + 1]
    assert prompt.startswith("Solve this coding task.") and "demo-skill" not in prompt
    # Same claude argv otherwise: plugin dir, tools, settings; only the prompt differs.

    def claude_argv(argv):
        argv = argv[argv.index("/opt/dokimi-agent/claude"):]
        return argv[:argv.index("-p") + 1] + argv[argv.index("-p") + 2:]

    assert claude_argv(skill_argv) == claude_argv(disc_argv)
    assert disc_argv[disc_argv.index("--plugin-dir") + 1] == "/opt/abskill"
    assert skill_m["skill_notice"] is True and disc_m["skill_notice"] is False
    assert disc_m["treatment"] == skill_m["treatment"]
    assert disc_m["n_skill_invocations"] == 1 and "solver_error" not in disc_m
    assert disc_m["skill_listing"] == {"description_chars": len("Use for demo things."),
                                       "max_desc_chars": 1536, "budget_chars": 8000,
                                       "truncated": False}
    seen = {"stream": _stream()}  # the plugin did not load: no discovery to measure
    _fake_claude(cli, seen)
    bad = create_harness(f"docker-claude-skill-discover:{skill}", runner=cli).run(
        task, model="haiku", output_dir=tmp_path / "bad")
    assert bad.metrics["solver_error"].startswith("treatment not offered")


def test_a_discover_arm_warns_when_the_listing_cuts_the_description(
        tmp_path, cli, docker_calls, monkeypatch):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: demo-skill\ndescription: {'x' * 1500}\nwhen_to_use: {'y' * 100}\n---\n# D\n",
        encoding="utf-8")
    _fake_claude(cli, {"stream": _stream("demo-skill")})
    out = tmp_path / "out"
    result = create_harness(f"docker-claude-skill-discover:{skill}", runner=cli).run(
        load_task(_task(tmp_path)), model="haiku", output_dir=out)
    listing = result.metrics["skill_listing"]
    assert listing["truncated"] is True and listing["description_chars"] == 1500 + 3 + 100
    assert "first 1536" in (out / "run.log").read_text(encoding="utf-8")


def test_a_discover_arm_does_not_warn_when_the_listing_shows_the_whole_description(
        tmp_path, cli, docker_calls, monkeypatch):
    skill = _demo_skill(tmp_path)
    _fake_claude(cli, {"stream": _stream("demo-skill")})
    out = tmp_path / "out"
    result = create_harness(f"docker-claude-skill-discover:{skill}", runner=cli).run(
        load_task(_task(tmp_path)), model="haiku", output_dir=out)
    assert result.metrics["skill_listing"]["truncated"] is False
    assert "WARNING" not in (out / "run.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("make", ["missing", "readme", "outside-link", "fork", "link-loop"])
def test_a_bad_skill_fails_before_any_docker_step(tmp_path, cli, docker_calls, monkeypatch, make):
    skill = _demo_skill(tmp_path, extra="context: fork\n" if make == "fork" else "")
    path = skill
    if make == "missing":
        path = tmp_path / "nope"
    elif make == "readme":
        (skill / "README.md").write_text("x", encoding="utf-8")
        path = skill / "README.md"
    elif make == "outside-link":
        (skill / "repo").symlink_to("..")
    elif make == "link-loop":  # each link is inside the skill and not its ancestor
        (skill / "a").mkdir()
        (skill / "c").mkdir()
        try:
            (skill / "a" / "b").symlink_to("../c", target_is_directory=True)
            (skill / "c" / "d").symlink_to("../a", target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlinks here")
    _fake_claude(cli, {})
    with pytest.raises(ValueError):
        create_harness(f"docker-claude-skill:{path}", runner=cli).run(load_task(_task(tmp_path)), model="haiku",
                                                          output_dir=tmp_path / "out")
    assert docker_calls == []
    assert not (tmp_path / "out" / "plugin").exists()


def test_a_failed_plugin_copy_leaves_no_staged_plugin(tmp_path, cli, docker_calls, monkeypatch):
    skill = _demo_skill(tmp_path)
    cli.answer = lambda call: (reply(stderr="docker cp failed", returncode=1)
                               if call.argv[1] == "cp" and call.argv[-1].endswith(":/opt/abskill") else None)
    _fake_claude(cli, {})
    out = tmp_path / "out"
    harness = create_harness(f"docker-claude-skill:{skill}", runner=cli)
    with pytest.raises(RuntimeError):
        harness._install_plugin_in_container("c1", out, io.StringIO(), {})
    out.mkdir(exist_ok=True)
    assert not (out / "plugin").exists()


def test_container_baseline_gets_no_plugin_and_no_notice(tmp_path, cli, docker_calls, monkeypatch):
    seen: dict = {}
    _fake_claude(cli, seen)
    task = load_task(_task(tmp_path))
    create_harness("docker-claude", runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")
    argv = seen["argv"]
    assert "--plugin-dir" not in argv
    assert argv[argv.index("-p") + 1].startswith("Solve this coding task.")
    assert "Skill" in argv[argv.index("--tools") + 1].split(",")


def test_public_task_gets_bridge_only_after_the_agent(tmp_path, cli, docker_calls, monkeypatch):
    _fake_claude(cli, {})
    task = load_task(_task(tmp_path, public=True))
    create_harness("docker-claude", runner=cli).run(task, model="haiku", output_dir=tmp_path / "out")
    run_call = next(c for c in docker_calls if c[0] == "run")
    assert run_call[run_call.index("--network") + 1].startswith("dokimi-net-")
    joins = [i for i, c in enumerate(docker_calls) if c[:3] == ["network", "connect", "bridge"]]
    sidecar_gone = next(i for i, c in enumerate(docker_calls) if c[:2] == ["rm", "-f"]
                        and c[2].startswith("dokimi-egress-") and i > 3)
    assert len(joins) == 1 and joins[0] > sidecar_gone  # bridge only after the agent's proxy is gone


def test_a_container_cli_crash_records_no_result(tmp_path, cli, docker_calls, monkeypatch):
    cli.solver = lambda call: reply(stderr="Killed", returncode=137)
    result = create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert result.metrics["terminal_reason"] == "no_result"


def test_network_is_removed_when_the_container_never_starts(tmp_path, cli, docker_calls, monkeypatch):
    cli.answer = lambda call: reply(stderr="docker: Error response from daemon", returncode=125) if (
        call.argv[1] == "run") else None
    task = load_task(_task(tmp_path))
    result = create_harness("docker-probe", runner=cli).run(task, output_dir=tmp_path / "out")
    assert "docker run failed" in result.metrics["error"] and result.reward is None  # errored
    assert docker_calls[-1][:2] == ["network", "rm"]


def test_container_probe_runs_as_the_agent_with_container_paths_forbidden(tmp_path, cli, docker_calls,
                                                                          monkeypatch):
    seen: dict = {}
    cli.answer = _probe_answer(seen, json.dumps({k: "closed" for k in sandbox_mod.LEAK_CLASSES}))
    task = load_task(_task(tmp_path))
    result = create_harness("docker-probe", runner=cli).run(task, output_dir=tmp_path / "out")
    assert seen["argv"][:4] == ["docker", "exec", "-u", AGENT_UID]
    assert "/solution" in seen["env"]["PROBE_TASK_FILES"].split()
    assert result.reward == 1.0 and result.passed is True
    assert not any(c[0] == "cp" and c[-1].endswith(":/opt/dokimi-agent/claude") for c in docker_calls)


def test_a_hung_container_probe_is_untested_not_sealed(tmp_path, cli, docker_calls, monkeypatch):
    """The probe's docker exec has 120 s, as before; running out shows nothing closed."""
    cli.answer = lambda call: reply('{"task_files": "closed"}', timed_out=True) if cli.is_probe(call.argv) else None
    result = create_harness("docker-probe", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    probe = next(c for c in cli.calls if cli.is_probe(c.argv))
    assert probe.timeout == 120
    assert set(result.metrics["isolation_probe"].values()) == {"untested"}
    assert result.reward == 0.0 and result.returncode == 0  # a verdict, not a timed-out cell
    assert "[probe] TIMEOUT" in (tmp_path / "out" / "run.log").read_text(encoding="utf-8")


def test_parse_log_keeps_only_decision_rows():
    text = '{"ready": true, "port": 3128}\nnoise\n{"host": "a", "decision": "allowed"}\n{bad'
    assert parse_log(text) == [{"host": "a", "decision": "allowed"}]


def test_container_probe_skips_variables_the_image_sets(tmp_path, cli, docker_calls, monkeypatch):
    seen: dict = {}
    probe = _probe_answer(seen, "{}")

    def with_image_env(call):
        args = list(call.argv[1:])
        if args[:2] == ["image", "inspect"]:  # the image's own env
            assert args[-1] == "sha256:img"
            return reply('["DEBIAN_FRONTEND=noninteractive"]')
        if args[0] == "inspect" and "{{.Image}}" in args:
            return reply("sha256:img")
        if args[0] == "inspect" and "{{.State.Running}}" not in args:
            # the container's: the image's names plus what the client injected
            return reply('["DEBIAN_FRONTEND=noninteractive", "GITHUB_TOKEN=injected"]')
        return probe(call)

    cli.answer = with_image_env
    monkeypatch.setenv("DEBIAN_FRONTEND", "noninteractive")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp")
    create_harness("docker-probe", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    deny = [tok.split(":", 1)[0] for tok in seen["env"]["PROBE_ENV_DENY"].split()]
    assert "GITHUB_TOKEN" in deny and "DEBIAN_FRONTEND" not in deny


def test_container_timeout_reaps_and_keeps_the_tree(tmp_path, cli, docker_calls, monkeypatch):
    cli.solver = lambda call: reply(timed_out=True)
    out = tmp_path / "out"
    result = create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), model="haiku",
                                                 output_dir=out)
    assert result.returncode == 124
    reap = next(i for i, c in enumerate(docker_calls)
                if c[:3] == ["exec", "-u", AGENT_UID] and "kill -9 -1" in c[-1])
    snapshot = next(i for i, c in enumerate(docker_calls) if c[0] == "cp" and c[1].endswith(":/app/."))
    assert reap < snapshot and (out / "work").is_dir()
    assert not any(c[0] == "cp" and c[-1].endswith(":/tests") for c in docker_calls)


_LEAKED = {"ANTHROPIC_API_KEY": "box-key-0001",
           "ANTHROPIC_CUSTOM_HEADERS": "X-Fake-A: hdrSecretAAAA1111\nX-Fake-B: hdrSecretBBBB2222"}


def _leaked_stream():
    return "\n".join([
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content": json.dumps(_LEAKED)}]}}),
        json.dumps({"type": "result", "num_turns": 2, "result": " ".join(_LEAKED.values())})])


@pytest.mark.parametrize("timeout", [False, True])
def test_a_container_solve_redacts_the_credentials_it_saves(
        tmp_path, cli, docker_calls, monkeypatch, timeout):
    for name, value in _LEAKED.items():
        monkeypatch.setenv(name, value)
    if timeout:
        cli.solver = lambda call: reply(_leaked_stream(), timed_out=True)
    else:
        _fake_claude(cli, {"stream": _leaked_stream()})
    out = tmp_path / "out"
    create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), model="haiku", output_dir=out)
    saved = (out / "solver.jsonl").read_text(encoding="utf-8")
    assert "[REDACTED]" in saved
    assert [p for v in _LEAKED.values() for p in v.splitlines() if p in saved] == []
    if not timeout:  # the result event is kept as solver.json
        assert "hdrSecret" not in (out / "solver.json").read_text(encoding="utf-8")


def test_container_probe_with_a_skill_forbids_it(tmp_path, cli, docker_calls, monkeypatch):
    skill = tmp_path / "elsewhere" / "skills" / "demo"
    skill.mkdir(parents=True)
    seen: dict = {}
    cli.answer = _probe_answer(seen, "{}")
    create_harness(f"docker-probe:{skill}", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert str(skill) in seen["env"]["PROBE_TREATMENT"].split()
    assert not any(c[0] == "cp" and c[-1].endswith(":/app/.skill") for c in docker_calls)


# ── review sweep on #72: ports, plain HTTP, loopback ─────────────────────────


def test_allowlist_is_per_port():
    srv, port = _echo_server()
    proxy = EgressProxy(bind="127.0.0.1", allow=[f"127.0.0.1:{port}"]).start()
    try:
        head, sock = _connect(proxy, f"127.0.0.1:{port + 1 if port < 65535 else port - 1}")
        sock.close()
    finally:
        proxy.stop()
        srv.close()
    assert head.startswith(b"HTTP/1.1 403")
    assert normalize_entry("pypi.org") == "pypi.org:443"
    assert normalize_entry("Gw.Corp:4000") == "gw.corp:4000"


def _http_server() -> tuple[socket.socket, int, list[bytes]]:
    """A loopback HTTP server that records the request head it gets and answers 200."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    heads: list[bytes] = []

    def serve():
        conn, _ = srv.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(1)
        heads.append(head)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1], heads


def test_plain_http_to_an_allowed_gateway_is_forwarded_in_origin_form():
    srv, port, heads = _http_server()
    proxy = EgressProxy(bind="127.0.0.1", allow=[f"127.0.0.1:{port}"]).start()
    try:
        sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        sock.sendall(f"POST http://127.0.0.1:{port}/v1/messages?beta=true HTTP/1.1\r\n"
                     f"Host: 127.0.0.1:{port}\r\nConnection: keep-alive\r\n"
                     "Proxy-Connection: keep-alive\r\nContent-Length: 0\r\n\r\n".encode())
        reply = sock.recv(256)
        sock.close()
    finally:
        proxy.stop()
        srv.close()
    assert reply.startswith(b"HTTP/1.1 200")
    sent = heads[0].decode()
    assert sent.startswith("POST /v1/messages?beta=true HTTP/1.1\r\n")
    assert "Connection: close" in sent and "keep-alive" not in sent.lower()
    assert proxy.log[0]["decision"] == "allowed"


def test_plain_http_is_not_chained_through_an_upstream_proxy():
    env = {"HTTP_PROXY": "http://corp:912"}
    proxy = EgressProxy(bind="127.0.0.1", allow=["gw.internal:4000"], upstream_env=env).start()
    try:
        sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        sock.sendall(b"GET http://gw.internal:4000/ HTTP/1.1\r\nHost: gw.internal:4000\r\n\r\n")
        assert sock.recv(64).startswith(b"HTTP/1.1 403")
        sock.close()
    finally:
        proxy.stop()
    assert "NO_PROXY" in proxy.log[0]["reason"]
    assert _upstream({**env, "NO_PROXY": "gw.internal"}, "gw.internal", "http") is None


def test_a_loopback_base_url_fails_fast(tmp_path, cli, docker_calls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:4000")
    assert loopback_base_urls({"ANTHROPIC_BASE_URL": "http://localhost:4000"})
    assert not loopback_base_urls({"ANTHROPIC_BASE_URL": "http://172.17.0.1:4000"})
    with pytest.raises(ValueError, match="docker-claude-host"):
        create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert not any(c[:2] == ["network", "create"] for c in docker_calls)


# ── review round on #72 ─────────────────────────────────────────────────────


def test_relay_passes_a_half_close_on_instead_of_ending():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]

    def serve():  # answers only after the client has finished sending
        conn, _ = srv.accept()
        data = b""
        while chunk := conn.recv(64):
            data += chunk
        conn.sendall(b"got:" + data)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    proxy = EgressProxy(bind="127.0.0.1", allow=[f"127.0.0.1:{port}"]).start()
    try:
        head, sock = _connect(proxy, f"127.0.0.1:{port}")
        sock.sendall(b"req")
        sock.shutdown(socket.SHUT_WR)
        assert sock.recv(64) == b"got:req"
        sock.close()
    finally:
        proxy.stop()
        srv.close()


def test_a_failed_allowed_tunnel_answers_502_and_keeps_host_and_port():
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()  # nothing listens there now
    proxy = EgressProxy(bind="127.0.0.1", allow=[f"127.0.0.1:{port}"]).start()
    try:
        head, sock = _connect(proxy, f"127.0.0.1:{port}")
        sock.close()
    finally:
        proxy.stop()
    assert head.startswith(b"HTTP/1.1 502")
    assert proxy.log[-1]["decision"] == "error"
    assert (proxy.log[-1]["host"], proxy.log[-1]["port"]) == ("127.0.0.1", port)


@pytest.mark.parametrize("bad", ["https://pypi.org", "pypi.org/simple", "user@pypi.org", "pypi.org:abc", ""])
def test_egress_entries_that_would_never_match_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_entry(bad)


def test_egress_entries_accept_ipv6():
    assert normalize_entry("[2001:db8::1]:8443") == "[2001:db8::1]:8443"
    assert normalize_entry("2001:db8::1") == "[2001:db8::1]:443"


def test_no_proxy_wildcards_and_cidrs():
    env = {"HTTPS_PROXY": "http://corp:912", "NO_PROXY": "*.corp.example,10.0.0.0/8"}
    assert _upstream(env, "llm.corp.example") is None
    assert _upstream(env, "10.1.2.3") is None
    assert _upstream(env, "11.1.2.3") == ("corp", 912)
    assert _upstream({**env, "NO_PROXY": "*"}, "anything") is None


def test_upstream_proxy_credentials_are_sent():
    from dokimi.harnesses.egress_proxy import _upstream_auth

    # Built from parts: a literal user:pass@host URL trips the secrets scanner.
    userinfo = ":".join(["u%40x", "p%3Aw"])
    assert _upstream_auth({"HTTPS_PROXY": f"http://{userinfo}@corp:912"}) == (
        "Proxy-Authorization: Basic dUB4OnA6dw==\r\n")
    assert _upstream_auth({"HTTPS_PROXY": "http://corp:912"}) == ""


def test_preflight_rejects_setups_a_container_cannot_carry():
    from dokimi.harnesses.container_agent import preflight

    with pytest.raises(ValueError, match="Bedrock needs credentials"):
        preflight({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "sso"})
    with pytest.raises(ValueError, match="loopback"):
        preflight({"HTTPS_PROXY": "http://127.0.0.1:3128", "ANTHROPIC_API_KEY": "k"})
    preflight({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_BEARER_TOKEN_BEDROCK": "t"})


def test_ca_and_credential_files_are_copied_in_and_repointed(tmp_path, cli, docker_calls, monkeypatch):
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----", encoding="utf-8")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(ca))
    seen: dict = {}
    _fake_claude(cli, seen)
    create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), model="haiku",
                                       output_dir=tmp_path / "out")
    assert any(c[:2] == ["cp", str(ca)] and c[2].endswith(":/opt/dokimi-agent/files/NODE_EXTRA_CA_CERTS")
               for c in docker_calls)


def test_a_binary_that_cannot_run_in_the_image_fails_clearly(tmp_path, cli, docker_calls, monkeypatch):
    cli.answer = lambda call: reply(stderr="exec /opt/dokimi-agent/claude: no such file", returncode=127) if (
        call.argv[1] == "exec" and call.argv[-1] == "--version") else None
    # Errored, with the isolation and egress record kept rather than raised away.
    result = create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), model="haiku",
                                                 output_dir=tmp_path / "out")
    assert "cannot run in the task image" in result.metrics["error"] and result.reward is None
    assert result.metrics["isolation"]["agent_location"] == "container"
    assert docker_calls[-1][:2] == ["network", "rm"]


def test_a_stale_container_is_removed_before_its_network(tmp_path, cli, docker_calls, monkeypatch):
    _fake_claude(cli, {})
    create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), model="haiku",
                                       output_dir=tmp_path / "out")
    first_rm = next(i for i, c in enumerate(docker_calls) if c[:2] == ["rm", "-f"])
    net_rm = next(i for i, c in enumerate(docker_calls) if c[:2] == ["network", "rm"])
    assert first_rm < net_rm and docker_calls[first_rm][2].startswith("dokimi-run-")


def test_probe_reports_a_writable_docker_socket(tmp_path, monkeypatch):
    sock_path = tmp_path / "docker.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(sock_path))
    monkeypatch.setattr(sandbox_mod.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(sandbox_mod, "PROBE_DOCKER_SOCKETS", str(sock_path))
    monkeypatch.setattr(sandbox_mod, "PROBE_HOST_GATEWAY", "127.0.0.1")
    try:
        ws = AgentWorkspace.create()
        result = run_probe(ws, {"PATH": "/usr/bin:/bin"}, url="http://127.0.0.1:9/", direct="127.0.0.1 9")
        ws.cleanup()
    finally:
        server.close()
    assert result["docker_api"] == "open"


# ── review round 4 on #72 ───────────────────────────────────────────────────


def test_vertex_hosts_follow_the_cli_region_mapping():
    def hosts(**env):
        return set(llm_hosts({"CLAUDE_CODE_USE_VERTEX": "1", **env}))

    assert {"aiplatform.googleapis.com:443"} <= hosts(CLOUD_ML_REGION="global")
    assert {"aiplatform.us.rep.googleapis.com:443"} <= hosts(CLOUD_ML_REGION="us")
    assert {"aiplatform.eu.rep.googleapis.com:443"} <= hosts(CLOUD_ML_REGION="eu")
    assert not {"us-aiplatform.googleapis.com:443"} & hosts(CLOUD_ML_REGION="us")
    assert {"europe-west1-aiplatform.googleapis.com:443"} <= hosts(CLOUD_ML_REGION="europe-west1")
    assert {"aiplatform.googleapis.com:443"} <= hosts(CLOUD_ML_REGION="us-east5",
                                                      VERTEX_REGION_CLAUDE_4_5_SONNET="global")


def test_provider_booleans_and_order_follow_the_cli():
    from dokimi.harnesses.egress_proxy import provider

    assert llm_hosts({"CLAUDE_CODE_USE_BEDROCK": "0"}) == ["api.anthropic.com:443"]
    assert provider({"CLAUDE_CODE_USE_BEDROCK": "false", "CLAUDE_CODE_USE_VERTEX": "yes"}) == "vertex"
    assert provider({"CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1"}) == "foundry"
    assert provider({"CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_BEDROCK": "on"}) == "bedrock"


@pytest.mark.parametrize("bad", ["*.pythonhosted.org", ".pythonhosted.org", "pypi.org.",
                                 "pypi.org,files.pythonhosted.org", "pypi.org.:443"])
def test_egress_entries_that_never_match_or_split_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_entry(bad)


def test_preflight_follows_the_cli_providers():
    from dokimi.harnesses.container_agent import preflight

    preflight({"CLAUDE_CODE_USE_BEDROCK": "0", "ANTHROPIC_API_KEY": "k"})  # not Bedrock at all
    with pytest.raises(ValueError, match="no hosts for this provider"):
        preflight({"CLAUDE_CODE_USE_MANTLE": "1"})
    with pytest.raises(ValueError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        preflight({"CLAUDE_CODE_USE_VERTEX": "1"})
    preflight({"CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_SKIP_VERTEX_AUTH": "1"})


@pytest.mark.parametrize("url,loop", [("http://0.0.0.0:3128", True), ("http://[::ffff:127.0.0.1]:1", True),
                                      ("http://localhost-gw.corp.example:3128", False),
                                      ("http://127.0.0.2:1", True), ("http://LOCALHOST.:1", True)])
def test_loopback_proxy_urls_are_parsed_as_addresses(url, loop):
    from dokimi.harnesses.container_agent import _is_loopback_url

    assert _is_loopback_url(url) is loop


def test_container_probe_does_not_deny_what_env_sets_itself(tmp_path, cli, docker_calls, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp")
    seen: dict = {}
    cli.answer = _probe_answer(seen, "{}")
    create_harness("docker-probe", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    deny = [tok.split(":", 1)[0] for tok in seen["env"]["PROBE_ENV_DENY"].split()]
    assert "GITHUB_TOKEN" in deny and "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in deny


@pytest.mark.parametrize("out,gateway", [("172.19.0.1 fde8:d51c:1ebc::1 ", "172.19.0.1"),
                                         ("fde8::1 10.0.0.1 ", "10.0.0.1"), ("garbage", ""), ("", "")])
def test_gateway_is_the_first_valid_ipv4(tmp_path, out, gateway):
    sb = _sandbox(tmp_path)
    assert sb.gateway(lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""), None) == gateway


def test_a_failed_network_create_leaves_no_env_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(container_mod.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(container_mod, "claude_binary", lambda: tmp_path / "claude")

    def docker(args, log, **kwargs):
        if args[:2] == ["network", "create"]:
            raise RuntimeError("docker network failed")
        return subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(RuntimeError):
        ContainerSandbox.open("r1", docker, None, source_env={"ANTHROPIC_API_KEY": "k"})
    assert not list(tmp_path.glob("dokimi-env-*"))


def test_close_removes_the_env_file_dir(tmp_path):
    sb = _sandbox(tmp_path)
    sb.exec_argv("c1", {"ANTHROPIC_API_KEY": "secret"}, ["true"])
    assert list((tmp_path / "envfiles").iterdir())
    sb.close(lambda *a, **k: reply(), None, None)
    assert not (tmp_path / "envfiles").exists()


def test_sidecar_binds_its_internal_alias_and_is_digest_pinned(tmp_path, cli, docker_calls, monkeypatch):
    _fake_claude(cli, {})
    create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), model="haiku", output_dir=tmp_path / "out")
    sidecar = next(c for c in docker_calls if c[0] == "create")
    assert sidecar[sidecar.index("--bind") + 1] == "egress"
    assert "@sha256:" in container_mod.PROXY_IMAGE and container_mod.PROXY_IMAGE in sidecar
    # The task container outlives the solve plus the verifier.
    run_call = next(c for c in docker_calls if c[0] == "run")
    assert int(run_call[-1]) >= 1800 and run_call[-2] == "sleep"


def test_the_container_outlives_a_long_solve(tmp_path, cli, docker_calls, monkeypatch):
    _fake_claude(cli, {})
    task_dir = _task(tmp_path)
    (task_dir / "task.toml").write_text("[agent]\ntimeout_sec = 2400\n[verifier]\ntimeout_sec = 300\n",
                                        encoding="utf-8")
    create_harness("docker-claude", runner=cli).run(load_task(task_dir), model="haiku", output_dir=tmp_path / "out")
    run_call = next(c for c in docker_calls if c[0] == "run")
    assert int(run_call[-1]) >= 2400 + 300


def test_a_verifier_on_a_dead_container_is_errored_not_zero(tmp_path, monkeypatch):
    def dead(call):
        args = call.argv[1:]
        if args[0] == "exec" and "test.sh" in args[-1]:  # it died during the verifier
            return reply(stderr="container dokimi-run-x is not running", returncode=1)
        if args[0] == "cp" and ":/logs/" in args[1]:
            return reply(returncode=1)
        if args[0] == "inspect":
            return reply("false\n")
        return None

    task = load_task(_task(tmp_path))
    with open(tmp_path / "log", "w") as log, pytest.raises(RuntimeError, match="not running at verify time"):
        DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=DockerCLI(answer=dead))._verify(
            task, "dokimi-run-x", tmp_path, log)


def test_container_skill_arm_ships_the_skill_with_links_followed(tmp_path, cli, docker_calls, monkeypatch):
    skill = tmp_path / "skills" / "demo"
    shared = skill / "shared" / "refs"
    shared.mkdir(parents=True)
    (shared / "a.md").write_text("ref", encoding="utf-8")
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nx", encoding="utf-8")
    (skill / "references").symlink_to("shared/refs")
    staged: dict = {}

    def record(call):
        args = call.argv[1:]
        if args[0] == "cp" and args[-1].endswith(":/opt/abskill"):
            links = [p for p in Path(args[1].rstrip(".")).rglob("*") if p.is_symlink()]
            refs = list(Path(args[1].rstrip(".")).rglob("references/a.md"))
            staged.update(links=links, ref=refs[0].read_text(encoding="utf-8") if refs else None)
        return None

    cli.answer = record
    _fake_claude(cli, {})
    create_harness(f"docker-claude-skill:{skill}", runner=cli).run(load_task(_task(tmp_path)), model="haiku",
                                                      output_dir=tmp_path / "out")
    assert staged == {"links": [], "ref": "ref"}


def test_probe_finds_a_proxy_that_forwards_plain_http(tmp_path, monkeypatch):
    """A forward path that skips the allowlist: CONNECT is refused, GET http:// is served."""
    monkeypatch.setattr(sandbox_mod.tempfile, "tempdir", str(tmp_path))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            # Read the whole head before answering, as a real proxy does: bash's printf
            # writes it a line at a time, and closing early resets the connection.
            head = b""
            while b"\r\n\r\n" not in head and (chunk := conn.recv(4096)):
                head += chunk
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n" if head.startswith(b"CONNECT")
                         else b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    url = f"http://127.0.0.1:{srv.getsockname()[1]}"
    try:
        ws = AgentWorkspace.create()
        result = run_probe(ws, {"PATH": "/usr/bin:/bin", "HTTP_PROXY": url, "HTTPS_PROXY": url},
                           url="http://127.0.0.1:9/", direct="127.0.0.1 9")
        ws.cleanup()
    finally:
        srv.close()
    assert result["network"] == "open"


def test_proxy_logs_plain_http_failures_with_host_and_normalised_names():
    proxy = EgressProxy(bind="127.0.0.1", allow=["localhost:9", "127.0.0.1:9"]).start()
    try:
        for req in (b"GET http://127.0.0.1:9/ HTTP/1.1\r\nHost: 127.0.0.1:9\r\n\r\n",
                    b"CONNECT LOCALHOST.:9 HTTP/1.1\r\nHost: x\r\n\r\n"):
            sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
            sock.sendall(req)
            sock.recv(1024)
            sock.close()
        for _ in range(50):
            if len(proxy.log) >= 4:
                break
            __import__("time").sleep(0.05)
    finally:
        proxy.stop()
    rows = [(r["host"], r["port"], r["decision"]) for r in proxy.log]
    # Allowed is logged before the connect, the failure after it, both with the host.
    assert rows == [("127.0.0.1", 9, "allowed"), ("127.0.0.1", 9, "error"),
                    ("localhost", 9, "allowed"), ("localhost", 9, "error")]


def test_cli_warns_when_arms_run_their_agents_in_different_places():
    from dokimi.commands.tasks import _launch_mismatches

    assert _launch_mismatches(["docker-claude", "docker-claude-host"], "docker-claude") == [
        "docker-claude-host runs its agent on the host, the baseline docker-claude in the task container"]
    assert _launch_mismatches(["docker-claude", "docker-probe"], "docker-claude") == []
    assert _launch_mismatches(["docker-claude"], None) == []


# ── review round on #72: canonical entries, one normalisation ───────────────


@pytest.mark.parametrize("entry", ["pypi.org", "PyPI.org:8443", "[2001:db8::1]:8443", "2001:db8::1"])
def test_normalize_entry_is_idempotent(entry):
    once = normalize_entry(entry)
    assert normalize_entry(once) == once


def test_url_entries_survive_normalisation_and_lose_a_trailing_dot():
    hosts = llm_hosts({"ANTHROPIC_BASE_URL": "http://[fd00::5]:4000"})
    assert "[fd00::5]:4000" in hosts and [normalize_entry(h) for h in hosts] == hosts
    assert {"gw.corp.example:4000"} <= set(llm_hosts({"ANTHROPIC_BASE_URL": "http://gw.corp.example.:4000"}))


def test_an_ipv6_entry_from_open_reaches_an_ipv6_host():
    srv = socket.socket(socket.AF_INET6)
    try:
        srv.bind(("::1", 0))
    except OSError:
        srv.close()
        pytest.skip("no IPv6 loopback")
    srv.listen()
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True).start()
    proxy = EgressProxy(bind="127.0.0.1", allow=[normalize_entry(f"[::1]:{port}")]).start()
    try:
        head, sock = _connect(proxy, f"[::1]:{port}")
        sock.close()
    finally:
        proxy.stop()
        srv.close()
    assert b" 200 " in head and proxy.allowed("::1", port)


def test_a_trailing_dot_base_url_does_not_break_the_other_tunnels():
    proxy = EgressProxy(bind="127.0.0.1", allow=llm_hosts({"ANTHROPIC_BASE_URL": "http://gw.example.:4000"}))
    assert proxy.allowed("api.anthropic.com", 443) and proxy.allowed("gw.example", 4000)


def test_a_bad_allowlist_entry_fails_at_construction_not_per_request():
    with pytest.raises(ValueError, match="no wildcards"):
        EgressProxy(bind="127.0.0.1", allow=["api.anthropic.com:443", "*.corp"])


def test_the_allowlist_is_normalised_once(monkeypatch):
    import dokimi.harnesses.egress_proxy as egress_mod

    proxy = EgressProxy(bind="127.0.0.1", allow=["pypi.org"])
    monkeypatch.setattr(egress_mod, "normalize_entry", lambda e: pytest.fail("per request"))
    assert proxy.allowed("PyPI.org.", 443) and not proxy.allowed("pypi.org", 80)


def test_a_vertex_region_is_trimmed_and_a_comma_is_refused_before_any_container(tmp_path, cli, docker_calls,
                                                                              monkeypatch):
    vertex = {"CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_SKIP_VERTEX_AUTH": "1"}
    assert {"us-east5-aiplatform.googleapis.com:443"} <= set(llm_hosts({**vertex, "CLOUD_ML_REGION": "us-east5 "}))
    for name, value in vertex.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VERTEX_REGION_CLAUDE_4_5_SONNET", "x,evil.example:443,y")
    with pytest.raises(ValueError, match="no wildcards"):
        create_harness("docker-claude", runner=cli).run(load_task(_task(tmp_path)), output_dir=tmp_path / "out")
    assert not any(c[:2] == ["network", "create"] or c[0] == "create" for c in docker_calls)


def test_ctrl_c_while_the_sidecar_starts_still_cleans_up(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def docker(args, log, **kwargs):
        calls.append(list(args))
        if args[0] == "create":
            raise KeyboardInterrupt
        return reply()

    # A temp dir of the test's own: under pytest-xdist other workers create and
    # remove dokimi-env-* in the shared one while this test runs.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(KeyboardInterrupt):
        ContainerSandbox.open("r1", docker, None, source_env={"ANTHROPIC_API_KEY": "k"}, with_agent=False)
    assert calls[-1] == ["network", "rm", "dokimi-net-r1"]
    assert not list(tmp_path.glob("dokimi-env-*"))


def test_the_plugin_modes_are_reset_not_widened(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Use for demo things.\n---\n# Demo\n", encoding="utf-8")
    cli = DockerCLI()
    harness = create_harness(f"docker-claude-skill:{skill}", runner=cli)
    harness._install_plugin_in_container("c1", tmp_path / "out", io.StringIO(), {})
    chmod = next(c for c in cli.args if "chmod" in c)
    # a+rX would keep a setuid or world-writable file from the source as it is.
    assert chmod == ["exec", "-u", "root", "c1", "chmod", "-R", "u=rwX,go=rX", "/opt/abskill"]


def test_an_announced_arm_does_not_warn_and_the_notice_is_on_by_default(
        tmp_path, cli, docker_calls, monkeypatch):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"---\nname: demo-skill\ndescription: {'x' * 1600}\n---\n# D\n",
                                    encoding="utf-8")
    task = load_task(_task(tmp_path))
    for name, harness in (("skill", create_harness(f"docker-claude-skill:{skill}", runner=cli)),
                          ("direct", DockerSolverHarness(harness_id="d", skill_path=str(skill), runner=cli))):
        seen: dict = {"stream": _stream("demo-skill")}
        _fake_claude(cli, seen)
        result = harness.run(task, model="haiku", output_dir=tmp_path / f"out-{name}")
        assert result.metrics["skill_listing"]["truncated"] is True
        assert result.metrics["skill_notice"] is True
        assert "demo-skill" in seen["argv"][seen["argv"].index("-p") + 1]
        assert "WARNING" not in (tmp_path / f"out-{name}" / "run.log").read_text(encoding="utf-8")


def test_two_runs_of_one_task_in_one_process_get_their_own_container(tmp_path):
    # A suite runs every harness cell in one process: a pid-only name made the
    # second cell's `docker rm -f` kill the first cell's container.
    task = load_task(_task(tmp_path))
    names = set()
    for arm in ("a", "b"):
        cli = DockerCLI()
        create_harness("docker-oracle", runner=cli).run(task, output_dir=tmp_path / arm)
        names |= {a[a.index("--name") + 1] for a in cli.args if a[0] == "run"}
    assert len(names) == 2 and all(n.startswith(f"dokimi-run-{task.name}-") for n in names)

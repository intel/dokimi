# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run the solver agent inside the task container, with egress only to the LLM.

The host launch in :mod:`.agent_sandbox` cannot hide an absolute path or close
the network. Inside the task container both come for free, because the
container holds only the image and ``/app``:

* the grader files reach the container only at verify time, after the agent
  has exited;
* neither the content checkout nor the operator's HOME is mounted;
* the container sits on a per-run ``--internal`` Docker network, which has no
  route out. The agent reaches the LLM through :mod:`.egress_proxy`, which runs
  in a sidecar container on that network and the default bridge, and tunnels
  only to the provider's hosts.

Everything goes through the Docker API (``docker cp``, a sidecar) and nothing
is bind-mounted or served from the harness process, so the same launch works
with a local daemon and on a Docker-in-Docker CI runner. The ``claude`` binary
is the host's own, copied in, so the image needs nothing installed. The agent
runs as an unprivileged uid, because the CLI refuses
``--dangerously-skip-permissions`` as root. Its HOME is a tmpfs at ``/run/abhome``.

A task's ``environment.network_mode = "public"`` is not extended to the agent.
Every task in the uxlfoundation/skills catalog declares it, and none of their
solutions or tests uses the network, so honouring it would hand every agent the
internet. A task whose solve needs other hosts lists them in
``environment.egress``. "public" applies to the verifier: the container joins
the default bridge only after the agent has exited.

What stays reachable: services on the Docker host that listen on the internal
network's gateway address or on ``0.0.0.0``. Closing that needs root on the
host.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from dokimi import process

from . import egress_proxy
from .agent_sandbox import (
    PROBE_DIRECT,
    PROBE_URL,
    SOLVER_TOOLS,
    agent_env,
    default_forbidden,
    denied_env_names,
    env_fingerprints,
    parse_probe_output,
    probe_env,
    probe_script,
)
from .egress_proxy import (
    env_true,
    host_env,
    llm_hosts,
    loopback_base_urls,
    normalize_entry,
    parse_log,
    provider,
)

AGENT_UID = "10001:10001"
# HOME is its own tmpfs, so the image's /tmp stays the image's (the verifier sees it too).
AGENT_HOME = "/run/abhome"
AGENT_BIN = "/opt/dokimi-agent/claude"
FILES_DIR = "/opt/dokimi-agent/files"
LABEL = "dokimi.run"  # on the network, sidecar and task container: find leftovers with it
# File-valued variables whose file is copied into the container and re-pointed there.
_FILE_VARS = ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE",
              "GOOGLE_APPLICATION_CREDENTIALS", "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE")
PROXY_PORT = 3128
_PROBE_TIMEOUT_SEC = 120.0
DOCKER_STEP_TIMEOUT = 600.0  # a docker step without a budget of its own (cp, exec, inspect, ...)
# The sidecar needs only a Python 3 interpreter. Pinned by digest (a tag moves, and
# the sidecar holds the upstream proxy URL); override for a mirror or an offline runner.
PROXY_IMAGE = os.environ.get(
    "DOKIMI_PROXY_IMAGE",
    "python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f")
# What the agent's own clients must not send to the egress proxy.
AGENT_NO_PROXY = "localhost,127.0.0.1,::1"
# Container-side paths the agent must not see while it solves: both layouts' grader
# dirs (the oracle and verifier are mounted at /solution, /oracle, /tests and /verifier).
CONTAINER_FORBIDDEN = ["/solution", "/oracle", "/tests", "/verifier", "/logs/verifier"]

# Host-path or host-shape variables that mean nothing inside the container.
_HOST_ONLY = frozenset({"PATH", "SHELL", "USER", "LOGNAME", "TMPDIR", "SSL_CERT_DIR", *_FILE_VARS})
# The proxy variables the Docker client may inject (proxies.default), in both cases.
_PROXY_NAMES = tuple(n for base in ("HTTPS", "HTTP", "ALL", "FTP", "NO")
                     for n in (f"{base}_PROXY", f"{base.lower()}_proxy"))

Docker = Callable[..., process.Completed]


def claude_binary() -> Path:
    """The host ``claude`` executable, resolved through its launcher symlink."""
    found = shutil.which("claude")
    if not found:
        raise FileNotFoundError("`claude` is not on PATH; it is copied into the task container")
    return Path(os.path.realpath(found))


@dataclass
class ContainerSandbox:
    """The internal network, the egress sidecar, and launch arguments for one solve."""

    network: str
    proxy_name: str
    allow: list[str]
    binary: Optional[Path]  # None for the probe, which needs no agent
    log: list[dict] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)  # _FILE_VARS name -> host file
    label: str = ""
    # The tree the agent owns and starts in: the task's working tree, /app or below
    # (the solver checks it). Not all of /app: a task can keep a read-only baseline
    # there (SkillsBench lean4-proof's /app/baseline), which a chown would hand over.
    workdir: str = "/app"
    _stopped: bool = False
    _scratch: Optional[Path] = None
    runner: process.Runner = field(default=process.run, repr=False)  # starts the probe's docker exec

    @classmethod
    def open(cls, run_id: str, docker: Docker, log, *, source_env: dict[str, str],
             task_egress: Optional[list[str] | str] = None,
             with_agent: bool = True, runner: process.Runner = process.run) -> "ContainerSandbox":
        if with_agent:
            preflight(source_env)
        binary = claude_binary() if with_agent else None
        extra_hosts = [task_egress] if isinstance(task_egress, str) else list(task_egress or [])
        # Every entry, not only the task's: a malformed one (a Vertex region with a comma)
        # fails here with its name, before the sidecar splits it into extra allowed hosts.
        allow = [normalize_entry(h) for h in llm_hosts(source_env) + [str(h) for h in extra_hosts]]
        files = {v: Path(source_env[v]) for v in _FILE_VARS
                 if source_env.get(v) and Path(source_env[v]).is_file()}
        sandbox = cls(network=f"dokimi-net-{run_id}", proxy_name=f"dokimi-egress-{run_id}",
                      allow=sorted(set(allow)), binary=binary, files=files,
                      label=f"{LABEL}={run_id}", runner=runner)
        # A crashed run with the same id may still hold the network: container first.
        for leftover in (run_id, sandbox.proxy_name):
            docker(["rm", "-f", leftover], log, check=False)
        docker(["network", "rm", sandbox.network], log, check=False)
        docker(["network", "create", "--internal", "--label", sandbox.label, sandbox.network], log)
        try:
            # After the network, inside the try: a failed create leaves no env-file dir behind.
            sandbox._scratch = Path(tempfile.mkdtemp(prefix="dokimi-env-"))
            sandbox._start_proxy(docker, log)
        except BaseException:  # also Ctrl-C (a first image pull): don't leave the network behind
            sandbox.close(docker, log, None)
            raise
        return sandbox

    def _start_proxy(self, docker: Docker, log) -> None:
        """Create the sidecar on bridge, copy the proxy in, join the internal network, start."""
        upstream = [a for k in sorted(host_env()) for a in ("-e", k)]
        docker(["create", "--name", self.proxy_name, "--network", "bridge", "--label", self.label,
                *upstream, PROXY_IMAGE, "python", "-u", "/egress_proxy.py",
                # Listen on the internal network only: not every container on bridge.
                "--bind", "egress", "--port", str(PROXY_PORT), "--allow", ",".join(self.allow)], log)
        docker(["cp", egress_proxy.__file__, f"{self.proxy_name}:/egress_proxy.py"], log)
        docker(["network", "connect", "--alias", "egress", self.network, self.proxy_name], log)
        docker(["start", self.proxy_name], log)
        for _ in range(100):  # the agent must not race a proxy that is not listening yet
            out = docker(["logs", self.proxy_name], log, check=False)
            if '"ready": true' in (getattr(out, "stdout", "") or ""):
                return
            time.sleep(0.2)
        raise RuntimeError(f"egress proxy {self.proxy_name} did not start; see run.log")

    @property
    def proxy_url(self) -> str:
        return f"http://egress:{PROXY_PORT}"

    def run_args(self) -> list[str]:
        """``docker run`` arguments: the internal network, the label, a tmpfs HOME."""
        uid, gid = AGENT_UID.split(":")
        return ["--network", self.network, "--label", self.label,
                "--tmpfs", f"{AGENT_HOME}:rw,exec,size=1g,mode=0700,uid={uid},gid={gid}"]

    def prepare(self, container: str, docker: Docker, log) -> None:
        """As root, before the agent starts: copy the CLI and files in, hand the work tree to the agent."""
        docker(["exec", "-u", "root", container, "sh", "-c",
                f"mkdir -p {Path(AGENT_BIN).parent} {FILES_DIR}"
                f" && chown -R {AGENT_UID} {shlex.quote(self.workdir)}"], log)
        for name, path in self.files.items():
            docker(["cp", str(path), f"{container}:{FILES_DIR}/{name}"], log)
        if self.files:
            docker(["exec", "-u", "root", container, "sh", "-c",
                    f"chown -R {AGENT_UID} {FILES_DIR} && chmod 0400 {FILES_DIR}/*"], log)
        if self.binary is not None:
            docker(["cp", str(self.binary), f"{container}:{AGENT_BIN}"], log)
            docker(["exec", "-u", "root", container, "chmod", "0755", AGENT_BIN], log)
            ran = docker(["exec", "-u", AGENT_UID, container, AGENT_BIN, "--version"], log, check=False)
            if getattr(ran, "returncode", 0) != 0:  # e.g. a glibc binary in a musl (alpine) image
                raise RuntimeError(f"{self.binary} cannot run in the task image as {AGENT_UID}; "
                                   "see run.log, or use docker-claude-host")

    def reap(self, container: str, docker: Docker, log) -> None:
        """Kill every process the agent uid left running once ``claude`` has exited.

        ``docker exec`` returning does not end what the agent backgrounded
        (``nohup ... &``). A survivor could read ``/tests`` once the verifier
        copies it in and rewrite ``/app`` under it, or use the bridge a public
        task joins for its verifier. ``kill -1`` as the agent uid reaches only
        that uid's processes; PID 1 belongs to root and stays.
        """
        ran = docker(["exec", "-u", AGENT_UID, container, "sh", "-c", "kill -9 -1 2>/dev/null; true"],
                     log, check=False)
        if ran.timed_out:  # survivors may still be running: the caller must not verify
            raise subprocess.TimeoutExpired(ran.argv, DOCKER_STEP_TIMEOUT)

    def env(self, source: dict[str, str], extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """The agent's environment inside the container."""
        env = {k: v for k, v in agent_env(source, extra).items()
               if k not in _HOST_ONLY and k not in _PROXY_NAMES}
        # NO_PROXY is set, not dropped: the Docker client injects its own (from
        # proxies.default), and an LLM host in it would bypass egress and fail.
        # Likewise ALL_PROXY and FTP_PROXY: left unset, the client's value (and its
        # credentials) would reach the agent.
        env.update({n: AGENT_NO_PROXY if n.lower() == "no_proxy" else self.proxy_url
                    for n in _PROXY_NAMES})
        env.update({"HOME": AGENT_HOME, "CLAUDE_CONFIG_DIR": f"{AGENT_HOME}/.claude",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        env.update({name: f"{FILES_DIR}/{name}" for name in self.files})
        return env

    def exec_argv(self, container: str, env: dict[str, str], argv: list[str]) -> tuple[list[str], dict[str, str]]:
        """``docker exec`` argv and the env to run the *client* with.

        The agent's env goes in a 0600 ``--env-file``, never on argv. The client
        keeps the operator's own environment: the agent's ``HOME`` and proxy would
        change which daemon and route the ``docker`` CLI itself uses. A value with
        a newline cannot go in an env file; that variable alone travels as
        ``-e NAME`` in the client's env.
        """
        assert self._scratch is not None
        fd, name = tempfile.mkstemp(dir=self._scratch, prefix="env-")
        multiline = {k: v for k, v in env.items() if "\n" in v}
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for key in sorted(env):
                if key not in multiline:
                    fh.write(f"{key}={env[key]}\n")
        names = [a for k in sorted(multiline) for a in ("-e", k)]
        argv_out = ["docker", "exec", "-u", AGENT_UID, "-w", self.workdir, "--env-file", name, *names,
                    container, *argv]
        return argv_out, {**os.environ, **multiline}

    def run_probe(self, container: str, task_path: Path, source: dict[str, str],
                  extra: Optional[dict[str, str]], docker: Docker, log,
                  skill_dirs: Iterable[Path] = ()) -> dict[str, str]:
        """The isolation probe as the agent uid, with the agent's env, in its work tree.

        A variable the task image sets itself (``ENV DEBIAN_FRONTEND=...``) is not a
        leak even when the host has one of the same name, so those are not checked.
        """
        forbidden = default_forbidden(task_path, skill_dirs)
        forbidden["task_files"] = [*forbidden["task_files"], *map(Path, CONTAINER_FORBIDDEN)]
        # The image's own env, not the container's: that one also holds what the Docker
        # client injected (proxies.default), which the agent must not be excused for.
        image = docker(["inspect", "-f", "{{.Image}}", container], log, check=False)
        image_id = (getattr(image, "stdout", "") or "").strip()
        inspected = (docker(["image", "inspect", "-f", "{{json .Config.Env}}", image_id], log, check=False)
                     if image_id else None)
        try:
            listed = json.loads(getattr(inspected, "stdout", "") or "[]") or []
            image_names = {e.split("=", 1)[0] for e in listed}
        except (AttributeError, json.JSONDecodeError):
            image_names = set()
        env = self.env(source, extra)
        # Neither a name the image sets nor one env() sets itself is a host leak.
        deny = [n for n in denied_env_names(source) if n not in image_names and n not in env]
        env.update(probe_env(forbidden, env_fingerprints(source, deny), PROBE_URL, PROBE_DIRECT))
        env["PROBE_GATEWAY"] = self.gateway(docker, log)
        argv, client_env = self.exec_argv(container, env, ["sh", "-c", probe_script()])
        proc = self.runner(argv, timeout=_PROBE_TIMEOUT_SEC, env=process.Env.only((), client_env))
        if proc.timed_out:  # a hung check showed nothing closed; the caller records it untested
            raise subprocess.TimeoutExpired(argv, _PROBE_TIMEOUT_SEC, proc.stdout_bytes, proc.stderr_bytes)
        return parse_probe_output(proc.stdout)

    def gateway(self, docker: Docker, log) -> str:
        """The internal network's first IPv4 gateway (the Docker host, as the container
        sees it), or "" so the probe reports ``docker_api`` untested, not a false closed."""
        out = docker(["network", "inspect", self.network, "-f",
                      "{{range .IPAM.Config}}{{.Gateway}} {{end}}"], log, check=False)
        for word in (getattr(out, "stdout", "") or "").split():
            try:
                if ipaddress.ip_address(word).version == 4:
                    return word
            except ValueError:
                continue
        return ""

    def isolation(self) -> dict[str, str]:
        return {
            "agent_location": "container",
            "work_tree": f"{self.workdir} in the task container",
            "operator_config": ("not in the container; the settings.json env block is merged"
                                " through the env allowlist"),
            "env": "allowlist",
            "tools": ",".join(SOLVER_TOOLS) + " (Bash can still start a nested claude with its defaults)",
            "host_filesystem": "not mounted",
            "network": "internal; egress proxy to " + ",".join(self.allow),
            "memory": "the agent's CLI and its tmpfs HOME count against the task's memory_mb",
        }

    def stop_proxy(self, docker: Docker, log) -> None:
        """Collect the sidecar's decisions and remove it, so the verifier has no proxy
        (a public task's verifier joins bridge right after)."""
        if self._stopped:
            return
        logs = docker(["logs", self.proxy_name], log, check=False)
        self.log = parse_log(getattr(logs, "stdout", "") or "")
        removed = docker(["rm", "-f", self.proxy_name], log, check=False)
        if removed.timed_out:  # still running, with the credentials: not stopped, close() retries
            raise subprocess.TimeoutExpired(removed.argv, DOCKER_STEP_TIMEOUT)
        self._stopped = True

    def close(self, docker: Docker, log, output_dir: Optional[Path]) -> None:
        """Stop the sidecar, save its decisions as ``egress.jsonl``, drop the network."""
        try:
            self.stop_proxy(docker, log)
        except subprocess.TimeoutExpired as exc:  # teardown is best-effort: go on to the network
            if log is not None:
                log.write(f"\n[sidecar removal] timed out after {exc.timeout:g}s\n")
        if output_dir is not None:
            with (output_dir / "egress.jsonl").open("w", encoding="utf-8") as fh:
                for row in self.log:
                    fh.write(json.dumps(row) + "\n")
        docker(["network", "rm", self.network], log, check=False)
        if self._scratch is not None:
            shutil.rmtree(self._scratch, ignore_errors=True)

    def egress_summary(self) -> dict[str, list[str]]:
        def hosts(decision: str) -> list[str]:
            return sorted({r["host"] for r in self.log if r["decision"] == decision})

        denied_entries = sorted({f"{r['host']}:{r['port']}" for r in self.log if r["decision"] == "denied"})
        return {"allowed": hosts("allowed"), "denied": hosts("denied"), "errors": hosts("error"),
                "denied_entries": denied_entries}


def preflight(env: dict[str, str]) -> None:
    """Fail before the agent starts when the provider setup cannot work in a container."""
    loopback = loopback_base_urls(env) + [u for u in (host_proxy_urls(env)) if _is_loopback_url(u)]
    if loopback:
        raise ValueError(f"{', '.join(loopback)} is on the host's loopback, which the task "
                         "container and the egress sidecar cannot reach; use docker-claude-host, "
                         "or an address the Docker network can route to")
    unknown = [n for n in _UNMAPPED_PROVIDERS if env_true(env, n)]
    if unknown:
        raise ValueError(f"{', '.join(unknown)}: the egress allowlist has no hosts for this provider; "
                         "use docker-claude-host")
    if provider(env) == "vertex" and not (
            env.get("GOOGLE_APPLICATION_CREDENTIALS") or env_true(env, "CLAUDE_CODE_SKIP_VERTEX_AUTH")):
        raise ValueError("Vertex needs GOOGLE_APPLICATION_CREDENTIALS (a key file the container can "
                         "carry) or CLAUDE_CODE_SKIP_VERTEX_AUTH; gcloud ADC does not reach the container")
    if provider(env) == "bedrock" and not (
            env.get("AWS_BEARER_TOKEN_BEDROCK") or env.get("AWS_ACCESS_KEY_ID")
            or env.get("AWS_SHARED_CREDENTIALS_FILE") or env_true(env, "CLAUDE_CODE_SKIP_BEDROCK_AUTH")):
        raise ValueError("Bedrock needs credentials the container can carry: AWS_BEARER_TOKEN_BEDROCK, "
                         "AWS_ACCESS_KEY_ID/SECRET, or AWS_SHARED_CREDENTIALS_FILE; an AWS_PROFILE "
                         "from ~/.aws or an SSO login does not reach the container")


def host_proxy_urls(env: dict[str, str]) -> list[str]:
    return [v for k, v in env.items() if k.lower() in ("https_proxy", "http_proxy") and v]


def _is_loopback_url(url: str) -> bool:
    from urllib.parse import urlsplit

    host = (urlsplit(url if "://" in url else "http://" + url).hostname or "").rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None) or addr
    return mapped.is_loopback or mapped.is_unspecified


# Providers the CLI supports whose hosts llm_hosts() does not know.
_UNMAPPED_PROVIDERS = ("CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
                       "CLAUDE_CODE_USE_MANTLE")

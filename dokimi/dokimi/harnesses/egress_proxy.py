# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An allowlisting HTTPS CONNECT proxy for an agent running in a task container.

The task container sits on a ``--internal`` Docker network, which has no route
out. This proxy runs in a sidecar container attached to that network and to the
default bridge (see :mod:`.container_agent`). A sidecar works wherever the
Docker daemon is, including Docker-in-Docker CI runners, where no host process
is reachable from a container network.

The allowlist holds ``host:port`` entries, normally just the LLM endpoint on 443.
``CONNECT host:port`` is tunnelled only for an entry; anything else gets 403.
Every decision is recorded; the sidecar prints each one as a JSON line, so a run
shows which hosts its agent tried to reach.

A plain-HTTP request (an ``http://`` gateway such as LiteLLM in
``ANTHROPIC_BASE_URL``) is forwarded to an allowed ``host:port`` directly. The
request line is rewritten to origin-form, and ``Connection: close`` holds every
connection to that one request. Plain HTTP is not chained through an upstream
proxy, because a forward proxy would honour the next request's own absolute URL
on a kept-alive connection. Such a host has to be in ``NO_PROXY``.

If the sidecar itself must use a proxy (``HTTPS_PROXY``), allowed tunnels are
chained through it.

This file runs standalone (stdlib only): ``python egress_proxy.py --allow h1,h2``.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import select
import sys
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

_BUF = 65536
_HEADER_LIMIT = 16384


_BASE_URL_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
                  "ANTHROPIC_FOUNDRY_BASE_URL")
_LOOPBACK = ("localhost", "127.", "::1", "0.0.0.0")  # nosec B104 - prefixes to detect, not a bind


def _entry(host: str, port: object) -> str:
    """The one spelling of a ``host:port`` entry: an IPv6 host is bracketed."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _url_entry(url: str) -> Optional[str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".")  # ``gw.example.:4000`` is ``gw.example:4000``
    if not host:
        return None
    return _entry(host, parts.port or (80 if parts.scheme == "http" else 443))


def env_true(env: dict[str, str], name: str) -> bool:
    """A boolean the way the CLI reads one: only ``1``, ``true``, ``yes`` and ``on``."""
    return (env.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def provider(env: dict[str, str]) -> str:
    """The provider the CLI picks, in its own order: Bedrock, Foundry, then Vertex."""
    for name in ("BEDROCK", "FOUNDRY", "VERTEX"):
        if env_true(env, f"CLAUDE_CODE_USE_{name}"):
            return name.lower()
    return "firstparty"


def vertex_host(region: str) -> str:
    """The Vertex endpoint the CLI uses for *region* (``global``, ``us``/``eu`` multi-regions)."""
    if region == "global":
        return "aiplatform.googleapis.com"
    if region in ("us", "eu"):
        return f"aiplatform.{region}.rep.googleapis.com"
    return f"{region}-aiplatform.googleapis.com"


def llm_hosts(env: dict[str, str]) -> list[str]:
    """``host:port`` entries the configured Claude Code provider talks to, from *env*."""
    hosts: list[str] = []
    chosen = provider(env)
    if chosen == "bedrock":
        region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1"
        hosts += [f"bedrock-runtime.{region}.amazonaws.com", f"bedrock.{region}.amazonaws.com"]
    elif chosen == "vertex":
        region = (env.get("CLOUD_ML_REGION") or "").strip() or "us-east5"
        hosts += [vertex_host(region), "oauth2.googleapis.com"]
        # Per-model overrides (VERTEX_REGION_CLAUDE_...) send that model elsewhere.
        overrides = (v.strip() for k, v in sorted(env.items()) if k.startswith("VERTEX_REGION_"))
        hosts += [vertex_host(v) for v in overrides if v]
    elif chosen == "foundry":
        if env.get("ANTHROPIC_FOUNDRY_RESOURCE"):
            hosts.append(f"{env['ANTHROPIC_FOUNDRY_RESOURCE']}.services.ai.azure.com")
    else:
        hosts.append("api.anthropic.com")
    entries = [f"{h}:443" for h in hosts]
    entries += [e for e in (_url_entry(env[v]) for v in _BASE_URL_VARS if env.get(v)) if e]
    return sorted(set(entries))


def loopback_base_urls(env: dict[str, str]) -> list[str]:
    """Base URLs on the host's loopback, which no container network can reach."""
    return [env[v] for v in _BASE_URL_VARS
            if env.get(v) and (urlsplit(env[v]).hostname or "").startswith(_LOOPBACK)]


def normalize_entry(entry: str) -> str:
    """``host`` -> ``host:443``; ``host:port`` and ``[v6]:port`` kept (a task's ``environment.egress``).

    The result is canonical (an IPv6 host stays in brackets) and a fixed point:
    normalizing it again returns it unchanged.

    A URL, a path, userinfo, a wildcard (``*.pythonhosted.org``), a comma list
    or a leading/trailing dot is rejected rather than turned into an entry that
    silently never matches (or, for a comma, that the sidecar splits in two).
    """
    e = entry.strip().lower()
    if (not e or any(c in e for c in ("/", "@", " ", "*", ","))
            or e.startswith(".") or e.split(":", 1)[0].endswith(".")):
        raise ValueError(f"egress entry {entry!r}: expected host, host:port or [ipv6]:port "
                         "(no wildcards: list each host)")
    if e.startswith("["):
        host, _, rest = e[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else "443"
    elif e.count(":") > 1:  # a bare IPv6 address
        host, port = e, "443"
    else:
        host, sep, port = e.rpartition(":")
        host, port = (host, port) if sep else (e, "443")
    if not host or not port.isdigit():
        raise ValueError(f"egress entry {entry!r}: expected host, host:port or [ipv6]:port")
    return _entry(host, port)


def _no_proxy_match(no_proxy: str, host: str) -> bool:
    """``NO_PROXY`` as curl reads it: ``*``, domain suffixes (``.x``, ``*.x``) and CIDRs."""
    for item in (i.strip().lower() for i in no_proxy.split(",")):
        item = item[2:] if item.startswith("*.") else item.lstrip(".")
        if not item:
            continue
        if item == "*" or host == item or host.endswith("." + item):
            return True
        try:
            if ipaddress.ip_address(host) in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def _upstream(env: dict[str, str], host: str, scheme: str = "https") -> Optional[tuple[str, int]]:
    """The host's own proxy for *host*, honouring ``NO_PROXY`` suffixes."""
    if scheme == "http":
        proxy = env.get("HTTP_PROXY") or env.get("http_proxy")
    else:
        proxy = env.get("HTTPS_PROXY") or env.get("https_proxy")
    if not proxy or _no_proxy_match(env.get("NO_PROXY") or env.get("no_proxy") or "", host.lower()):
        return None
    parts = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    return (parts.hostname or "", parts.port or 80) if parts.hostname else None


def _upstream_auth(env: dict[str, str]) -> str:
    """A ``Proxy-Authorization`` header line for credentials in the upstream proxy URL."""
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
    parts = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    if not parts.username:
        return ""
    raw = f"{unquote(parts.username)}:{unquote(parts.password or '')}".encode()
    return f"Proxy-Authorization: Basic {base64.b64encode(raw).decode()}\r\n"


@dataclass
class EgressProxy:
    """Serve on ``(bind, port)`` until :meth:`stop`; ``log`` holds one dict per request."""

    bind: str
    allow: list[str]
    upstream_env: dict[str, str] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    port: int = 0
    on_record: Optional[Callable[[dict], None]] = None
    _sock: Optional[socket.socket] = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _entries: set[str] = field(default_factory=set, init=False, repr=False)

    def start(self) -> "EgressProxy":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind, self.port))
        sock.listen(64)
        sock.settimeout(0.5)
        self._sock = sock
        self.port = sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()

    @property
    def url(self) -> str:
        return f"http://{self.bind}:{self.port}"

    def __post_init__(self) -> None:
        # Once, here: a bad entry fails the start, not every request.
        self._entries = {normalize_entry(e) for e in self.allow}

    def allowed(self, host: str, port: int) -> bool:
        return _entry(_norm_host(host), port) in self._entries

    # ── internals ───────────────────────────────────────────────────────────

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(30)
        # host/port as parsed, and whether a response has started (then no 502 is appended).
        state: dict = {"host": "", "port": 0, "established": False}
        try:
            head = _read_head(client)
            line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            method, _, rest = line.partition(" ")
            target = rest.split(" ", 1)[0]
            if method.upper() != "CONNECT":
                self._forward_http(client, head, target, state)
                return
            host, _, port_s = target.rpartition(":")
            port = int(port_s) if port_s.isdigit() else 443
            host = _norm_host(host.strip("[]"))
            state.update(host=host, port=port)
            if not self.allowed(host, port):
                self._record(host, port, "denied", "not on the allowlist")
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            # Logged before connecting, so a CONNECT still pending when the solve ends is seen.
            self._record(host, port, "allowed", "")
            remote = self._open(host, port)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            state["established"] = True
            _relay(client, remote)
        except Exception as exc:  # noqa: BLE001 - one bad tunnel must not take the proxy down
            self._record(state["host"], state["port"], "error", f"{type(exc).__name__}: {exc}")
            if not state["established"]:  # the client learns the route failed, not just a closed socket
                try:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass
        finally:
            client.close()

    def _forward_http(self, client: socket.socket, head: bytes, target: str, state: dict) -> None:
        """One absolute-form plain-HTTP request to an allowed ``host:port``, sent directly."""
        parts = urlsplit(target)
        host, port = _norm_host(parts.hostname or ""), (parts.port or 80)
        state.update(host=host or target, port=port)
        reason = ""
        if parts.scheme != "http" or not host:
            reason = "not an absolute http:// request"
        elif not self.allowed(host, port):
            reason = "not on the allowlist"
        elif _upstream(self.upstream_env, host, "http") is not None:
            reason = "plain HTTP is not chained through the upstream proxy; add the host to NO_PROXY"
        if reason:
            self._record(host or target, port, "denied", reason)
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        self._record(host, port, "allowed", "plain http")
        remote = socket.create_connection((host, port), timeout=30)
        remote.sendall(_origin_form(head, parts))
        state["established"] = True  # a relay error now must not append a 502 to the body
        _relay(client, remote)

    def _open(self, host: str, port: int) -> socket.socket:
        up = _upstream(self.upstream_env, host)
        if up is None:
            return socket.create_connection((host, port), timeout=30)
        remote = socket.create_connection(up, timeout=30)
        remote.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                       f"{_upstream_auth(self.upstream_env)}\r\n".encode())
        status = _read_head(remote).split(b"\r\n", 1)[0]
        if b" 200" not in status:
            remote.close()
            raise ConnectionError(f"upstream proxy refused: {status.decode('latin-1', 'replace')}")
        return remote

    def _record(self, host: str, port: int, decision: str, reason: str) -> None:
        row = {"t": round(time.time(), 3), "host": host, "port": port,
               "decision": decision, "reason": reason}
        self.log.append(row)
        if self.on_record is not None:
            self.on_record(row)


def _norm_host(host: str) -> str:
    """``LOCALHOST.`` and ``localhost`` are one host, in the allowlist check and in the log."""
    return host.lower().rstrip(".")


def _read_head(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1)  # byte-wise: nothing after the header may be consumed
        if not chunk:
            break
        data += chunk
        if len(data) > _HEADER_LIMIT:
            raise ValueError("request header too large")
    return data


def _origin_form(head: bytes, parts) -> bytes:
    """Rewrite the request line to origin-form and pin the connection to this request."""
    lines = head.decode("latin-1").split("\r\n")
    method, _, rest = lines[0].partition(" ")
    version = rest.rsplit(" ", 1)[-1]
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    drop = ("connection:", "proxy-connection:", "keep-alive:", "proxy-authorization:")
    headers = [h for h in lines[1:] if h and not h.lower().startswith(drop)]
    return "\r\n".join([f"{method} {path} {version}", *headers, "Connection: close", "", ""]).encode("latin-1")


def _relay(a: socket.socket, b: socket.socket) -> None:
    """Copy both ways until both sides are done; a half-close is passed on, not treated as the end."""
    socks = [a, b]
    try:
        while socks:
            readable, _, broken = select.select(socks, [], socks, 300)
            if broken or not readable:
                return
            for s in readable:
                other = b if s is a else a
                data = s.recv(_BUF)
                if not data:
                    socks.remove(s)
                    try:
                        other.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                other.sendall(data)
    finally:
        b.close()


def host_env() -> dict[str, str]:
    """The proxy variables of this process, which allowed tunnels chain through."""
    return {k: v for k, v in os.environ.items()
            if k.lower() in ("https_proxy", "http_proxy", "no_proxy")}


def parse_log(text: str) -> list[dict]:
    """The decision rows a sidecar printed (other output is ignored)."""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and "decision" in row:
                rows.append(row)
    return rows


def _main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--bind", default="127.0.0.1",
                    help="address or name to listen on; the sidecar passes its internal-network alias")
    ap.add_argument("--port", type=int, default=3128)
    ap.add_argument("--allow", required=True, help="comma-separated host:port allowlist")
    ap.add_argument("--max-seconds", type=float, default=4 * 3600,
                    help="exit after this long, so a sidecar orphaned by a killed harness ends")
    args = ap.parse_args(argv)

    def emit(row: dict) -> None:
        print(json.dumps(row), flush=True)

    # A name (the sidecar's alias) is resolved here, so only that network reaches the proxy.
    bind = socket.gethostbyname(args.bind)
    EgressProxy(bind=bind, port=args.port, allow=[h for h in args.allow.split(",") if h],
                upstream_env=host_env(), on_record=emit).start()
    print(json.dumps({"ready": True, "port": args.port}), flush=True)
    time.sleep(args.max_seconds)


if __name__ == "__main__":
    _main(sys.argv[1:])

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Real writable-socket detection, including hosts with long temporary paths."""

import socket
import tempfile
from pathlib import Path

import dokimi.harnesses.agent_sandbox as sandbox_mod
from dokimi.harnesses.agent_sandbox import AgentWorkspace, run_probe


def test_probe_reports_a_writable_docker_socket(tmp_path, monkeypatch):
    # AF_UNIX paths on macOS are limited to 104 bytes; pytest's normal TMPDIR
    # can exceed that before the socket filename is added.
    # mkdtemp creates a private unpredictable directory; only its parent is fixed.
    with tempfile.TemporaryDirectory(prefix="dk-", dir="/tmp") as short_tmp:  # nosec B108
        sock_path = Path(short_tmp) / "docker.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(sock_path))
            monkeypatch.setattr(sandbox_mod.tempfile, "tempdir", str(tmp_path))
            monkeypatch.setattr(sandbox_mod, "PROBE_DOCKER_SOCKETS", str(sock_path))
            monkeypatch.setattr(sandbox_mod, "PROBE_HOST_GATEWAY", "127.0.0.1")
            ws = AgentWorkspace.create()
            try:
                result = run_probe(ws, {"PATH": "/usr/bin:/bin"},
                                   url="http://127.0.0.1:9/", direct="127.0.0.1 9")
            finally:
                ws.cleanup()
            assert result["docker_api"] == "open"



# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Uptake: whether the agent of a cell used what its arm added (#41 D4).

An arm that adds a tool (a script, a document, an MCP server) can move a score
through the guidance line that names the tool even if the tool is never
called. ``result.json`` therefore records, for a ``patch:`` arm that adds
something an agent can use, how often the agent used it:

- **files**: the files the patch added that the guidance globs do not match
  (a guidance file is loaded, not used);
- **commands**: command events whose text names one of those files, as a path
  relative to the workspace (``tools/fmt``, ``./tools/fmt``, ``bash tools/fmt``)
  or under the workspace's absolute path. A command run as ``cd tools && ./fmt``
  is not counted;
- **reads**: read events of one of those files;
- **mcp_servers** / **mcp_calls**: the servers of a ``.mcp.json`` the patch
  added or changed, and the calls of their tools (``mcp__<server>__<tool>``).
  Whether the harness starts them is the profile's business: a profile that
  starts none gives 0 calls, which is what the warning is for.

``used`` is whether any count is non-zero; ``measured`` is false when the agent
left no trace to count from (it never started, or its trace did not parse).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MCP_CONFIG = ".mcp.json"


def mcp_servers(ws: Path, arm_files: list[str]) -> list[str]:
    """The server names of the ``.mcp.json`` at the workspace root, when the arm wrote it; read at prep, before
    the agent can change it. A file that does not parse names no server."""
    if MCP_CONFIG not in arm_files:
        return []
    try:
        servers = json.loads((ws / MCP_CONFIG).read_text(encoding="utf-8")).get("mcpServers")
    except (OSError, ValueError, AttributeError):
        return []
    return sorted(str(k) for k in servers) if isinstance(servers, dict) else []


def arm_uptake(events: list[dict[str, Any]] | None, files: list[str], servers: list[str],
               workdir: str) -> dict[str, Any]:
    """The ``uptake`` of ``result.json`` from the cell's normalized events (``None``: no trace)."""
    out: dict[str, Any] = {"files": sorted(files), "mcp_servers": sorted(servers), "measured": events is not None,
                           "commands": 0, "reads": 0, "mcp_calls": 0, "used": False}
    if events is None:
        return out
    named = _path_pattern(files, workdir)
    prefixes = tuple(f"mcp__{_mcp_name(s)}__" for s in servers)
    wanted = set(files)
    for ev in events:
        kind = ev.get("kind")
        if kind == "command" and named is not None and named.search(str(ev.get("text", ""))):
            out["commands"] += 1
        elif kind == "read" and ev.get("path") in wanted:
            out["reads"] += 1
        elif kind == "tool" and prefixes and str(ev.get("name", "")).startswith(prefixes):
            out["mcp_calls"] += 1
    out["used"] = bool(out["commands"] or out["reads"] or out["mcp_calls"])
    return out


def _path_pattern(files: list[str], workdir: str) -> re.Pattern[str] | None:
    """One of ``files`` as a whole word of a command: relative (optionally ``./``) or under ``workdir``."""
    if not files:
        return None
    paths = "|".join(re.escape(f) for f in sorted(files, key=len, reverse=True))
    root = re.escape(workdir.rstrip("/") + "/")
    return re.compile(rf"(?:^|(?<=[\s'\"`=;&|(<>])|(?<=\./)|(?<={root}))(?:{paths})(?=$|[\s'\"`;&|)<>:])")


def _mcp_name(server: str) -> str:
    """A server name as Claude Code spells it in tool names: characters outside ``[A-Za-z0-9_-]`` become ``_``."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", server)

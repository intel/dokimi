# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Uptake: whether the agent of a cell used what its arm added (#41 D4).

An arm that adds a tool (a script, a document, an MCP server) can move a score
through the guidance line that names the tool even if the tool is never
called. ``result.json`` therefore records, for a ``patch:`` arm that adds
something an agent can use, how often the agent used it:

- **files**: the files the patch added that the guidance globs do not match
  (a guidance file is loaded, not used; ``.mcp.json`` is a config whose servers are counted by their calls);
- **commands**: command events whose text names one of those files, as a path
  relative to the workspace (``tools/fmt``, ``./tools/fmt``, ``bash tools/fmt``)
  or under the workspace's absolute path. A file at the workspace root is named only as ``./name``, under the
  absolute path or after a shell or interpreter (``sh run.sh``): a bare ``lint`` resolves through ``PATH``, not to
  the workspace file, and ``npm run lint`` does not name a ``lint`` file. A command run as ``cd tools && ./fmt``
  is not counted;
- **reads**: read events of one of those files (reading a script's text counts: it is the agent engaging with
  what the arm added, not only running it);
- **mcp_servers** / **mcp_calls**: the servers the patch added to the ``.mcp.json``
  (those the file already declared at the base are not the arm's), and the calls of their tools (``mcp__<server>__<tool>``).
  Whether the harness starts them is the profile's business: a profile that
  starts none gives 0 calls, which is what the warning is for.

A call the harness denied (``denied``) is not counted. ``used`` is whether any
count is non-zero; ``measured`` is false when the agent left no trace to count
from (it never started, or its trace did not parse).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MCP_CONFIG = ".mcp.json"


def base_servers(text: str) -> set[str]:
    """The server names a ``.mcp.json`` text declares; none when it does not parse."""
    try:
        servers = json.loads(text).get("mcpServers")
    except (ValueError, AttributeError):
        return set()
    return {str(k) for k in servers} if isinstance(servers, dict) else set()


def mcp_servers(ws: Path, arm_files: list[str], base: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """The server names of the ``.mcp.json`` at the workspace root that the arm added: those it wrote, less the
    ``base`` ones the file already declared before the patch; read at prep, before the agent can change it. A file that does not parse names no server, nor does a link out of the workspace
    or to anything but a regular file: it is not read."""
    if MCP_CONFIG not in arm_files:
        return []
    try:
        path = (ws / MCP_CONFIG).resolve()
        if not path.is_relative_to(ws.resolve()) or not path.is_file():
            return []
        servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers")
    except (OSError, RuntimeError, ValueError, AttributeError):  # RuntimeError: a link loop, before Python 3.13
        return []
    return sorted(str(k) for k in servers if str(k) not in base) if isinstance(servers, dict) else []


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
        if ev.get("denied"):  # the harness refused the call: the tool never ran
            continue
        kind = ev.get("kind")
        if kind == "command" and named is not None and named.search(str(ev.get("text", ""))):
            out["commands"] += 1
        elif kind == "read" and ev.get("path") in wanted:
            out["reads"] += 1
        elif kind == "tool" and prefixes and str(ev.get("name", "")).startswith(prefixes):
            out["mcp_calls"] += 1
    out["used"] = bool(out["commands"] or out["reads"] or out["mcp_calls"])
    return out


_INTERPRETERS = r"(?:sh|bash|zsh|dash|python3?|node|ruby|perl)"


def _path_pattern(files: list[str], workdir: str) -> re.Pattern[str] | None:
    """One of ``files`` as a whole word of a command: relative (optionally ``./``) or under ``workdir``.
    A file with no ``/`` is a common word, and bare it resolves through ``PATH``, so it must be ``./``-prefixed,
    under ``workdir`` or run by an interpreter."""
    if not files:
        return None
    root = re.escape(workdir.rstrip("/") + "/")
    end = r"(?=$|[\s'\"`;&|)<>:])"
    parts = []
    nested = sorted((f for f in files if "/" in f), key=len, reverse=True)
    flat = sorted((f for f in files if "/" not in f), key=len, reverse=True)
    if nested:
        paths = "|".join(re.escape(f) for f in nested)
        parts.append(rf"(?:^|(?<=[\s'\"`=;&|(<>])|(?<=\./)|(?<={root}))(?:{paths}){end}")
    if flat:
        names = "|".join(re.escape(f) for f in flat)
        parts.append(rf"(?:^\s*\./|[;&|(]\s*\./|(?:^|(?<=\s)){_INTERPRETERS}\s+|(?<=[\s'\"`=;&|(<>])\./|(?<={root}))"
                     rf"(?:{names}){end}")
    return re.compile("|".join(parts))


def _mcp_name(server: str) -> str:
    """A server name as Claude Code spells it in tool names: characters outside ``[A-Za-z0-9_-]`` become ``_``."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", server)

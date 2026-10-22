# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Checks that need every task base, not only the eval tree (contract §2, §3).

A task's ``base`` is usually older than the revision the manifest was read
from. Guidance that was deleted upstream after a base still exists at that
base, and the ``guidance:none`` arm must remove it there, so the globs are
evaluated per base. A base that no longer resolves would fail every run of its
task, so it is an error here.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .manifest import RepoCheckReport, match_glob
from .source import GitTree, RepoTree, SourceError

MCP_CONFIG = ".mcp.json"


def mcp_config_error(text: str) -> str | None:
    """Why ``text`` is not a ``.mcp.json`` the agent's CLI starts with (a JSON object whose ``mcpServers``, when
    there is one, is an object), or ``None``. The CLI exits before its first turn on anything else, which a
    run would grade as a task failure."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return f"not valid JSON ({exc})"
    except RecursionError:
        return "not valid JSON (nested too deeply)"
    if not isinstance(data, dict):
        return f"not a JSON object (got {type(data).__name__})"
    if "mcpServers" in data and not isinstance(data["mcpServers"], dict):
        return f"`mcpServers` is not an object (got {type(data['mcpServers']).__name__})"
    return None


def tree_mcp_error(tree: RepoTree) -> str | None:
    """``mcp_config_error`` of the ``.mcp.json`` at the root of ``tree``; ``None`` without one."""
    try:
        if not tree.is_file(MCP_CONFIG):
            return None
        return mcp_config_error(tree.read_text(MCP_CONFIG))
    except (SourceError, OSError, UnicodeDecodeError):
        return None


def check_mcp_configs(git_dir: Any, tasks: list[Any], profile: Any, tools: str) -> None:
    """Before a run: refuse a task base whose ``.mcp.json`` the profile would pass to the agent (the ``all`` tool
    mode) and the agent's CLI would not accept, instead of every cell of the task failing as a task failure."""
    if not profile.applied_args(tools, frozenset({MCP_CONFIG})):
        return
    for base in dict.fromkeys(t.base for t in tasks):
        problem = tree_mcp_error(GitTree(git_dir, base))
        if problem:
            raise ValueError(f"{MCP_CONFIG} at task base {base} is {problem}; `--tools all` passes it to the agent, "
                             "which exits on it before its first turn. Fix or remove the file, or use --tools read/none")


def check_bases(report: RepoCheckReport, open_base: Callable[[str], RepoTree],
                unmatched_at_eval_tree: list[str]) -> None:
    """Resolve each distinct task base and record what the guidance globs match there."""
    manifest = report.manifest
    by_base: dict[str, list[str]] = {}
    failed: set[str] = set()
    matched_somewhere: set[str] = set()
    for task in report.tasks:
        if task.base in by_base or task.base in failed:
            continue
        try:
            base_tree = open_base(task.base)
            files = base_tree.files()
        except SourceError as exc:
            report.errors.append(f"task {task.id}: base {task.base!r} does not resolve in {report.tree}: {exc}")
            failed.add(task.base)
            continue
        found: list[str] = []
        for pattern in manifest.guidance_globs:
            matched = [f for f in files if match_glob(pattern, f) and not manifest.in_eval_dir(f)]
            if matched:
                matched_somewhere.add(pattern)
            found.extend(m for m in matched if m not in found)
        by_base[task.base] = found
        problem = tree_mcp_error(base_tree)
        if problem:
            report.errors.append(f"task {task.id}: {MCP_CONFIG} at base {task.base!r} is {problem}; `repo run --tools all` "
                                 "would fail every cell of it as a task failure")

    report.guidance_by_base = {base: len(found) for base, found in by_base.items()}
    where = f"any of the {len(by_base)} task base(s) that resolved" if failed else "any task base"
    report.warnings.extend(
        f"guidance glob matches no file at the eval tree or at {where}: {p}"
        for p in unmatched_at_eval_tree if p not in matched_somewhere
    )
    bare = sorted(t.id for t in report.tasks if report.guidance_by_base.get(t.base) == 0)
    if bare:
        report.notes.append(
            f"no guidance file at the base of {len(bare)} task(s), so every guidance:* arm "
            f"is the same workspace there; leave them out of arm comparisons: {', '.join(bare)}"
        )


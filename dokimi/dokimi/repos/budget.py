# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: what a repository puts into an agent's context, before any run.

A static inventory, in characters, of the context a harness takes from the tree when it
starts at the repository root:

- **start**: guidance files at the root (with the files they ``@import``) and the skill
  listing (each project skill's ``name`` and ``description``). Every model call pays for these.
- **lazy**: guidance files in subdirectories, and what a nested CLAUDE.md imports, loaded
  when the agent works in that subtree.
- **on demand**: skill bodies, loaded when the agent invokes the skill.
- **off**: AGENTS.md files the harness will not read: a CLAUDE.md is at the root and no
  CLAUDE.md imports them (measured on Claude Code 2.1.286; the reach probe re-measures).

Characters, not tokens: how many tokens a character costs depends on the model's tokenizer,
and differs between model families. The exact cost is the first-call prompt difference
between a run with the file and one without.

Two checks ride along: skills whose descriptions are near-duplicates (the agent has to choose
between them from the listing alone), and AGENTS.md files turned off by a CLAUDE.md.
"""

from __future__ import annotations

import json
import re
from itertools import combinations
from typing import Any

from dokimi.repos.source import RepoTree

SKILL_GLOB = re.compile(r"^\.claude/skills/[^/]+/SKILL\.md$")
MCP_FILE = ".mcp.json"
IMPORT = re.compile(r"(?<![\w`])@([\w./-]+\.md)\b")
DUPLICATE_JACCARD = 0.5
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and the of to for in on with use when or is are be this that it its from by as at".split())


def budget(tree: RepoTree, guidance_names: tuple[str, ...]) -> dict[str, Any]:
    files = tree.files()
    names = set(guidance_names)
    guidance = [f for f in files if f.rsplit("/", 1)[-1] in names]
    claude = [f for f in guidance if f.rsplit("/", 1)[-1].startswith("CLAUDE")]
    root_claude = [f for f in claude if "/" not in f]
    # A root CLAUDE.md's imports load at start; a nested one's when the agent reaches its subtree.
    at_start = _imports(tree, root_claude)
    lazily = _imports(tree, [f for f in claude if "/" in f]) - at_start

    entries: list[dict[str, Any]] = []
    for f in guidance:
        if f.rsplit("/", 1)[-1] == "AGENTS.md" and root_claude and f not in at_start | lazily:
            when = "off"
        elif f in at_start or "/" not in f:
            when = "start"
        else:
            when = "lazy"
        entries.append({"path": f, "kind": "guidance", "when": when, "chars": _chars(tree, f)})
    for f in sorted((at_start | lazily) - set(guidance)):
        if tree.is_file(f):
            entries.append({"path": f, "kind": "import", "when": "start" if f in at_start else "lazy",
                            "chars": _chars(tree, f)})

    skills = _skills(tree, [f for f in files if SKILL_GLOB.match(f)])
    for s in skills:
        entries.append({"path": s["path"], "kind": "skill listing", "when": "start", "chars": s["listing_chars"]})
        entries.append({"path": s["path"], "kind": "skill body", "when": "on demand", "chars": s["body_chars"]})

    totals = {w: sum(e["chars"] for e in entries if e["when"] == w) for w in ("start", "lazy", "on demand", "off")}
    warnings = []
    off = [e["path"] for e in entries if e["when"] == "off"]
    if off:
        warnings.append(f"{', '.join(root_claude)} at the root turns AGENTS.md loading off and no CLAUDE.md "
                        f"imports these, so Claude Code reads none of {', '.join(off)} unless the agent opens "
                        "them (import each with `@AGENTS.md` from a CLAUDE.md beside it; `repo reach` measures it)")
    for s in skills:
        if not s["description"]:
            warnings.append(f"{s['path']}: no description; the listing gives the agent nothing to choose it by")
    duplicates = _near_duplicates(skills)
    for d in duplicates:
        warnings.append(f"skills {d['a']} and {d['b']} have near-duplicate descriptions "
                        f"(word overlap {d['overlap']:.2f}); the listing is all the agent picks from")
    return {
        "tree": tree.label,
        "entries": entries,
        "totals": totals,
        "skills": len(skills),
        "duplicate_skills": duplicates,
        "mcp_servers": _mcp_servers(tree),
        "warnings": warnings,
    }


def _chars(tree: RepoTree, path: str) -> int:
    try:
        return len(tree.read_text(path))
    except (OSError, UnicodeDecodeError, ValueError):
        return 0


def _imports(tree: RepoTree, roots: list[str]) -> set[str]:
    """Files the root CLAUDE.md files ``@import``, transitively (relative to the importing file)."""
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        f = todo.pop()
        try:
            text = tree.read_text(f)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        here = f.rsplit("/", 1)[0] + "/" if "/" in f else ""
        for target in IMPORT.findall(text):
            path = _normalize(here + target)
            if path and path not in seen:
                seen.add(path)
                todo.append(path)
    return seen


def _normalize(path: str) -> str:
    parts: list[str] = []
    for p in path.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if not parts:
                return ""  # outside the tree
            parts.pop()
        else:
            parts.append(p)
    return "/".join(parts)


def _skills(tree: RepoTree, paths: list[str]) -> list[dict[str, Any]]:
    from dokimi.utils import parse_frontmatter

    out = []
    for path in paths:
        try:
            meta, body = parse_frontmatter(tree.read_text(path))
        except (OSError, UnicodeDecodeError, ValueError):
            meta, body = {}, ""
        meta = meta if isinstance(meta, dict) else {}
        name = str(meta.get("name") or path.split("/")[-2])
        description = str(meta.get("description") or "")
        out.append({"path": path, "name": name, "description": description,
                    "listing_chars": len(name) + len(description), "body_chars": len(body)})
    return out


def _near_duplicates(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words = {s["name"]: set(_WORD.findall(s["description"].lower())) - _STOP for s in skills}
    out = []
    for a, b in combinations(sorted(words), 2):
        union = words[a] | words[b]
        if not union:
            continue
        overlap = len(words[a] & words[b]) / len(union)
        if overlap >= DUPLICATE_JACCARD:
            out.append({"a": a, "b": b, "overlap": round(overlap, 2)})
    return out


def _mcp_servers(tree: RepoTree) -> list[str]:
    if not tree.is_file(MCP_FILE):
        return []
    try:
        servers = json.loads(tree.read_text(MCP_FILE)).get("mcpServers") or {}
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return []
    return sorted(servers) if isinstance(servers, dict) else []

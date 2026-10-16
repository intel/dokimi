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

Three checks ride along: skills with no description, skills whose descriptions are near-duplicates
(the agent has to choose between them from the listing alone), and AGENTS.md files turned off by a
CLAUDE.md at the root.
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
# Claude Code does not follow an @path inside a code block or a code span.
_CODE_BLOCK = re.compile(r"^ {0,3}(`{3,}|~{3,}).*?(?:^ {0,3}\1[`~]*[ \t]*$|\Z)", re.S | re.M)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
DUPLICATE_JACCARD = 0.5
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and the of to for in on with use when or is are be this that it its from by as at".split())


def budget(tree: RepoTree, guidance_names: tuple[str, ...]) -> dict[str, Any]:
    files = tree.files()
    names = set(guidance_names)
    guidance = _unaliased(tree, [f for f in files if f.rsplit("/", 1)[-1] in names])
    claude = [f for f in guidance if f.rsplit("/", 1)[-1].startswith("CLAUDE")]
    root_claude = [f for f in claude if _is_root(f)]
    # A root CLAUDE.md's imports load at start; a nested one's when the agent reaches its subtree.
    at_start = _imports(tree, root_claude)
    lazily = _imports(tree, [f for f in claude if f not in root_claude]) - at_start

    entries: list[dict[str, Any]] = []
    unreadable_warnings: list[str] = []
    for f in guidance:
        if f.rsplit("/", 1)[-1] == "AGENTS.md" and root_claude and f not in at_start | lazily:
            when = "off"
        elif f in at_start or _is_root(f):
            when = "start"
        else:
            when = "lazy"
        chars, warning = _chars(tree, f)
        if warning:
            unreadable_warnings.append(warning)
        entries.append({"path": f, "kind": "guidance", "when": when, "chars": chars})
    counted = {_target(tree, f) for f in guidance}
    for f in sorted((at_start | lazily) - set(guidance), key=lambda p: (p not in at_start, p)):
        if _is_file(tree, f) and _target(tree, f) not in counted:
            counted.add(_target(tree, f))
            chars, warning = _chars(tree, f)
            if warning:
                unreadable_warnings.append(warning)
            entries.append({"path": f, "kind": "import", "when": "start" if f in at_start else "lazy",
                            "chars": chars})

    skills = _skills(tree, [f for f in files if SKILL_GLOB.match(f)])
    for s in skills:
        entries.append({"path": s["path"], "kind": "skill listing", "when": "start", "chars": s["listing_chars"]})
        entries.append({"path": s["path"], "kind": "skill body", "when": "on demand", "chars": s["body_chars"]})

    totals = {w: sum(e["chars"] for e in entries if e["when"] == w) for w in ("start", "lazy", "on demand", "off")}
    warnings = unreadable_warnings[:]
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


def _is_root(path: str) -> bool:
    """Guidance Claude Code reads at start: at the root, or the project memory in `.claude/`."""
    return "/" not in path or path == ".claude/CLAUDE.md"


def _target(tree: RepoTree, path: str) -> str:
    """The file ``path`` is once links are followed; ``path`` itself if it is not a link or cannot be resolved."""
    try:
        return tree.resolve(path)
    except (OSError, ValueError):
        return path


def _unaliased(tree: RepoTree, guidance: list[str]) -> list[str]:
    """One path per file: an AGENTS.md linked to a CLAUDE.md (or the reverse) is the same bytes in context."""
    groups: dict[str, list[str]] = {}
    for f in guidance:
        groups.setdefault(_target(tree, f), []).append(f)
    keep = {min(g, key=lambda p: (not p.rsplit("/", 1)[-1].startswith("CLAUDE"), p)) for g in groups.values()}
    return [f for f in guidance if f in keep]


def _is_file(tree: RepoTree, path: str) -> bool:
    try:
        return tree.is_file(path)
    except (OSError, ValueError):  # a link that leaves the tree is not a file of it
        return False


def _chars(tree: RepoTree, path: str) -> tuple[int, str | None]:
    """Return (char_count, warning_message). warning_message is None if the file is readable."""
    try:
        return len(tree.read_text(path)), None
    except (OSError, ValueError):
        return 0, None  # File does not exist or outside the tree; no warning needed
    except UnicodeDecodeError:
        return 0, f"{path}: not readable (non-UTF-8 or binary), counted as 0"


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
        for target in IMPORT.findall(_CODE_SPAN.sub("", _CODE_BLOCK.sub("", text))):
            if target.startswith("/"):
                continue  # an absolute path names the host filesystem, not a file of the tree
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
    """Pairs of skills with similar descriptions, named by ``name`` (by path when two share a name)."""
    taken = [s["name"] for s in skills]
    label = [s["path"] if taken.count(s["name"]) > 1 else s["name"] for s in skills]
    words = [set(_WORD.findall(s["description"].lower())) - _STOP for s in skills]
    out = []
    for i, j in combinations(sorted(range(len(skills)), key=lambda k: (label[k], k)), 2):
        union = words[i] | words[j]
        if not union:
            continue
        overlap = len(words[i] & words[j]) / len(union)
        if overlap >= DUPLICATE_JACCARD:
            out.append({"a": label[i], "b": label[j], "overlap": round(overlap, 2)})
    return out


def _mcp_servers(tree: RepoTree) -> list[str]:
    if not _is_file(tree, MCP_FILE):
        return []
    try:
        servers = json.loads(tree.read_text(MCP_FILE)).get("mcpServers") or {}
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return []
    return sorted(servers) if isinstance(servers, dict) else []

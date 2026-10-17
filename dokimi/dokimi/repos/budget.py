# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: what a repository puts into an agent's context, before any run.

A static inventory, in characters, of the context a harness takes from the tree when it
starts at the repository root:

- **start**: guidance files at the root, the files a root CLAUDE.md ``@import``s (imports are
  followed from ``CLAUDE*`` files only), and the skill
  listing (each project skill's ``name``, plus its ``description`` and ``when_to_use`` up to
  1,536 characters together; a skill with
  ``disable-model-invocation: true`` is not listed). Every model call pays for these.
- **lazy**: guidance files in subdirectories, and what a nested CLAUDE.md imports, loaded
  when the agent works in that subtree.
- **on demand**: skill bodies, loaded when the agent invokes the skill.
- **off**: AGENTS.md files the harness will not read: a CLAUDE.md is at the root and no
  CLAUDE.md imports them (measured on Claude Code 2.1.286; the reach probe re-measures).

Characters, not tokens: how many tokens a character costs depends on the model's tokenizer,
and differs between model families. The exact cost is the first-call prompt difference
between a run with the file and one without.

Four checks ride along: skills with no description, skills whose descriptions are near-duplicates
(the agent has to choose between them from the listing alone), AGENTS.md files turned off by a
CLAUDE.md at the root, and unreadable files (non-UTF-8, dangling link).
"""

from __future__ import annotations

import json
import re
from itertools import combinations
from typing import Any

from dokimi.repos.source import RepoTree

SKILL_GLOB = re.compile(r"^\.claude/skills/[^/]+/SKILL\.md$")
MCP_FILE = ".mcp.json"
# Claude Code truncates description + when_to_use at 1,536 characters in the listing (docs: Skills).
LISTING_TEXT_CAP = 1536
IMPORT = re.compile(r"(?<![\w`])@([\w./-]+\.md)\b")
# Claude Code does not follow an @path inside a code block or a code span.
_CODE_BLOCK = re.compile(r"^ {0,3}(`{3,}|~{3,}).*?(?:^ {0,3}\1[`~]*[ \t]*$|\Z)", re.S | re.M)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
DUPLICATE_JACCARD = 0.5
_WORD = re.compile(r"\w+")
_STOP = frozenset("a an and the of to for in on with use when or is are be this that it its from by as at".split())


def budget(tree: RepoTree, guidance_names: tuple[str, ...]) -> dict[str, Any]:
    files = tree.files()
    names = set(guidance_names)
    every = [f for f in files if f.rsplit("/", 1)[-1] in names]
    guidance = _unaliased(tree, every)
    groups: dict[str, list[str]] = {}
    for f in every:
        groups.setdefault(_target(tree, f), []).append(f)
    # Rules are decided from every path that reaches a file, not only the one shown.
    claude = [f for f in every if f.rsplit("/", 1)[-1].startswith("CLAUDE")]
    # Only a root CLAUDE.md (not .claude/CLAUDE.md or CLAUDE.local.md) can turn AGENTS.md off
    root_claude = [f for f in claude if _is_root(f)]
    # For off rule: only root CLAUDE.md that resolves (not dangling/out-of-tree); unreadable UTF-8 still counts
    root_claude_for_off = [f for f in root_claude if f == "CLAUDE.md" and _is_resolvable(tree, f)]
    root_claude_readable = [f for f in root_claude if _is_resolvable(tree, f)]
    # A root CLAUDE.md's imports load at start; a nested one's when the agent reaches its subtree.
    at_start = _imports(tree, root_claude_readable)
    lazily = _imports(tree, [f for f in claude if f not in root_claude]) - at_start

    start_t = {_target(tree, p) for p in at_start}
    loaded_t = start_t | {_target(tree, p) for p in lazily}

    entries: list[dict[str, Any]] = []
    unreadable_warnings: list[str] = []
    for f in guidance:
        t = _target(tree, f)
        aliases = groups.get(t, [f])
        root_alias = any(a in root_claude for a in aliases)
        nested_alias = any(a in claude and a not in root_claude for a in aliases)
        if f.rsplit("/", 1)[-1] == "AGENTS.md" and root_claude_for_off:
            # a root CLAUDE.md displaces AGENTS.md: it is read only through a CLAUDE.md that reaches it
            if root_alias or t in start_t:
                when = "start"
            elif nested_alias or t in loaded_t:
                when = "lazy"
            else:
                when = "off"
        elif root_alias or t in start_t or _is_root(f):
            when = "start"
        else:
            when = "lazy"
        chars, warning = _chars(tree, f)
        if warning:
            unreadable_warnings.append(warning)
        entries.append({"path": f, "kind": "guidance", "when": when, "chars": chars})
    counted = {_target(tree, f) for f in guidance}
    for f in sorted((at_start | lazily) - set(guidance), key=lambda p: (p not in at_start, p)):
        if _is_resolvable(tree, f) and _target(tree, f) not in counted:
            counted.add(_target(tree, f))
            chars, warning = _chars(tree, f)
            if warning:
                unreadable_warnings.append(warning)
            entries.append({"path": f, "kind": "import", "when": "start" if f in at_start else "lazy",
                            "chars": chars})

    skills = _skills(tree, [f for f in files if SKILL_GLOB.match(f)])
    for s in skills:
        if s["listed"]:
            entries.append({"path": s["path"], "kind": "skill listing", "when": "start", "chars": s["listing_chars"]})
        entries.append({"path": s["path"], "kind": "skill body", "when": "on demand", "chars": s["body_chars"]})

    totals = {w: sum(e["chars"] for e in entries if e["when"] == w) for w in ("start", "lazy", "on demand", "off")}
    warnings = unreadable_warnings[:]
    off = [e["path"] for e in entries if e["when"] == "off"]
    if off:
        warnings.append(f"{', '.join(root_claude_for_off)} turns AGENTS.md loading off and no CLAUDE.md "
                        f"imports these, so Claude Code reads none of {', '.join(off)} unless the agent opens "
                        "them (import each with `@AGENTS.md` from a CLAUDE.md beside it; `repo reach` measures it)")
    for s in skills:
        if s["unreadable"]:
            warnings.append(f"{s['path']}: not readable ({s['unreadable']}), body counted as 0; "
                            "the listing falls back to the directory name")
        elif s["listed"] and not s["description"]:
            warnings.append(f"{s['path']}: no description; the listing gives the agent nothing to choose it by")
    duplicates = _near_duplicates([s for s in skills if s["listed"]])
    for d in duplicates:
        warnings.append(f"skills {d['a']} and {d['b']} have near-duplicate descriptions "
                        f"(Jaccard {d['overlap']:.2f} on words, stop words removed); the listing is all the agent picks from")
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
    keep = {
        min(g, key=lambda p: (p.count("/"), not p.rsplit("/", 1)[-1].startswith("CLAUDE"), p))
        for g in groups.values()
    }
    return [f for f in guidance if f in keep]


def _is_resolvable(tree: RepoTree, path: str) -> bool:
    """True if the file path resolves (not a dangling link, loop, or out-of-tree)."""
    try:
        tree.resolve(path)  # SourceError if it dangles, loops or escapes
        return tree.is_file(path)
    except (OSError, ValueError):
        return False


def _unreadable_reason(err: Exception) -> str:
    if isinstance(err, UnicodeDecodeError):
        return "non-UTF-8 or binary"
    return "not found, broken link, or outside tree"  # dangling symlink, loop, escape, source error


def _chars(tree: RepoTree, path: str) -> tuple[int, str | None]:
    """Return (char_count, warning_message). Warn on all unreadable files: dangling/out-of-tree links, bad encoding."""
    try:
        return len(tree.read_text(path)), None
    except (OSError, ValueError) as err:  # UnicodeDecodeError is a ValueError
        return 0, f"{path}: not readable ({_unreadable_reason(err)}), counted as 0"


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
        unreadable: str | None = None
        try:
            meta, body = parse_frontmatter(tree.read_text(path))
        except (OSError, ValueError) as err:  # UnicodeDecodeError is a ValueError
            meta, body, unreadable = {}, "", _unreadable_reason(err)
        meta = meta if isinstance(meta, dict) else {}
        name = str(meta.get("name") or path.split("/")[-2])
        description = str(meta.get("description") or "")
        # The CLI reads a boolean as 1/true/yes/on (as harnesses/skill_treatment.py does); such a
        # skill is left out of the listing, so the model never sees its name or description.
        hidden = str(meta.get("disable-model-invocation", "")).strip().lower() in ("1", "true", "yes", "on")
        listed_text = len(description) + len(str(meta.get("when_to_use") or ""))
        out.append({"path": path, "name": name, "description": description,
                    "listing_chars": len(name) + min(listed_text, LISTING_TEXT_CAP),
                    "body_chars": len(body),
                    "unreadable": unreadable, "listed": not hidden})
    return out


def _near_duplicates(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pairs of skills with similar descriptions, named by ``name`` (by path when two share a name).

    Reports exact duplicates (words[i] == words[j]) always, and partial overlaps (Jaccard >= 0.5)
    only when both descriptions have 3+ meaningful tokens after stop-word removal.
    """
    taken = [s["name"] for s in skills]
    label = [s["path"] if taken.count(s["name"]) > 1 else s["name"] for s in skills]
    words = [set(_WORD.findall(s["description"].lower())) - _STOP for s in skills]
    out = []
    for i, j in combinations(sorted(range(len(skills)), key=lambda k: (label[k], k)), 2):
        union = words[i] | words[j]
        if not union:
            continue
        overlap = len(words[i] & words[j]) / len(union)
        is_exact = words[i] == words[j] and words[i]  # Exact match and non-empty
        is_partial = overlap >= DUPLICATE_JACCARD and len(words[i]) >= 3 and len(words[j]) >= 3
        if is_exact or is_partial:
            out.append({"a": label[i], "b": label[j], "overlap": round(overlap, 2)})
    return out


def _mcp_servers(tree: RepoTree) -> list[str]:
    if not _is_resolvable(tree, MCP_FILE):
        return []
    try:
        servers = json.loads(tree.read_text(MCP_FILE)).get("mcpServers") or {}
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return []
    return sorted(servers) if isinstance(servers, dict) else []

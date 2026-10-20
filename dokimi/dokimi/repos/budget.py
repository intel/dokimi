# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: what a repository puts into an agent's context, before any run.

A static inventory, in characters, of the context a harness takes from the tree when it
starts at the repository root:

- **start**: guidance files at the root, the files a root ``CLAUDE*`` file ``@import``s (imports are
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

Five checks ride along: skills with no description, skills whose descriptions are near-duplicates
(the agent has to choose between them from the listing alone), AGENTS.md files turned off by a
CLAUDE.md at the root, unreadable files (non-UTF-8, dangling link), and imports past the
harness's hop limit.
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
# Claude Code's import path runs from the @ to the next whitespace: `(@x.md)` or `@x.md.` imports nothing.
IMPORT = re.compile(r"(?<!\S)@(\S+\.md)(?!\S)")
# Claude Code does not follow an @path inside a code block or a code span. Only top-level fences
# are handled (not one inside a blockquote or list item, nor an indented code block). A backtick
# fence's info string has no backtick (CommonMark), so ```make``` at a line start is a code span.
# An unclosed fence runs to the end of the file.
_CODE_BLOCK = re.compile(r"^ {0,3}(`{3,}(?=[^`\n]*$)|~{3,}).*?(?:^ {0,3}\1[`~]*[ \t]*$|\Z)", re.S | re.M)
# A code span may wrap onto the next line of its paragraph (CommonMark), but not across a blank
# line, onto a heading, quote, list item or underline line, or past the end of a heading.
_BACKTICKS = re.compile(r"`+")
_BLOCK_BREAK = re.compile(
    r"\n(?:[ \t]*\n| {0,3}(?:#{1,6}(?:[ \t]|$)|>|[-*+][ \t]|\d{1,9}[.)][ \t]|=+[ \t]*$|(?:[-*_][ \t]*)+$))", re.M)
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t][^\n]*)?$", re.M)
DUPLICATE_JACCARD = 0.5
_WORD = re.compile(r"\w+")
_STOP = frozenset("a an and the of to for in on with use when or is are be this that it its from by as at".split())


def budget(tree: RepoTree, guidance_names: tuple[str, ...], import_max_depth: int | None = None) -> dict[str, Any]:
    files = tree.files()
    names = set(guidance_names)
    every = [f for f in files if f.rsplit("/", 1)[-1] in names]
    guidance = _unaliased(tree, every)
    groups: dict[str, list[str]] = {}
    for f in every:
        groups.setdefault(_target(tree, f), []).append(f)
    # Rules are decided from every path that reaches a file, not only the one shown.
    claude = [f for f in every if f.rsplit("/", 1)[-1].startswith("CLAUDE")]
    root_claude = [f for f in claude if _is_root(f)]
    # Claude reads AGENTS.md only when no CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md is in the
    # working directory or above it, so any root one of those turns AGENTS.md off. Only those that
    # resolve (not dangling/out-of-tree) count; unreadable UTF-8 still counts.
    root_claude_for_off = [f for f in root_claude if _is_resolvable(tree, f)]
    # A root CLAUDE.md's imports load at start; a nested one's when the agent reaches its subtree.
    at_start, cut_start = _imports(tree, root_claude_for_off, import_max_depth)
    lazy_all, cut_lazy = _imports(tree, [f for f in claude if f not in root_claude], import_max_depth)

    # An AGENTS.md Claude reads on its own expands its @imports from hop 0: root ones at start, nested
    # ones when the agent reaches their subtree. One a root CLAUDE* displaces is read only through an
    # import, whose chain already follows it at its real hop count, unless it is a root CLAUDE* alias.
    agents = [f for f in every if f.rsplit("/", 1)[-1] == "AGENTS.md" and _is_resolvable(tree, f)
              and (not root_claude_for_off or any(a in root_claude for a in groups.get(_target(tree, f), [f])))]
    agents_at_start, cut_agents_start = _imports(tree, [f for f in agents if _is_root(f)], import_max_depth)
    agents_lazy, cut_agents_lazy = _imports(tree, [f for f in agents if not _is_root(f)], import_max_depth)
    at_start |= agents_at_start
    lazy_all |= agents_lazy
    cut_start = cut_start + cut_agents_start
    cut_lazy = cut_lazy + cut_agents_lazy
    lazily = lazy_all - at_start
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
    loaded = {_target(tree, p) for p in at_start | lazy_all}
    for f, target in sorted(set(cut_start + cut_lazy)):
        if _target(tree, target) in loaded:
            continue  # another chain did load it, possibly through a link
        if not _is_resolvable(tree, target):
            continue  # nothing to load, as with a missing import within the limit
        warnings.append(f"{f}: import of {target} is past the harness's {import_max_depth}-hop import limit, "
                        "not followed")
    off = [e["path"] for e in entries if e["when"] == "off"]
    if off:
        warnings.append(f"{', '.join(root_claude_for_off)} turns AGENTS.md loading off and no CLAUDE.md "
                        f"imports these, so Claude Code reads none of {', '.join(off)} unless the agent opens "
                        "them (import each with `@AGENTS.md` from a CLAUDE.md beside it; `repo reach` measures it)")
    for s in skills:
        if s["unreadable"]:
            warnings.append(f"{s['path']}: not readable ({s['unreadable']}), body counted as 0")
        elif s["listed"] and not s["description"]:
            warnings.append(f"{s['path']}: no description and an empty body; the listing gives the agent nothing "
                            "to choose it by")
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
    return "/" not in path or path in (".claude/CLAUDE.md", ".claude/AGENTS.md")


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
        # SourceError if it dangles, loops or escapes; a linked directory in the path is followed
        return tree.is_file(tree.resolve(path))
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
    except (OSError, ValueError, RuntimeError) as err:  # RuntimeError: a link loop before Python 3.13  # UnicodeDecodeError is a ValueError
        return 0, f"{path}: not readable ({_unreadable_reason(err)}), counted as 0"


def _imports(tree: RepoTree, roots: list[str], max_depth: int | None = None) -> tuple[set[str], list[tuple[str, str]]]:
    """Files the CLAUDE.md ``roots`` ``@import``, transitively (relative to the importing file).

    Breadth first, so a file is placed at its shortest hop count. With ``max_depth``, imports of
    files already ``max_depth`` hops from a root are not followed; those left unreached come back
    as ``(importing file, target)`` pairs.
    """
    seen: set[str] = set()
    # Also by the file a path resolves to: an alias (CLAUDE.md -> .) names the same file under
    # ever longer paths, which a path-string check never recognises as a cycle.
    reached = {_target(tree, r) for r in roots}
    level = list(roots)
    cut: list[tuple[str, str]] = []
    depth = 0
    while level:
        nxt: list[str] = []
        for f in level:
            try:
                text = tree.read_text(f)
            except (OSError, ValueError, RuntimeError):  # RuntimeError: a link loop before Python 3.13
                continue
            here = f.rsplit("/", 1)[0] + "/" if "/" in f else ""
            for target in IMPORT.findall(_strip_code_spans(_CODE_BLOCK.sub("", text))):
                if target.startswith("/"):
                    continue  # an absolute path names the host filesystem, not a file of the tree
                path = _normalize(here + target)
                if not path or path in seen or _target(tree, path) in reached:
                    continue
                if max_depth is not None and depth >= max_depth:
                    cut.append((f, path))
                    continue
                seen.add(path)
                reached.add(_target(tree, path))
                nxt.append(path)
        level = nxt
        depth += 1
    return seen, [(f, p) for f, p in cut if p not in seen]


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
        except (OSError, ValueError, RuntimeError) as err:  # UnicodeDecodeError is a ValueError; RuntimeError a loop
            meta, body, unreadable = {}, "", _unreadable_reason(err)
        meta = meta if isinstance(meta, dict) else {}
        name = str(meta.get("name") or path.split("/")[-2])
        # Claude Code uses the first non-empty line of the body when the description is omitted
        description = str(meta.get("description") or "") or next(
            (line.strip() for line in body.splitlines() if line.strip()), "")
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


def _strip_code_spans(text: str) -> str:
    """Remove code spans: a backtick run closed by the next run of equal length, not across a block break.

    A linear scan rather than a regex, which backtracks exponentially on long backtick runs.
    """
    runs = [(m.start(), m.end()) for m in _BACKTICKS.finditer(text)]
    blanks = sorted([m.start() for m in _BLOCK_BREAK.finditer(text)] + [m.end() for m in _HEADING.finditer(text)])
    # closer[i]: index of the next run after i with the same length, or None.
    closer: list[int | None] = [None] * len(runs)
    seen: dict[int, int] = {}
    for i in range(len(runs) - 1, -1, -1):
        n = runs[i][1] - runs[i][0]
        closer[i] = seen.get(n)
        seen[n] = i
    out, pos, b, i = [], 0, 0, 0
    while i < len(runs):
        start, end = runs[i]
        j = closer[i]
        while b < len(blanks) and blanks[b] < end:
            b += 1
        if j is not None and (b == len(blanks) or runs[j][0] < blanks[b]):
            out.append(text[pos:start])
            pos = runs[j][1]
            i = j + 1
        else:
            i += 1
    out.append(text[pos:])
    return "".join(out)

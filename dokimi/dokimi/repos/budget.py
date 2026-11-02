# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: what a repository puts into an agent's context, before any run.

A static inventory, in characters, of the context a harness takes from the tree when it
starts at the repository root:

- **start**: guidance files at the root, the files a root ``CLAUDE*`` or ``AGENTS.md`` file
  ``@import``s (``AGENTS.md`` only when it is not turned off), the root ``.claude/rules/**/*.md``
  files whose ``paths`` frontmatter names no pattern, when the harness reads rules (their own ``@`` paths are not
  imports unless a CLAUDE.md imports the rule; then they are, transitively), and the skill
  listing (each project skill's ``name``, plus its ``description`` and ``when_to_use`` up to
  1,536 characters together; a skill with
  ``disable-model-invocation: true`` is not listed). Every model call pays for these.
- **lazy**: guidance files in subdirectories, what a nested CLAUDE.md or AGENTS.md imports, and the
  rules of a subdirectory's ``.claude/rules/``, loaded when the agent works in that subtree; rules
  whose ``paths`` names a pattern, loaded when the agent reads a matching file.
- **on demand**: skill bodies, loaded when the agent invokes the skill.
- **off**: AGENTS.md files the harness will not read: a root CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md is present and no
  CLAUDE.md imports them (measured on Claude Code 2.1.286; the reach probe re-measures).

Characters, not tokens: how many tokens a character costs depends on the model's tokenizer,
and differs between model families. The exact cost is the first-call prompt difference
between a run with the file and one without.

Six checks ride along: skills with no description and no body text, skills whose descriptions are near-duplicates
(the agent has to choose between them from the listing alone), AGENTS.md files turned off by a
CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md at the root, unreadable files (non-UTF-8, dangling link), imports past the
harness's hop limit, and links under ``.claude/rules`` to a directory or out of the tree, whose rules
are not counted.
"""

from __future__ import annotations

import json
import re
from itertools import combinations
from typing import Any

import yaml

from dokimi.repos.source import RepoTree

SKILL_GLOB = re.compile(r"^\.claude/skills/[^/]+/SKILL\.md$")
# Rules are discovered recursively, in the root .claude/rules/ and in a subdirectory's (docs: Memory).
RULE_GLOB = re.compile(r"^(?:.+/)?\.claude/rules/(?:.+/)?[^/]+\.md$")
_RULES_ENTRY = re.compile(r"^(?:.+/)?\.claude/rules(?:/.+)?$")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)
# The `paths:` key of frontmatter that does not parse: its line, read on its own (see _paths_block).
_PATHS_LINE = re.compile(r"^paths[ \t]*:(.*)$")
_INDENTED_KEY = re.compile(r"^[ \t]+(?:[A-Za-z0-9_][^:#]*|\"[^\"]*\"|'[^']*'):(?:[ \t]|$)")  # `  unused: ...`: a key, not a paths continuation
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


def _nested_unscoped(tree: RepoTree, files) -> set:
    """(rule target, directory above its .claude/rules) for each unscoped rule in a nested .claude/rules."""
    return {(_target(tree, f), f.split(".claude/rules/", 1)[0]) for f in files
            if RULE_GLOB.match(f) and not f.startswith(".claude/rules/") and not _rule(tree, f)[1]}


def _cut_is_quiet(tree: RepoTree, f: str, target: str, loaded, cut_lazy_set: set, nested_unscoped: set) -> bool:
    """True when a cut import needs no warning: another chain loaded its target, or it is a cut lazy import of an
    unscoped nested rule from guidance in that rule's subtree (the rule loads with that guidance anyway)."""
    if _target(tree, target) in loaded:
        return True  # another chain did load it, possibly through a link
    return (f, target) in cut_lazy_set and any(
        _target(tree, target) == t and f.startswith(sub) for t, sub in nested_unscoped)


def budget(tree: RepoTree, guidance_names: tuple[str, ...], import_max_depth: int | None = None,
           rules: bool = False) -> dict[str, Any]:
    files = tree.files()
    names = set(guidance_names)
    # under .claude/rules a file is a rule whatever its name (a rules/CLAUDE.md is not guidance), when
    # the harness reads rules; otherwise it is an ordinary file of the profile's guidance names
    every = [f for f in files if f.rsplit("/", 1)[-1] in names and not (rules and RULE_GLOB.match(f))]
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
    at_start, cut_start = _imports(tree, root_claude_for_off, import_max_depth, rules)
    lazy_all, cut_lazy = _imports(tree, [f for f in claude if f not in root_claude], import_max_depth, rules)

    # An AGENTS.md Claude reads on its own expands its @imports from hop 0: root ones at start, nested
    # ones when the agent reaches their subtree. One a root CLAUDE* displaces is read only through an
    # import, whose chain already follows it at its real hop count, unless it is a root CLAUDE* alias.
    agents = [f for f in every if f.rsplit("/", 1)[-1] == "AGENTS.md" and _is_resolvable(tree, f)
              and (not root_claude_for_off or any(a in root_claude for a in groups.get(_target(tree, f), [f])))]
    agents_at_start, cut_agents_start = _imports(tree, [f for f in agents if _is_root(f)], import_max_depth, rules)
    agents_lazy, cut_agents_lazy = _imports(tree, [f for f in agents if not _is_root(f)], import_max_depth, rules)
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
    # What loads without a rule: a rule promotes a guidance entry below, but that does not make it independently loaded
    guidance_loaded = {_target(tree, e["path"]) for e in entries if e["when"] != "off"}
    counted = {_target(tree, f) for f in guidance}
    entries += _rules(tree, files if rules else [], counted, start_t, unreadable_warnings, entries)
    rule_entries = {_target(tree, e["path"]): e for e in entries if e["kind"] == "rule"}
    for f in sorted((at_start | lazily) - set(guidance), key=lambda p: (p not in at_start, p)):
        if not _is_resolvable(tree, f):
            continue
        t = _target(tree, f)
        when = "start" if f in at_start else "lazy"
        if t in counted:
            # A rule link counted this file with its frontmatter removed. An ordinary @import keeps the frontmatter,
            # so its count replaces the rule's when it loads at the same time or earlier (a rule file is left alone).
            rule = rule_entries.get(t)
            if rule is None or RULE_GLOB.match(f) or RULE_GLOB.match(t) or (when == "lazy" and rule["when"] == "start"):
                continue
            chars, warning = _chars(tree, f)
            if warning:
                unreadable_warnings.append(warning)
            rule.update(path=f, kind="import", when=when, chars=chars)
            continue
        counted.add(t)
        chars, warning = _chars(tree, f)
        if warning:
            unreadable_warnings.append(warning)
        entries.append({"path": f, "kind": "import", "when": when, "chars": chars})

    skills = _skills(tree, [f for f in files if SKILL_GLOB.match(f)])
    for s in skills:
        if s["listed"]:
            entries.append({"path": s["path"], "kind": "skill listing", "when": "start", "chars": s["listing_chars"]})
        entries.append({"path": s["path"], "kind": "skill body", "when": "on demand", "chars": s["body_chars"]})

    totals = {w: sum(e["chars"] for e in entries if e["when"] == w) for w in ("start", "lazy", "on demand", "off")}
    warnings = unreadable_warnings[:]
    # A guidance file or a rule is an entry of its own, so a cut import of it is already loaded (not an import).
    # A scoped rule (lazy) loads only when a matching file is read, so it does not cover a cut import at start.
    loaded = {_target(tree, p) for p in at_start | lazy_all} | guidance_loaded | {
        _target(tree, e["path"]) for e in entries if e["when"] == "start" and e["kind"] in ("guidance", "rule")}
    # An unscoped nested rule loads for any work in its subtree, so a cut import of it there skips nothing.
    # A path-scoped rule, or one imported from outside its subtree, does not load with its importer: still named.
    nested_unscoped = _nested_unscoped(tree, files) if rules else set()
    cut_lazy_set = set(cut_lazy)
    for f, target in sorted(set(cut_start + cut_lazy)):
        if _cut_is_quiet(tree, f, target, loaded, cut_lazy_set, nested_unscoped):
            continue
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


def _rules(tree: RepoTree, files: list[str], counted: set[str], start_t: set[str],
           warnings: list[str], guidance: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ``rule`` entries of ``files``, skipping files already ``counted`` (and adding theirs).

    A rule that is the same file as a guidance entry (in ``guidance``) is not counted twice: that entry
    takes the earlier of the two loading conditions, so a root rule linked to an AGENTS.md that a root
    CLAUDE.md turns off loads it at start.

    ``budget`` passes no files for a harness that does not read ``.claude/rules`` (``guidance.claude_rules``).
    """
    rank = {"off": 0, "lazy": 1, "start": 2}
    by_target = {_target(tree, e["path"]): e for e in guidance}
    out = []
    dirs: set[str] = set()
    for f in sorted(f for f in files if _RULES_ENTRY.match(f)):
        try:
            if tree.is_dir(tree.resolve(f, dirs=True)):  # a tree lists a link to a directory as one entry
                warnings.append(f"{f}: a link to a directory; the rules behind it are not counted")
                dirs.add(f)
        except (OSError, ValueError, RuntimeError):  # out of the tree (a shared rules dir), dangling or a loop
            # A link that does not resolve may be a directory whatever its name, and the tree cannot tell which:
            # any such entry is named, except a Markdown rule, which _rule names as unreadable and counts as 0.
            if not RULE_GLOB.match(f):
                warnings.append(f"{f}: a link that does not resolve in the tree; any rules behind it are not counted")
    for f in sorted(f for f in files if RULE_GLOB.match(f) and f not in dirs):
        target = _target(tree, f)
        chars, scoped, warning = _rule(tree, f)
        # a CLAUDE.md that imports a rule at start loads it then, whatever its frontmatter says
        when = "start" if (f.startswith(".claude/rules/") and not scoped) or target in start_t else "lazy"
        if target in counted:
            # a link to a file already counted (a guidance file, or another rule): the same bytes, loaded once.
            # The entry takes the earlier loading condition, and that route's count: a rule has its frontmatter removed.
            entry = by_target.get(target)
            if entry is not None and rank[when] > rank[entry["when"]]:
                entry["when"] = when
                entry["chars"] = chars
            continue
        counted.add(target)
        if warning:
            warnings.append(warning)
        entry = {"path": f, "kind": "rule", "when": when, "chars": chars}
        out.append(entry)
        by_target[target] = entry
    return out


def _rule(tree: RepoTree, path: str) -> tuple[int, bool, str | None]:
    """Return (chars in context, has ``paths``, warning) for a ``.claude/rules`` file.

    Claude Code removes the frontmatter before loading a rule, so it is not counted. A rule with
    a non-empty ``paths`` loads only when the agent reads a matching file. Measured on Claude Code
    2.1.304, the key is read line by line: it holds even when the rest of the YAML does not parse
    (the docs say such a rule loads unconditionally), while ``paths: []`` loads at start. A rule
    whose only pattern is invalid (``[unclosed``) never loads; it is counted lazy.
    """
    try:
        text = tree.read_text(path)
    except (OSError, ValueError, RuntimeError) as err:  # UnicodeDecodeError is a ValueError; RuntimeError a loop
        return 0, False, f"{path}: not readable ({_unreadable_reason(err)}), counted as 0"
    text = text.removeprefix("\ufeff")  # a UTF-8 BOM from an editor is not part of the frontmatter (as in skills)
    m = _FRONTMATTER.match(text)
    if not m:
        return len(text), False, None
    return len(text) - m.end(), _has_paths(m.group(1)), None


def _without_frontmatter(text: str) -> str:
    text = text.removeprefix("\ufeff")
    m = _FRONTMATTER.match(text)
    return text[m.end():] if m else text


def _has_paths(frontmatter: str) -> bool:
    """True if ``paths`` names at least one pattern: a string, or a list item that is a string, not blank.

    A value that is not a string (a number, a boolean, a mapping) names no pattern, and a container is never
    converted to text: a YAML alias chain expands on ``str()``. When the frontmatter does not parse, the
    ``paths`` key is read on its own (``_paths_block``), as Claude Code reads it.
    """
    try:
        meta = yaml.safe_load(frontmatter)
    except (yaml.YAMLError, RecursionError, ValueError):  # RecursionError: nesting too deep; ValueError: an integer over the digit limit
        meta = None  # Claude Code still reads a `paths:` line of YAML that does not parse
    if isinstance(meta, dict):
        return _value_names(meta.get("paths"))
    block = _paths_block(frontmatter)
    if block is None:
        return False
    try:
        own = yaml.safe_load("\n".join(block))
    except (yaml.YAMLError, RecursionError, ValueError):
        own = None
    if isinstance(own, dict):  # the key parses on its own, whatever the rest of the frontmatter does
        return _value_names(own.get("paths"))
    # the key itself does not parse (`[unclosed`): its text names a pattern unless every part is empty
    values = [_without_comment(block[0].split(":", 1)[1])]
    for line in block[1:]:
        s = line.strip()
        if s and not s.startswith("#"):
            values.append(_without_comment(s[1:] if s.startswith("-") else s))
    return any(_names_pattern(v) for v in values)


def _value_names(value: object) -> bool:
    """True if a parsed ``paths`` value names a pattern: a string (split on commas) or a list with a non-blank string."""
    items = value if isinstance(value, list) else value.split(",") if isinstance(value, str) else []
    return any(isinstance(v, str) and v.strip() for v in items)


def _names_pattern(value: str) -> bool:
    """True if one unparsed ``paths`` value (the inline value or a list item) names a pattern.

    Brackets and commas are split as YAML would; an empty item, ``null`` and ``~`` name nothing, and quotes
    around an item are not part of it (``"null"`` is the pattern ``null``).
    """
    for part in value.strip().strip("[]").split(","):
        part = part.strip()
        if part in ("", "null", "~"):
            continue
        if part.strip("\"'").strip():
            return True
    return False


def _paths_block(frontmatter: str) -> list[str] | None:
    """The lines of the ``paths`` key: its line, then the lines that continue it (indented, ``- item``, blank or comment).

    None when there is no ``paths`` key. A flow list that runs over several lines (``paths: [`` then ``"src/**"]``)
    is one block, so its continuation lines are read with the key.
    """
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        if _PATHS_LINE.match(line):
            block = [line]
            depth = _flow_depth(line)  # a flow list open on the key runs on, unindented or not
            for item in lines[i + 1:]:
                if depth == 0 and _INDENTED_KEY.match(item):  # an indented field of another key ends the block
                    break
                if depth > 0 or not item.strip() or item[:1] in (" ", "\t", "-", "#"):
                    block.append(item)
                    depth += _flow_depth(item)
                else:
                    break
            return block
    return None


def _flow_depth(line: str) -> int:
    """Open minus closed brackets on a line, a YAML comment left out (``paths: [] # [x`` is closed)."""
    text = _without_comment(line)
    return text.count("[") - text.count("]")


def _without_comment(text: str) -> str:
    """``text`` up to a YAML comment: a `#` that starts a word, outside a quoted scalar (``"src/#x"`` keeps it)."""
    quote = None
    prev = " "
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and prev in " \t[,":
            quote = ch
        elif ch == "#" and prev in " \t":
            return text[:i]
        prev = ch
    return text


def _imports(tree: RepoTree, roots: list[str], max_depth: int | None, rules: bool) -> tuple[set[str], list[tuple[str, str]]]:
    """Files the CLAUDE.md ``roots`` ``@import``, transitively (relative to the importing file).

    Breadth first, so a file is placed at its shortest hop count. A rule an import reaches is followed like any
    other file (its @ paths are imports only because a CLAUDE.md imports the rule: a rule that no CLAUDE.md imports
    is never read here). With ``max_depth``, imports of files already ``max_depth`` hops from a root are not
    followed; those left unreached come back as ``(importing file, target)`` pairs.
    """
    seen: set[str] = set()
    # Also by the file a path resolves to, so an alias (CLAUDE.md -> .) is not walked again under a longer path.
    # Expanded once per route kind (rule path or not): a rule link reached first must not hide a later import.
    reached = {_target(tree, r) for r in roots}
    reached_by_rule: set[str] = set()
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
            if rules and f not in roots and (RULE_GLOB.match(f) or RULE_GLOB.match(_target(tree, f))):
                text = _without_frontmatter(text)  # a rule's frontmatter is removed before it loads (as _rule counts)
            here = f.rsplit("/", 1)[0] + "/" if "/" in f else ""
            for target in IMPORT.findall(_strip_code_spans(_CODE_BLOCK.sub("", text))):
                if target.startswith("/"):
                    continue  # an absolute path names the host filesystem, not a file of the tree
                path = _normalize(here + target)
                if not path or path in seen:
                    continue
                t = _target(tree, path)
                done = reached_by_rule if RULE_GLOB.match(path) else reached
                if t in done:
                    continue
                if max_depth is not None and depth >= max_depth:
                    cut.append((f, path))
                    continue
                seen.add(path)
                done.add(t)
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

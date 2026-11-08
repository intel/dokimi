# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Review the skills of an external catalog: spec frontmatter, and identical copies.

Runs after the readers of :mod:`dokimi.external.catalog`. A skill that loaded
gets every check below. A skill that did not load gets one check only: broken
YAML in its frontmatter, reported as ``frontmatter-parse`` with the YAML error.
The load error also says "invalid YAML", with the line. Other load errors stay the scan's own. Checks
follow the Agent Skills spec:

- ``name`` is the directory name, kebab-case (lowercase letters, digits and
  single hyphens) and at most 64 characters. A mismatch or a bad format is an
  ``error``; a ``name`` missing from the frontmatter is a ``warn``, because the
  loader falls back to the directory name silently.
- ``description`` is at most 1024 characters (``error``). A missing or empty
  description is a load error, raised by the loader.
- The frontmatter is valid YAML (``frontmatter-parse``, ``error``).
- A frontmatter key that neither the spec nor Claude Code names is a ``warn``
  (``frontmatter-unknown-key``, see :data:`FRONTMATTER_KEYS`), and so is an ``allowed-tools``
  name that is no Claude Code tool (``allowed-tools-unknown``). A body over 500 lines or about
  5000 tokens (characters / 4) is a ``warn`` (``body-too-large``).
- A file with no frontmatter at all does not load, and its load error says "no frontmatter"
  rather than "missing a description" (a load error, so no finding; see the loader).

Identical copies: of the skills whose ``SKILL.md`` bytes are the same, the one
nearest the root (fewest path parts, ties to path order) is the original, the
copy :func:`dokimi.skills.discover_skills` keeps; each other copy is a ``warn``
naming it. The model sees only the name and
description, so two such skills compete for the same query even when their bundled
files differ (a plugin copy with an extra ``agents/`` folder is still a mirror).
Mirrors are common (one skill checked in under two plugin directories), and
counting them twice inflates routing and coverage numbers.

Same name, different content: a skill whose ``name`` another listed skill shares, with other bytes, is a ``warn`` (``name-conflict``) naming the other path. A listed skill is one a harness offers (no
``disable-model-invocation``, no spec error other than a description over 1024 characters; see ``dokimi.external.listing.in_field``).
Routing leaves that name out of the field rather than aborting.

``disable-model-invocation: true`` is not a finding: the model is never offered
that skill, so it is reported as a flag (the same reading as the skill arm's
check in :mod:`dokimi.harnesses.skill_treatment`).

Bundle links: a relative Markdown link in ``SKILL.md`` whose file does not exist is a
``warn`` (``link-missing``), inline or reference-style (``[label]: target``), with an
optional ``<...>`` destination, and URI destinations (``tel:``, ``urn:``, ``mailto:``) are
not files. A note is added when the file exists from the catalog root
instead (a wrong base). Example links such as ``[Title](URL)`` are common, so this is not
an error and does not fail ``--strict``. A link to a file outside the skill directory is a ``warn``
(``link-escapes-skill``): it breaks when the skill is installed alone. A ``../`` code span
naming such a file is ``path-escapes-skill``. Code spans that name no file are not checked,
and neither are code fences: those are usually examples or paths in the user's own project.

Descriptions: one with no "when" clause is a ``warn`` (``description-no-when``),
and so is one whose tokens overlap another skill's at a Jaccard similarity of at least
:data:`NEAR_DUPLICATE_JACCARD` (``description-near-duplicate``). Skills with
``disable-model-invocation: true`` are exempt from both, as the model never chooses them.

Mirror drift: two skills in different paths with the same directory name but different
``SKILL.md`` text are a ``warn`` on both (``mirror-drift``), naming the other copies: the
same skill has probably been edited in one place and not the other.

Content (:mod:`dokimi.external.content_checks`): a model ID its vendor has retired or
deprecated is a ``warn`` (``legacy-model-id``), and so is a hidden character (bidi control,
Unicode tag, stray zero-width character) in ``SKILL.md`` or a bundled text file
(``hidden-unicode``), reported with its line and codepoint.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Sequence
from urllib.parse import unquote

import yaml

from dokimi.skills import Skill
from dokimi.utils import describe_yaml_error, parse_frontmatter_strict

from .catalog import SkillCatalogEntry, _within
from .content_checks import content_findings
from .skill_markdown import link_pieces, outside_fences
from .listing import in_field

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
NEAR_DUPLICATE_JACCARD = 0.5
NEAR_DUPLICATE_MIN_SHARED = 3  # shared words; a one-word overlap in two short descriptions is not a near-duplicate
# The frontmatter keys the Agent Skills spec defines, then the ones Claude Code reads on top of it.
# Any other key is a typo or a field no harness reads (``frontmatter-unknown-key``).
FRONTMATTER_KEYS = frozenset(
    {
        "name", "description", "license", "allowed-tools", "metadata", "compatibility",
        "argument-hint", "disable-model-invocation", "user-invocable", "model", "context",
        "agent", "hooks", "version", "when_to_use", "paths", "shell", "effort",
    }
)
# The tool names Claude Code has. An ``allowed-tools`` entry is checked by its name only
# (``allowed-tools-unknown``): ``mcp__*`` tools are not checked, and ``Tool(pattern)`` is read as ``Tool``.
CLAUDE_TOOLS = frozenset(
    {
        "Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS", "WebFetch",
        "WebSearch", "Task", "Agent", "TodoWrite", "NotebookEdit", "NotebookRead",
        "Skill", "AskUserQuestion", "ExitPlanMode", "BashOutput", "KillShell", "KillBash",
        "SlashCommand", "ToolSearch", "Monitor", "TaskStop", "SendMessage", "EnterPlanMode",
        "EnterWorktree", "ExitWorktree", "ListMcpResourcesTool", "ReadMcpResourceTool",
        "CronCreate", "CronDelete", "CronList", "Workflow", "LSP", "Computer", "PowerShell",
    }
)
# A body over either limit is ``body-too-large``. Tokens are estimated as characters / 4, so no tokenizer is needed.
BODY_MAX_LINES = 500
BODY_MAX_TOKENS = 5000

# A "when" clause of any subject and verb ("run migrations when deploying"), or a use phrase: the model
# chooses a skill from its description alone.
WHEN_RE = re.compile(
    r"\b(?:when\s+\w|use for\b|use this skill\b|trigger(?:s|ed)?\s*(?:on\b|:)|invoke only\b|activate\b|"
    r"invoke this\b|use (?:it |this |these )?(?:when|if|before|after|whenever|proactively|anytime)\b|"
    r"whenever\b)",
    re.I,
)
# Words that say nothing about what a skill is for; left out of the description overlap.
STOP_WORDS = frozenset(
    """a an the and or of to in on for with by from as at is are be been being this that
    these those it its it's you your we our use used using when with without into than then
    also any all each skill skills claude user users make makes do does not no yes can will
    should must may might if else only other such via per about over under more most very
    just""".split()
)
# A backslash before ASCII punctuation is an escape in a link destination (``guide\_notes.md``).
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
# RFC 3986 scheme, dots included; a dotted one with a line or symbol after it is a file (_file_ref).
URI_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+\-.]*:")
# After a file, a line or a symbol (``run.py:42``, ``run.py:10-20``, ``run.py:main``): read as the file's name.
FILE_SUFFIX_RE = re.compile(r"\d+(?:[-:]\d+)?|[A-Za-z_]\w*")
LOCATION_SUFFIX_RE = re.compile(r":(?:\d+(?:[-:]\d+)?|[A-Za-z_]\w*)$")
SKILL_DIR_VAR = "${CLAUDE_SKILL_DIR}/"

# Every code a review can report: ``catalog check --ignore`` accepts these and no others.
FINDING_CODES = (
    "description-near-duplicate",
    "description-no-when",
    "description-too-long",
    "duplicate",
    "frontmatter-parse",
    "link-escapes-skill",
    "link-missing",
    "mirror-drift",
    "name-conflict",
    "name-format",
    "name-missing",
    "name-mismatch",
    "path-escapes-skill",
)


@dataclass
class Finding:
    """One check that did not pass: ``level`` is ``warn`` or ``error``."""

    level: str
    code: str
    message: str


def state_of(findings: Iterable[Finding]) -> str:
    """``error`` if any finding is an error, else ``warn`` if any is a warning, else ``ok``."""
    levels = {f.level for f in findings}
    return "error" if "error" in levels else "warn" if "warn" in levels else "ok"


@dataclass
class SkillReview:
    """What :func:`review_skills` found for one loaded skill."""

    findings: list[Finding] = field(default_factory=list)
    disable_model_invocation: bool = False
    content_sha256: Optional[str] = None
    duplicate_of: Optional[str] = (
        None  # the skill directory of the earlier copy, relative to the catalog root
    )

    @property
    def state(self) -> str:
        """``error`` if any finding is an error, else ``warn`` if any is a warning, else ``ok``."""
        return state_of(self.findings)


def _strict_meta(text: str) -> tuple[dict, Optional[Finding]]:
    """The frontmatter of ``text``, or a ``frontmatter-parse`` error carrying the YAML message."""
    try:
        meta, _ = parse_frontmatter_strict(text)
    except yaml.YAMLError as exc:
        # The same line and hint as the loader's load error, so the row and this finding agree.
        return {}, Finding(
            "error", "frontmatter-parse", f"frontmatter is not valid YAML: {describe_yaml_error(exc)}"
        )
    except RecursionError as exc:  # nesting too deep for the YAML parser: no line to name
        message = " ".join(str(exc).split())  # the YAML message is several lines; keep one
        return {}, Finding(
            "error", "frontmatter-parse", f"frontmatter is not valid YAML: {message}"
        )
    return meta, None


def _group_end(value: str, start: int, quotes: bool = True) -> Optional[int]:
    """The index just past the ``)`` that closes the ``(`` at ``start``, or None when the group never closes.

    Nested parentheses count. With ``quotes``, quoted text inside the group is opaque, so a ``)`` in quotes does
    not close it; a quote that never closes makes the group fail, and the caller reads that quote as a plain
    character (``Bash(echo don't)``) with ``quotes=False``.
    """
    depth, quote = 0, ""
    for i in range(start, len(value)):
        c = value[i]
        if quote:
            if c == quote:
                quote = ""
        elif quotes and c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _group_end_checked(value: str, start: int, quotes: bool) -> tuple[Optional[int], bool]:
    """``(_group_end(...), quotes still on)``. A quote that never closes switches quotes off for the rest of
    the value: that is one failed quoted scan at most, so the whole value is read in linear time."""
    if quotes:
        end = _group_end(value, start)
        if end is not None:
            return end, True
    return _group_end(value, start, quotes=False), False


def _tool_names(value: str) -> list[str]:
    """The tool name of each entry of one ``allowed-tools`` string, in one pass over it.

    Entries are split on whitespace and commas. A ``(pattern)`` after a name is consumed whole (see
    :func:`_group_end`). A group that never closes runs to the end of the string: its tool name is kept,
    its pattern is not read, and the scan stops. Quoting is a heuristic, and a stray quote can hide the entries between two quotes or
    leave a junk name such as ``")"`` (warn-only, and rare).
    """
    names: list[str] = []
    i, n = 0, len(value)
    quotes = True
    while i < n:
        c = value[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if c == "(":  # a pattern with no tool name: skipped
            end, quotes = _group_end_checked(value, i, quotes)
            if end is None:
                break
            i = end
            continue
        j = i
        while j < n and not (value[j].isspace() or value[j] in ",("):
            j += 1
        names.append(value[i:j])
        i = j
        if i < n and value[i] == "(":
            end, quotes = _group_end_checked(value, i, quotes)
            if end is None:
                break
            i = end
    return names


def _unknown_tools(allowed: object) -> list[str]:
    """The names in an ``allowed-tools`` value (a comma or space list, or a YAML list) that Claude Code has no tool of.

    ``mcp__*`` tools are not checked. A ``Tool(pattern)`` entry is checked by ``Tool``.
    """
    if allowed is None:
        return []
    items = allowed if isinstance(allowed, list) else [allowed]
    names = [name for item in items if isinstance(item, str) for name in _tool_names(item)]
    return [name for name in names if not name.startswith("mcp__") and name not in CLAUDE_TOOLS]


def _body_findings(skill: Skill) -> list[Finding]:
    """``body-too-large`` when the instruction body is over :data:`BODY_MAX_LINES` lines or :data:`BODY_MAX_TOKENS` tokens."""
    lines = len(skill.body.splitlines())
    tokens = len(skill.body) // 4  # chars / 4: an estimate, not a tokenizer count
    if lines <= BODY_MAX_LINES and tokens <= BODY_MAX_TOKENS:
        return []
    return [
        Finding(
            "warn",
            "body-too-large",
            f"SKILL.md body is {lines} lines and about {tokens} tokens (characters / 4); the limit is "
            f"{BODY_MAX_LINES} lines or {BODY_MAX_TOKENS} tokens. Move the detail into bundled files.",
        )
    ]


def _frontmatter_findings(skill: Skill, *, at_root: bool = False) -> tuple[list[Finding], bool]:
    """The spec checks on the frontmatter of ``skill``, and its ``disable-model-invocation`` flag.

    ``at_root``: the ``SKILL.md`` sits directly in the scanned root, so its directory name is the
    name of a checkout, which says nothing about the skill; the name/directory comparison is skipped.
    """
    meta, parse_error = _strict_meta(skill.path.read_text(encoding="utf-8"))
    if parse_error is not None:
        return [parse_error], False
    dir_name = _dir_name_of(skill.path.parent)
    findings: list[Finding] = []
    declared = str(meta.get("name") or "").strip()
    if not declared:
        findings.append(
            Finding(
                "warn",
                "name-missing",
                "no name in the frontmatter"
                + ("" if at_root else f"; the directory name {dir_name!r} is used"),
            )
        )
    else:
        if declared != dir_name and not at_root:
            findings.append(
                Finding(
                    "error",
                    "name-mismatch",
                    f"name {declared!r} is not the directory name {dir_name!r}",
                )
            )
        # ``name-format`` also covers the length: a name over 64 characters is not a separate code.
        problems = []
        if not NAME_PATTERN.fullmatch(declared):
            problems.append("is not kebab-case (lowercase letters, digits, single hyphens)")
        if len(declared) > NAME_MAX:
            problems.append(f"is {len(declared)} characters; the limit is {NAME_MAX}")
        if problems:
            findings.append(
                Finding("error", "name-format", f"name {declared!r} {' and '.join(problems)}")
            )
    if len(skill.description) > DESCRIPTION_MAX:
        findings.append(
            Finding(
                "error",
                "description-too-long",
                f"description is {len(skill.description)} characters; the limit is {DESCRIPTION_MAX}",
            )
        )
    unknown_keys = sorted({str(key) for key in meta} - FRONTMATTER_KEYS)
    if unknown_keys:
        findings.append(
            Finding(
                "warn",
                "frontmatter-unknown-key",
                f"frontmatter has {', '.join(map(repr, unknown_keys))}, which no spec or Claude Code "
                "field is named; a typo, or a field no harness reads",
            )
        )
    unknown_tools = sorted(set(_unknown_tools(meta.get("allowed-tools"))))
    if unknown_tools:
        findings.append(
            Finding(
                "warn",
                "allowed-tools-unknown",
                f"allowed-tools names {', '.join(map(repr, unknown_tools))}, which are not Claude Code "
                "tools (mcp__ tools are not checked; in Tool(pattern) only the tool name is checked)",
            )
        )
    # Read as the skill arm reads it (dokimi.harnesses.skill_treatment.check_skill).
    hidden = str(meta.get("disable-model-invocation", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return findings, hidden


def _skill_digest(skill: Skill) -> str:
    """SHA-256 of the ``SKILL.md`` bytes: the text the model is shown when it chooses a skill."""
    return hashlib.sha256(skill.path.read_bytes()).hexdigest()


def _unloaded_review(entry: SkillCatalogEntry) -> Optional[SkillReview]:
    """A review for an entry that did not load, only when its ``SKILL.md`` has broken YAML; else None.

    A ``SKILL.md`` that resolves outside the scanned root is not read: the YAML message quotes
    the offending line, and the catalog must not get a file outside it into the report.
    """
    if entry.path is None:
        return None
    candidate = entry.path / "SKILL.md" if entry.path.is_dir() else entry.path
    if candidate.name != "SKILL.md" or not candidate.is_file():
        return None
    if not _within(candidate, entry.source_root):
        return None
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None  # the loader already reported why the skill did not load
    _, parse_error = _strict_meta(text)
    return None if parse_error is None else SkillReview(findings=[parse_error])


def _relative_to_root(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` (compared as written, then resolved), else as written."""
    for a, b in ((path, root), (path.resolve(), root.resolve())):
        try:
            return a.relative_to(b).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _counterpart(other: tuple[SkillCatalogEntry, Skill, SkillReview], entry: SkillCatalogEntry) -> str:
    """The other skill's directory relative to its root, qualified by that root when it is another checkout."""
    o_entry, o_skill, _ = other
    where = _relative_to_root(o_skill.path.parent, o_entry.source_root)
    if o_entry.source_root.resolve() == entry.source_root.resolve():
        return where
    return f"{where} in {o_entry.source_root.as_posix()}"


def _file_ref(raw: str, link: bool = False) -> Optional[str]:
    """The skill-relative file a reference names, or None if it names no file to check.

    None for a URL or any other URI (``tel:``, ``urn:``, ``MAILTO:``), a bare anchor, an
    absolute path, a placeholder (``URL``, ``TBD``, ``...``), or a template variable (not ``${CLAUDE_SKILL_DIR}``). A
    ``<...>`` destination (CommonMark) is read as the path inside the brackets. A link
    destination (``link``) is a URL: its ``#fragment`` and ``?query`` are not part of the path,
    its backslash escapes of ASCII punctuation (``guide\\_notes.md``) are decoded, and its
    percent-encoded bytes name the file (``guide%20notes.md``). A code span is literal: a ``#``
    or a ``%`` in it is in the file's name.
    """
    text = raw.strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    if link:
        text = _ESCAPE_RE.sub(r"\1", text)
        text = text.split("#", 1)[0].split("?", 1)[0].strip()
        text = unquote(text)
    if not text or "://" in text or text.startswith("/"):
        return None
    scheme = URI_SCHEME_RE.match(text)
    if scheme and not ("." in scheme.group() and FILE_SUFFIX_RE.fullmatch(text[scheme.end() :])):
        return None
    text = LOCATION_SUFFIX_RE.sub("", text)  # the file is named before its line or symbol suffix
    if not text or text.lower() in {"url", "link", "path", "uri", "href", "todo", "tbd", "..."}:  # a stand-in
        return None
    if text.startswith(SKILL_DIR_VAR):
        text = text[len(SKILL_DIR_VAR) :]
        if not text:
            return None
    if any(c in text for c in "${}<>*"):
        return None
    return text


def _resolved(path: Path) -> Optional[Path]:
    """``path`` resolved, or None when the OS refuses the name (a NUL byte, a symlink loop)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _exists(path: Path) -> bool:
    """``path.exists()``, where a name the OS refuses (over 255 bytes in one part, say) is absent."""
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _inside(path: Path, base: Path) -> bool:
    """Whether ``path`` lies under ``base`` by its written name, with ``..`` taken away. Reads no file."""
    return Path(os.path.abspath(path)).is_relative_to(os.path.abspath(base))


def _in_checkout(path: Path, source_root: Path) -> Optional[Path]:
    """``path`` resolved, or None unless it lies in the checkout both as written and where it resolves."""
    target = _resolved(path) if _inside(path, source_root) else None
    return target if target is not None and target.is_relative_to(source_root.resolve()) else None


def _link_finding(raw: str, skill_dir: Path, source_root: Path, real_dir: Path) -> Optional[Finding]:
    """The finding for one link destination (inline or reference), or None when it is fine."""
    text = _file_ref(raw, link=True)
    if text is None:
        return None
    named = skill_dir / text
    if not _inside(named, skill_dir):
        return _escapes_link(raw)
    target = _resolved(named)
    if target is not None and not target.is_relative_to(real_dir):
        return _escapes_link(raw)
    if target is not None and _exists(target):
        return None
    from_root = ""
    rooted = _in_checkout(source_root / text, source_root)
    if rooted is not None and _exists(rooted):
        from_root = " (it exists from the catalog root: the base is wrong)"
    return Finding("warn", "link-missing", f"link {raw!r} names no file relative to the skill directory{from_root}")


def _span_finding(raw: str, skill_dir: Path, source_root: Path, real_dir: Path) -> Optional[Finding]:
    """The finding for a code span that names an existing file outside the skill (``../``), or None."""
    text = _file_ref(raw)
    if text is None or not raw.startswith("../"):
        return None
    target = _in_checkout(skill_dir / text, source_root)
    if target is None or not _exists(target) or target.is_relative_to(real_dir):
        return None  # outside the checkout, as written or resolved: not read
    return Finding(
        "warn",
        "path-escapes-skill",
        f"{raw!r} points outside the skill directory; it breaks when the skill "
        "is installed alone",
    )


def _bundle_findings(skill: Skill, source_root: Path) -> list[Finding]:
    """Link and path checks on the body of ``skill``: missing targets, or targets outside it.

    A target is classed by its written name, then by where it resolves, before any file is read:
    one outside the skill directory is ``link-escapes-skill`` whether or not it exists, and one
    outside the catalog checkout (through a symlink too) is never stat-ed.
    """
    skill_dir = skill.path.parent
    real_dir = skill_dir.resolve()
    targets, spans = link_pieces(outside_fences(skill.body))
    found = [_link_finding(raw, skill_dir, source_root, real_dir) for raw in targets]
    found += [_span_finding(raw, skill_dir, source_root, real_dir) for raw in spans]
    return [finding for finding in found if finding is not None]


def _escapes_link(raw: str) -> Finding:
    return Finding(
        "warn",
        "link-escapes-skill",
        f"link {raw!r} points outside the skill directory; it breaks when the skill is installed alone",
    )


def _description_findings(skill: Skill) -> list[Finding]:
    """A ``warn`` when the description has no "when" clause."""
    if not WHEN_RE.search(" ".join(skill.description.split())):
        return [
            Finding(
                "warn",
                "description-no-when",
                "description has no 'when' clause (e.g. 'use when ...'); the model chooses "
                "the skill from the description alone",
            )
        ]
    return []


def _tokens(description: str) -> frozenset[str]:
    """The lowercase words of three or more letters, in any script, less the stop words."""
    words = re.findall(r"[^\W\d_]{3,}", description.lower())
    return frozenset(w for w in words if w not in STOP_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _dir_name_of(directory: Path) -> str:
    """The name of ``directory`` with its ``..`` parts taken away lexically: ``alpha/sub/..`` is ``alpha``.

    Lexical, not resolved, so a symlinked skill directory keeps the name it is listed under.
    """
    return Path(os.path.normpath(directory)).name


def _dir_name(item: tuple[SkillCatalogEntry, Skill, SkillReview]) -> Optional[str]:
    """The directory name that can make a skill a mirror of another, None for the root skill.

    The root ``SKILL.md`` is named by the checkout's directory, not by the skill, so it has no
    name to match (as ``_frontmatter_findings`` treats it with ``at_root``).
    """
    entry, skill, _ = item
    if skill.path.parent.resolve() == entry.source_root.resolve():
        return None
    return _dir_name_of(skill.path.parent)


def _same_description(a: Skill, b: Skill) -> bool:
    return " ".join(a.description.split()) == " ".join(b.description.split())


def _is_drift(a: tuple[SkillCatalogEntry, Skill, SkillReview], b: tuple[SkillCatalogEntry, Skill, SkillReview]) -> bool:
    """Two same-named skills whose ``SKILL.md`` differ though their descriptions agree: copies that drifted."""
    return a[2].content_sha256 != b[2].content_sha256 and _same_description(a[1], b[1])


def _mirror_drift(loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]]) -> None:
    """``mirror-drift`` on each skill that shares its directory name with a copy that has drifted.

    A same-named skill with another description is a different skill (plugins repeat ``review``),
    and the description checks cover it.
    """
    by_dir_name: dict[str, list[tuple[SkillCatalogEntry, Skill, SkillReview]]] = {}
    for item in loaded:
        name = _dir_name(item)
        if name is not None:
            by_dir_name.setdefault(name, []).append(item)
    for group in by_dir_name.values():
        for item in group:
            entry, _, review = item
            others = [_counterpart(other, entry) for other in group if _is_drift(item, other)]
            if others:
                review.findings.append(
                    Finding(
                        "warn",
                        "mirror-drift",
                        f"SKILL.md differs from {', '.join(others)} (the same skill name in "
                        "another path); the copies may have drifted apart",
                    )
                )


def _near_duplicates(loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]]) -> None:
    """``description-near-duplicate`` on both skills of each pair whose descriptions overlap."""
    shown = [item for item in loaded if not item[2].disable_model_invocation]
    tokens = [_tokens(" ".join(item[1].description.split())) for item in shown]
    names = [_dir_name(item) for item in shown]
    by_token: dict[str, list[int]] = {}
    for index, words in enumerate(tokens):
        for word in words:
            by_token.setdefault(word, []).append(index)
    for i, (entry, _, review) in enumerate(shown):
        # only a pair that shares a word can overlap
        for j in sorted({k for word in tokens[i] for k in by_token[word] if k > i}):
            o_entry, _, o_review = shown[j]
            if o_review.content_sha256 == review.content_sha256:
                continue  # an identical SKILL.md: already a duplicate
            if names[i] is not None and names[i] == names[j] and _is_drift(shown[i], shown[j]):
                continue  # the same skill in another path, drifted: already a mirror-drift
            shared = len(tokens[i] & tokens[j])
            similarity = _jaccard(tokens[i], tokens[j])
            if shared >= NEAR_DUPLICATE_MIN_SHARED and similarity >= NEAR_DUPLICATE_JACCARD:
                pairs = (
                    (review, _counterpart(shown[j], entry)),
                    (o_review, _counterpart(shown[i], o_entry)),
                )
                for side, other in pairs:
                    side.findings.append(
                        Finding(
                            "warn",
                            "description-near-duplicate",
                            f"description overlaps {other} (word overlap {similarity:.0%}); "
                            "the model may pick either",
                        )
                    )


def review_skills(
    entries: Sequence[SkillCatalogEntry], *, standalone: bool = False
) -> list[Optional[SkillReview]]:
    """One :class:`SkillReview` per entry, in order.

    None for an entry that did not load, unless its frontmatter is broken YAML: then its
    review carries the ``frontmatter-parse`` error. Of each identical ``SKILL.md``, the
    copy nearest the root is the original; every other copy names it in ``duplicate_of``.
    Mirror drift and near-duplicate descriptions are checked across all the loaded skills,
    and a skill with ``disable-model-invocation`` set is left out of the description checks.

    ``standalone``: each entry is one skill directory reviewed alone (:func:`review_skill_dir`). Its
    ``SKILL.md`` is still the root for the frontmatter and link checks, but its bundled files are
    content-checked as in a catalog, since no checkout root stands above them.
    """
    reviews: list[Optional[SkillReview]] = []
    copies: list[tuple[SkillReview, str, str]] = []  # (review, skill directory, digest) per loaded skill
    loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]] = []
    by_name: dict[str, list[tuple[SkillReview, str, str]]] = {}
    for entry in entries:
        if not entry.loaded or entry.skill is None:
            reviews.append(_unloaded_review(entry))
            continue
        skill = entry.skill
        at_root = skill.path.parent.resolve() == entry.source_root.resolve()
        findings, hidden = _frontmatter_findings(skill, at_root=at_root)
        review = SkillReview(findings=findings, disable_model_invocation=hidden)
        review.findings += _bundle_findings(skill, entry.source_root)
        review.findings += _body_findings(skill)
        review.findings += [
            Finding("warn", code, message)
            for code, message in content_findings(skill, at_root=at_root and not standalone)
        ]
        if not hidden:
            review.findings += _description_findings(skill)
        review.content_sha256 = _skill_digest(skill)
        where = _relative_to_root(skill.path.parent, entry.source_root)
        copies.append((review, where, review.content_sha256))
        by_name.setdefault(skill.name, []).append((review, where, review.content_sha256))
        reviews.append(review)
        loaded.append((entry, skill, review))

    original_of: dict[str, tuple[SkillReview, str]] = {}
    for copy, where, digest in sorted(copies, key=lambda c: (len(PurePosixPath(c[1]).parts), c[1])):
        original_of.setdefault(digest, (copy, where))
    for copy, _, digest in copies:
        original, where = original_of[digest]
        if original is copy:
            continue
        copy.duplicate_of = where
        copy.findings.append(
            Finding(
                "warn",
                "duplicate",
                f"SKILL.md is identical to {where}; the same skill is counted twice",
            )
        )
    _mirror_drift(loaded)
    _near_duplicates(loaded)
    _name_conflicts(by_name)
    return reviews


def _name_conflicts(by_name: dict[str, list[tuple[SkillReview, str, str]]]) -> None:
    """A warning on each listed skill that shares its name with a listed skill of different content.

    Listed means a harness offers it: a skill with ``disable-model-invocation`` or a spec error is
    left out of the routing field by default (see ``in_field``), so its copy is no conflict. Identical copies are not
    a conflict (they are the duplicate above). The routing field skips such a name, so the scan
    names both paths to say which skills are affected.
    """
    for name, group in by_name.items():
        group = [entry for entry in group if in_field(entry[0])]
        for review, _, digest in group:
            others = sorted({other for _, other, other_digest in group if other_digest != digest})
            if not others:
                continue
            review.findings.append(
                Finding(
                    "warn",
                    "name-conflict",
                    f"name {name!r} is also used by {', '.join(others)}, with different content; "
                    "`novelty routing --catalog` leaves this name out of the field "
                    "until one is renamed or removed",
                )
            )

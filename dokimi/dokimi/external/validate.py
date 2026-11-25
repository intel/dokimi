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
  name that is no Claude Code tool (``allowed-tools-unknown``). An ``allowed-tools`` that grants
  ``Bash`` for every command (``Bash`` with no pattern, or ``Bash(*)``) is a ``warn``
  (``allowed-tools-unrestricted-bash``); its message names a ``pipe-to-shell`` or
  ``permission-bypass-flag`` hit in the same skill. A body over 500 lines or about
  5000 tokens (characters / 4) is a ``warn`` (``body-too-large``).
- A file with no frontmatter at all does not load, and its load error says "no frontmatter"
  rather than "missing a description" (see the loader). Its one finding is
  ``not-a-skill-no-frontmatter`` (``error``, see :mod:`dokimi.external.no_frontmatter`), except under
  OpenCode or a host with no known spec: ``opencode-no-description`` or ``unsupported-skill-format``
  (see :mod:`dokimi.external.skill_format`).

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
naming such a file is ``path-escapes-skill``. A code span naming a bundled file the skill does
not have is ``path-missing`` (see :mod:`dokimi.external.bundle_paths`). Other code spans, and code
fences, are not checked: those are usually examples or paths in the user's own project.

Bundled scripts and documents that ``SKILL.md`` never names are one ``warn`` per skill
(``bundle-file-unreferenced``, see :mod:`dokimi.external.bundle_refs`).

Descriptions: one with no "when" clause is a ``warn`` (``description-no-when``); the cues are
English, Spanish, French, German, Portuguese, Russian, Chinese, Japanese and Korean (``when_cues.WHEN_CUES``),
and a description in a script none covers is not checked: it gets a ``note`` (``description-when-not-checked``). One whose tokens overlap another skill's at a
Jaccard similarity of at least :data:`NEAR_DUPLICATE_JACCARD` is a ``warn`` too (``description-near-duplicate``). Under a different name, an overlap from :data:`SIMILAR_JACCARD` up to that line is a
``note`` (``similar-skills-exist``) that does not change the state. Skills with
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
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

import yaml

from dokimi.skills import Skill
from dokimi.utils import describe_yaml_error, parse_frontmatter_strict

from .description_overlap import (NEAR_DUPLICATE_JACCARD, NEAR_DUPLICATE_MIN_SHARED, SIMILAR_JACCARD,
                                  _candidate_pairs, _jaccard, _tokens)
from .bundle_paths import path_missing
from .link_files import _file_ref, _link_files
from .catalog import SkillCatalogEntry, _within
from .bundle_refs import unreferenced_check
from .command_checks import command_findings
from .allowed_tools import RISKY_COMMAND_CODES, tool_names, grants_unrestricted_bash, unrestricted_bash_message
from .content_checks import content_findings
from .frontmatter_shape import keeps_load_error, opens_frontmatter
from .findings import Finding, SkillReview, state_of  # noqa: F401 (state_of: re-exported, it lived here)
from .listing import entry_chars, in_field
from .no_frontmatter import NO_FRONTMATTER, no_frontmatter_message
from .skill_format import FORMAT_AGENT_SKILL, FORMAT_OPENCODE, FORMAT_UNSUPPORTED, format_findings, opencode_hidden, skill_format
from .script_checks import script_findings
from .paths import _exists, _in_checkout, _inside, _resolved
from .skill_markdown import QUOTE_MARK_RE, link_pieces, outside_fences
from .upstream_codes import UPSTREAM_FINDING_CODES
from .when_cues import when_clause_found

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
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

# Every code a review can report: ``catalog check --ignore`` accepts these and no others.
# tests/test_catalog_ignore.py reads the emitted codes from the source and fails on any missing here.
FINDING_CODES = (
    "allowed-tools-unknown",
    "allowed-tools-unrestricted-bash",
    "body-too-large",
    "bundle-file-unchecked",
    "bundle-file-unreferenced",
    "description-near-duplicate",
    "description-no-when",
    "description-too-long",
    "description-when-not-checked",
    "duplicate",
    "frontmatter-parse",
    "frontmatter-unknown-key",
    "hidden-unicode",
    "install-unpinned",
    "legacy-model-id",
    "link-dead",
    "link-escapes-skill",
    "link-missing",
    "listing-over-budget",
    "mirror-drift",
    "name-conflict",
    "name-format",
    "name-mismatch",
    "name-missing",
    "not-a-skill-no-frontmatter",
    "not-loaded",
    "opencode-no-description",
    "package-not-found",
    "path-escapes-skill",
    "path-missing",
    "unreadable",
    "unsupported-skill-format",
    "permission-bypass-flag",
    "pipe-to-shell",
    "script-crlf",
    "script-lint",
    "script-syntax",
    "similar-skills-exist",
) + UPSTREAM_FINDING_CODES  # the codes of `catalog check --upstream`, in upstream_codes.py



def name_risky_commands(findings: list[Finding]) -> list[Finding]:
    """The findings, with the unrestricted-Bash message naming the risky-command codes that are still in them.

    The grant's message says the skill's risky commands run with no prompt, so it follows the codes that remain
    after ``--ignore`` drops some.
    """
    risky = sorted({f.code for f in findings if f.code in RISKY_COMMAND_CODES})
    return [
        Finding(f.level, f.code, unrestricted_bash_message(risky)) if f.code == "allowed-tools-unrestricted-bash" else f
        for f in findings
    ]




def _strict_meta(text: str) -> tuple[dict, Optional[Finding]]:
    """The frontmatter of ``text``, or a ``frontmatter-parse`` error carrying the YAML message."""
    try:
        meta, _ = parse_frontmatter_strict(text)
    except yaml.YAMLError as exc:
        # The same line and hint as the loader's load error, so the row and this finding agree.
        return {}, Finding(
            "error", "frontmatter-parse", f"frontmatter is not valid YAML: {describe_yaml_error(exc)}"
        )
    return meta, None


def _unknown_tools(allowed: object) -> list[str]:
    """The names in an ``allowed-tools`` value (a comma or space list, or a YAML list) that Claude Code has no tool of.

    ``mcp__*`` tools are not checked. A ``Tool(pattern)`` entry is checked by ``Tool``.
    """
    if allowed is None:
        return []
    items = allowed if isinstance(allowed, list) else [allowed]
    names = [name for item in items if isinstance(item, str) for name in tool_names(item)]
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


def _tool_findings(allowed: object) -> list[Finding]:
    """``allowed-tools-unknown`` and ``allowed-tools-unrestricted-bash`` for an ``allowed-tools`` value."""
    findings: list[Finding] = []
    unknown_tools = sorted(set(_unknown_tools(allowed)))
    if unknown_tools:
        findings.append(
            Finding(
                "warn",
                "allowed-tools-unknown",
                f"allowed-tools names {', '.join(map(repr, unknown_tools))}, which are not Claude Code "
                "tools (mcp__ tools are not checked; in Tool(pattern) only the tool name is checked)",
            )
        )
    if grants_unrestricted_bash(allowed):
        findings.append(Finding("warn", "allowed-tools-unrestricted-bash", unrestricted_bash_message([])))
    return findings


def _frontmatter_findings(
    skill: Skill, *, at_root: bool = False, fmt: str = FORMAT_AGENT_SKILL
) -> tuple[list[Finding], bool]:
    """The spec checks on the frontmatter of ``skill``, and its ``disable-model-invocation`` flag.

    ``at_root``: the ``SKILL.md`` sits directly in the scanned root, so its directory name is the
    name of a checkout, which says nothing about the skill; the name/directory comparison is skipped.
    ``fmt`` ``opencode``: OpenCode's rules (``skill_format``): ``name`` is optional and a display
    label, so no name/directory or missing-name finding, no description cap, and a bad name is a warning.
    """
    meta, parse_error = _strict_meta(skill.path.read_text(encoding="utf-8"))
    if parse_error is not None:
        return [parse_error], False
    opencode = fmt == FORMAT_OPENCODE
    dir_name = _dir_name_of(skill.path.parent)
    findings: list[Finding] = []
    declared = str(meta.get("name") or "").strip()
    if not declared:
        if not opencode:  # OpenCode falls back to the path-derived ID without a warning
            findings.append(
                Finding(
                    "warn",
                    "name-missing",
                    "no name in the frontmatter"
                    + ("" if at_root else f"; the directory name {dir_name!r} is used"),
                )
            )
    else:
        if declared != dir_name and not at_root and not opencode:
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
            # OpenCode states the kebab-case rule only for the path-derived ID, and does not enforce it: a warning.
            findings.append(
                Finding("warn" if opencode else "error", "name-format",
                        f"name {declared!r} {' and '.join(problems)}")
            )
    if len(skill.description) > DESCRIPTION_MAX and not opencode:  # OpenCode documents no description cap
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
    findings += _tool_findings(meta.get("allowed-tools"))
    # Read as the skill arm reads it (dokimi.harnesses.skill_treatment.check_skill).
    hidden = str(meta.get("disable-model-invocation", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return findings, opencode_hidden(meta, hidden) if opencode else hidden


def _skill_digest(skill: Skill) -> str:
    """SHA-256 of the ``SKILL.md`` bytes: the text the model is shown when it chooses a skill."""
    return hashlib.sha256(skill.path.read_bytes()).hexdigest()


def _unloaded_safety(candidate: Path, meta: dict, root: Path, standalone: bool) -> list[Finding]:
    """The command checks and the unrestricted-Bash grant of an unloaded ``SKILL.md`` a format rule exempts,
    so ``--strict`` fails it as it fails a loaded skill."""
    at_root = candidate.parent.resolve() == root.resolve() and not standalone
    found = [Finding("warn", code, message) for code, message in command_findings(candidate, at_root=at_root)]
    found += [f for f in _tool_findings(meta.get("allowed-tools")) if f.code == "allowed-tools-unrestricted-bash"]
    return name_risky_commands(found)


def _unloaded_review(entry: SkillCatalogEntry, standalone: bool = False) -> Optional[SkillReview]:
    """A review for an entry that did not load, only when its ``SKILL.md`` is plain Markdown (no frontmatter) or
    has broken YAML; else None.

    A ``SKILL.md`` that resolves outside the scanned root is not read: the YAML message quotes
    the offending line, and the catalog must not get a file outside it into the report.
    """
    if entry.path is None:
        return None
    try:
        candidate = entry.path / "SKILL.md" if entry.path.is_dir() else entry.path
        if candidate.name != "SKILL.md" or not candidate.is_file():
            return None
        if not _within(candidate, entry.source_root):
            return None
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None  # the loader already reported why the skill did not load
    if not text.lstrip("\ufeff").strip():
        return None  # an empty file is not prose: the loader's error stands
    lines = text.lstrip("\ufeff").splitlines()
    first = next((line.rstrip() for line in lines if line.strip()), "")
    unquoted = ["> quoted" if QUOTE_MARK_RE.match(line) else line for line in lines]  # a quoted example is prose too: text, not a key
    fmt = skill_format(candidate, entry.source_root)
    if keeps_load_error(fmt, lines, first, unquoted):
        return None  # a broken skill, not prose or a missing description; the loader's error stands
    if first.strip() != "---" or not opens_frontmatter(unquoted):  # a rule or prose, not a broken block
        loaded = format_findings(fmt, loaded=False)  # OpenCode loads a file with no frontmatter; a host with no spec gets a note
        if loaded:
            return SkillReview(findings=loaded + _unloaded_safety(candidate, {}, entry.source_root, standalone))
        at_root = candidate.parent.resolve() == entry.source_root.resolve()  # as _frontmatter_findings reads it
        hint = no_frontmatter_message(lines, _dir_name_of(candidate.parent), at_root=at_root)
        return SkillReview(findings=[Finding("error", NO_FRONTMATTER, hint)])
    meta, parse_error = _strict_meta(text)
    if parse_error is not None:
        return SkillReview(findings=[parse_error])
    gap = format_findings(fmt, meta, loaded=False)
    return SkillReview(findings=gap + _unloaded_safety(candidate, meta, entry.source_root, standalone)) if gap else None


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
    linked = frozenset(text.removeprefix("./") for text in (_file_ref(raw, link=True) for raw in targets) if text)
    return [finding for finding in found if finding is not None] + [
        Finding("warn", "path-missing", message)
        for message in path_missing(spans, skill_dir, source_root, real_dir, linked)
    ]


def _escapes_link(raw: str) -> Finding:
    return Finding(
        "warn",
        "link-escapes-skill",
        f"link {raw!r} points outside the skill directory; it breaks when the skill is installed alone",
    )


def _description_findings(skill: Skill) -> list[Finding]:
    """A ``warn`` when the description has no "when" clause. A script with no cue list gets a ``note``
    instead: the clause is not checked, and that must not read as a pass."""
    found = when_clause_found(skill.description)
    if found is None:
        return [
            Finding(
                "note",
                "description-when-not-checked",
                "description is in a script with no 'when' cue list, so its 'when' clause is not checked",
            )
        ]
    if found is False:
        return [
            Finding(
                "warn",
                "description-no-when",
                "description has no 'when' clause (e.g. 'use when ...'); the model chooses "
                "the skill from the description alone",
            )
        ]
    return []


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


def _name_key(name: str) -> str:
    """The name as the corpus analyses compare names: lower case, every run of other characters one hyphen.

    A name with no letter, digit or combining mark at all (``★``) would key to ``""``
    and equal every other such name, so it keys to its case-folded self.
    """
    kept = "".join(c if c.isalnum() or unicodedata.category(c).startswith("M") else "-" for c in name.lower())
    return re.sub(r"-+", "-", kept).strip("-") or name.casefold().strip()


def _similar_skills(loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]]) -> None:
    """``similar-skills-exist`` (a note) on both skills of each pair under different names whose descriptions overlap.

    The overlap is at least SIMILAR_JACCARD and below NEAR_DUPLICATE_JACCARD: a pair at or above it is already a
    ``description-near-duplicate`` warning. Same-named skills are left to the name and copy checks.
    """
    shown = [item for item in loaded if not item[2].disable_model_invocation]
    tokens = [_tokens(" ".join(item[1].description.split())) for item in shown]
    keys = [_name_key(item[1].name) for item in shown]
    for i, j in _candidate_pairs(tokens, SIMILAR_JACCARD):
        if keys[i] == keys[j]:
            continue
        shared = len(tokens[i] & tokens[j])
        similarity = _jaccard(tokens[i], tokens[j])
        if shared < NEAR_DUPLICATE_MIN_SHARED or not SIMILAR_JACCARD <= similarity < NEAR_DUPLICATE_JACCARD:
            continue
        entry, _, review = shown[i]
        o_entry, _, o_review = shown[j]
        pairs = (
            (review, _counterpart(shown[j], entry)),
            (o_review, _counterpart(shown[i], o_entry)),
        )
        for side, other in pairs:
            side.findings.append(
                Finding(
                    "note",
                    "similar-skills-exist",
                    f"description overlaps {other} (word overlap {int(similarity * 100)}%) under another name; "
                    "the two may be one skill under two names",
                )
            )


def _near_duplicates(loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]]) -> None:
    """``description-near-duplicate`` on both skills of each pair whose descriptions overlap.

    Only the candidate pairs of :func:`_candidate_pairs` are compared.
    """
    shown = [item for item in loaded if not item[2].disable_model_invocation]
    tokens = [_tokens(" ".join(item[1].description.split())) for item in shown]
    names = [_dir_name(item) for item in shown]
    for i, j in _candidate_pairs(tokens, NEAR_DUPLICATE_JACCARD):
        entry, _, review = shown[i]
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


def _unreferenced_findings(skill: Skill, *, at_root: bool) -> list[Finding]:
    """The bundled files ``SKILL.md`` and the documents it names never mention, or a note that not all were seen."""
    unreferenced, unchecked = unreferenced_check(skill.path, _link_files(skill.body), at_root=at_root,
                                                 doc_links=_link_files)
    return ([Finding("warn", "bundle-file-unreferenced", unreferenced)] if unreferenced is not None else []) + (
        [Finding("note", "bundle-file-unchecked", unchecked)] if unchecked is not None else [])


def review_skills(
    entries: Sequence[SkillCatalogEntry], *, standalone: bool = False
) -> list[Optional[SkillReview]]:
    """One :class:`SkillReview` per entry, in order.

    None for an entry that did not load, unless its ``SKILL.md`` is plain Markdown (then its review
    carries the ``not-a-skill-no-frontmatter`` error) or its frontmatter is broken YAML (then the
    ``frontmatter-parse`` error). A loaded skill whose ``SKILL.md`` cannot
    be read during review gets a review with one ``unreadable`` error and no digest. Of each
    identical ``SKILL.md``, the copy nearest the root is the original; every other copy names
    it in ``duplicate_of``.
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
            reviews.append(_unloaded_review(entry, standalone))
            continue
        skill = entry.skill
        try:  # any read of SKILL.md after it loaded: an error row, not a crash
            at_root = skill.path.parent.resolve() == entry.source_root.resolve()
            fmt = skill_format(skill.path, entry.source_root)
            findings, hidden = _frontmatter_findings(skill, at_root=at_root, fmt=fmt)
            if fmt == FORMAT_UNSUPPORTED:  # no spec to hold the frontmatter and description to: one note, and the safety finding stays
                findings = format_findings(fmt) + [
                    f for f in findings if f.code == "allowed-tools-unrestricted-bash"
                ]
            review = SkillReview(findings=findings, disable_model_invocation=hidden)
            review.findings += _bundle_findings(skill, entry.source_root)
            review.findings += _body_findings(skill)
            review.findings += [
                Finding("warn", code, message)
                for code, message in command_findings(skill.path, at_root=at_root and not standalone)
            ]
            review.findings = name_risky_commands(review.findings)
            review.findings += [
                Finding("warn", code, message)
                for code, message in content_findings(skill, at_root=at_root and not standalone)
            ]
            review.findings += _unreferenced_findings(skill, at_root=at_root and not standalone)
            review.findings += [Finding(*item) for item in script_findings(
                skill.path.parent, checkout=None if standalone else entry.source_root)]
            if not hidden and fmt != FORMAT_UNSUPPORTED:
                review.findings += _description_findings(skill)
            review.content_sha256 = _skill_digest(skill)
            review.listing_chars = entry_chars(skill)
        except (OSError, UnicodeDecodeError, RuntimeError, ValueError) as exc:  # the unreadable row
            unreadable = Finding("error", "unreadable", f"SKILL.md cannot be read: {exc}")
            reviews.append(SkillReview(findings=[unreadable]))
            continue
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
    supported = [x for x in loaded if skill_format(x[1].path, x[0].source_root) != FORMAT_UNSUPPORTED]
    _near_duplicates(supported)
    _similar_skills(supported)
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
                    "`skill routing --against` (or `catalog routing ROOT`) leaves this name out of the field "
                    "until one is renamed or removed",
                )
            )

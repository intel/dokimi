# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Review the skills of an external catalog: spec frontmatter, and identical copies.

Runs after the readers of :mod:`dokimi.external.catalog`. A skill that loaded
gets every check below. A skill that did not load gets one check only: broken
YAML in its frontmatter, reported as ``frontmatter-parse`` with the YAML error,
because the loader reads broken YAML as "no description" and its load error
would otherwise hide the cause. Other load errors stay the scan's own. Checks
follow the Agent Skills spec:

- ``name`` is the directory name, kebab-case (lowercase letters, digits and
  single hyphens) and at most 64 characters. A mismatch or a bad format is an
  ``error``; a ``name`` missing from the frontmatter is a ``warn``, because the
  loader falls back to the directory name silently.
- ``description`` is at most 1024 characters (``error``). A missing or empty
  description is a load error, raised by the loader.
- The frontmatter is valid YAML (``frontmatter-parse``, ``error``).

Identical copies: of the skills whose ``SKILL.md`` bytes are the same, the one
nearest the root (fewest path parts, ties to path order) is the original, the
copy :func:`dokimi.skills.discover_skills` keeps; each other copy is a ``warn``
naming it. The model sees only the name and
description, so two such skills compete for the same query even when their bundled
files differ (a plugin copy with an extra ``agents/`` folder is still a mirror).
Mirrors are common (one skill checked in under two plugin directories), and
counting them twice inflates routing and coverage numbers.

``disable-model-invocation: true`` is not a finding: the model is never offered
that skill, so it is reported as a flag (the same reading as the skill arm's
check in :mod:`dokimi.harnesses.skill_treatment`).

Bundle links: a relative Markdown link in ``SKILL.md`` whose file does not exist is a
``warn`` (``link-missing``), with a note when the file exists from the catalog root
instead (a wrong base). Example links such as ``[Title](URL)`` are common, so this is not
an error and does not fail ``--strict``. A link to a file outside the skill directory is a ``warn``
(``link-escapes-skill``): it breaks when the skill is installed alone. A ``../`` code span
naming such a file is ``path-escapes-skill``. Code spans that name no file are not checked,
and neither are code fences: those are usually examples or paths in the user's own project.

Descriptions: one with no "when to use" clause is a ``warn`` (``description-no-when``),
and so is one whose tokens overlap another skill's at a Jaccard similarity of at least
:data:`NEAR_DUPLICATE_JACCARD` (``description-near-duplicate``). Skills with
``disable-model-invocation: true`` are exempt from both, as the model never chooses them.

Mirror drift: two skills in different paths with the same directory name but different
``SKILL.md`` text are a ``warn`` on both (``mirror-drift``), naming the other copies: the
same skill has probably been edited in one place and not the other.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

import yaml

from dokimi.skills import Skill
from dokimi.utils import parse_frontmatter_strict

from .catalog import SkillCatalogEntry, _within

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
NEAR_DUPLICATE_JACCARD = 0.5

# A "when to use" clause: the model chooses a skill from its description alone.
WHEN_RE = re.compile(
    r"\b(?:when to use|when to delegate|use when|use this when|use for|use this skill|"
    r"trigger(?:s|ed)?\s*(?:on|when|:)|invoke (?:when|this)|invoke only|"
    r"use (?:it |this |these )?(?:when|if|before|after|whenever|proactively|anytime)|"
    r"whenever|when the user|activate\b|should be used when|when building|"
    r"when (?:you|the user) )",
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
LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
SKILL_DIR_VAR = "${CLAUDE_SKILL_DIR}/"


@dataclass
class Finding:
    """One check that did not pass: ``level`` is ``warn`` or ``error``."""

    level: str
    code: str
    message: str


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
        levels = {f.level for f in self.findings}
        return "error" if "error" in levels else "warn" if "warn" in levels else "ok"


def _strict_meta(text: str) -> tuple[dict, Optional[Finding]]:
    """The frontmatter of ``text``, or a ``frontmatter-parse`` error carrying the YAML message."""
    try:
        meta, _ = parse_frontmatter_strict(text)
    except (yaml.YAMLError, RecursionError) as exc:  # nesting too deep for the YAML parser
        message = " ".join(str(exc).split())  # the YAML message is several lines; keep one
        return {}, Finding(
            "error", "frontmatter-parse", f"frontmatter is not valid YAML: {message}"
        )
    return meta, None


def _frontmatter_findings(skill: Skill, *, at_root: bool = False) -> tuple[list[Finding], bool]:
    """The spec checks on the frontmatter of ``skill``, and its ``disable-model-invocation`` flag.

    ``at_root``: the ``SKILL.md`` sits directly in the scanned root, so its directory name is the
    name of a checkout, which says nothing about the skill; the name/directory comparison is skipped.
    """
    meta, parse_error = _strict_meta(skill.path.read_text(encoding="utf-8"))
    if parse_error is not None:
        return [parse_error], False
    dir_name = skill.path.parent.name
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
        if len(declared) > NAME_MAX or not NAME_PATTERN.fullmatch(declared):
            findings.append(
                Finding(
                    "error",
                    "name-format",
                    f"name {declared!r} is not kebab-case (lowercase letters, digits, "
                    f"single hyphens; at most {NAME_MAX} characters)",
                )
            )
    if len(skill.description) > DESCRIPTION_MAX:
        findings.append(
            Finding(
                "error",
                "description-too-long",
                f"description is {len(skill.description)} characters; the limit is {DESCRIPTION_MAX}",
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


def _outside_fences(body: str) -> list[str]:
    """The lines of ``body`` that are not inside a fenced code block."""
    lines: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            lines.append(line)
    return lines


def _file_ref(raw: str) -> Optional[str]:
    """The skill-relative file a reference names, or None if it names no file to check.

    None for a URL, a bare anchor, an absolute path, or a template with a variable other
    than ``${CLAUDE_SKILL_DIR}``.
    """
    text = raw.split("#", 1)[0].strip()
    if not text or "://" in text or text.startswith(("mailto:", "/")):
        return None
    if text.startswith(SKILL_DIR_VAR):
        return text[len(SKILL_DIR_VAR) :] or None
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


def _bundle_findings(skill: Skill, source_root: Path) -> list[Finding]:
    """Link and path checks on the body of ``skill``: missing targets, or targets outside it."""
    skill_dir = skill.path.parent
    real_dir = skill_dir.resolve()
    lines = _outside_fences(skill.body)
    links = dict.fromkeys(m for line in lines for m in LINK_RE.findall(line))
    spans = dict.fromkeys(
        s.strip()
        for line in lines
        for s in CODE_SPAN_RE.findall(line)
        if s.strip().startswith("../")
    )
    findings: list[Finding] = []
    for raw in links:
        text = _file_ref(raw)
        if text is None:
            continue
        target = _resolved(skill_dir / text)
        if target is not None and _exists(target):
            if not target.is_relative_to(real_dir):
                findings.append(
                    Finding(
                        "warn",
                        "link-escapes-skill",
                        f"link {raw!r} points outside the skill directory; it breaks when "
                        "the skill is installed alone",
                    )
                )
            continue
        from_root = ""
        if _exists(source_root / text):
            from_root = " (it exists from the catalog root: the base is wrong)"
        findings.append(
            Finding(
                "warn",
                "link-missing",
                f"link {raw!r} names no file relative to the skill directory{from_root}",
            )
        )
    for raw in spans:
        text = _file_ref(raw)
        if text is None:
            continue
        target = _resolved(skill_dir / text)
        if target is not None and _exists(target) and not target.is_relative_to(real_dir):
            findings.append(
                Finding(
                    "warn",
                    "path-escapes-skill",
                    f"{raw!r} points outside the skill directory; it breaks when the skill "
                    "is installed alone",
                )
            )
    return findings


def _description_findings(skill: Skill) -> list[Finding]:
    """A ``warn`` when the description has no "when to use" clause."""
    if not WHEN_RE.search(" ".join(skill.description.split())):
        return [
            Finding(
                "warn",
                "description-no-when",
                "description has no 'when to use' or 'use when' clause; the model chooses "
                "the skill from the description alone",
            )
        ]
    return []


def _tokens(description: str) -> frozenset[str]:
    """The lowercase words of three or more letters in ``description``, less the stop words."""
    words = re.findall(r"[a-z]{3,}", description.lower())
    return frozenset(w for w in words if w not in STOP_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _mirror_drift(loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]]) -> None:
    """``mirror-drift`` on each skill that shares its directory name with a differing copy."""
    by_dir_name: dict[str, list[tuple[SkillCatalogEntry, Skill, SkillReview]]] = {}
    for item in loaded:
        by_dir_name.setdefault(item[1].path.parent.name, []).append(item)
    for group in by_dir_name.values():
        for _, _, review in group:
            others = [
                _relative_to_root(o_skill.path.parent, o_entry.source_root)
                for o_entry, o_skill, o_review in group
                if o_review.content_sha256 != review.content_sha256
            ]
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
    for i, (entry, skill, review) in enumerate(shown):
        for j in range(i + 1, len(shown)):
            o_entry, o_skill, o_review = shown[j]
            if o_review.content_sha256 == review.content_sha256:
                continue  # an identical SKILL.md: already a duplicate
            if o_skill.path.parent.name == skill.path.parent.name:
                continue  # the same skill in another path: already a mirror-drift
            similarity = _jaccard(tokens[i], tokens[j])
            if similarity >= NEAR_DUPLICATE_JACCARD:
                pairs = (
                    (review, _relative_to_root(o_skill.path.parent, o_entry.source_root)),
                    (o_review, _relative_to_root(skill.path.parent, entry.source_root)),
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


def review_skills(entries: Sequence[SkillCatalogEntry]) -> list[Optional[SkillReview]]:
    """One :class:`SkillReview` per entry, in order.

    None for an entry that did not load, unless its frontmatter is broken YAML: then its
    review carries the ``frontmatter-parse`` error. Of each identical ``SKILL.md``, the
    copy nearest the root is the original; every other copy names it in ``duplicate_of``.
    Mirror drift and near-duplicate descriptions are checked across all the loaded skills,
    and a skill with ``disable-model-invocation`` set is left out of the description checks.
    """
    reviews: list[Optional[SkillReview]] = []
    copies: list[tuple[SkillReview, str, str]] = []  # (review, skill directory, digest) per loaded skill
    loaded: list[tuple[SkillCatalogEntry, Skill, SkillReview]] = []
    for entry in entries:
        if not entry.loaded or entry.skill is None:
            reviews.append(_unloaded_review(entry))
            continue
        skill = entry.skill
        findings, hidden = _frontmatter_findings(
            skill, at_root=skill.path.parent.resolve() == entry.source_root.resolve()
        )
        review = SkillReview(findings=findings, disable_model_invocation=hidden)
        review.findings += _bundle_findings(skill, entry.source_root)
        if not hidden:
            review.findings += _description_findings(skill)
        review.content_sha256 = _skill_digest(skill)
        where = _relative_to_root(skill.path.parent, entry.source_root)
        copies.append((review, where, review.content_sha256))
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
    return reviews

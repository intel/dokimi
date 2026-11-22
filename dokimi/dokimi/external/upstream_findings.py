# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --upstream``: what a skill copy inherits from the upstream version it matches.

The copy is matched by :mod:`dokimi.external.upstream` (bytes first, then name and similarity), and the match gives
three findings, none of them an error:

- ``upstream-stale`` (note): the copy matches an upstream version that is not the upstream head.
- ``license-dropped-from-upstream`` (warn): the copy is edited, its upstream version declares a license, and the copy
  declares none. An unedited copy has the upstream bytes, so it cannot drop one.
- ``risk-inherited-from-upstream`` (warn): the copy keeps a risk the upstream version has too: unrestricted Bash
  (``allowed-tools-unrestricted-bash``), ``pipe-to-shell`` or ``permission-bypass-flag``. The copy's side is the risk
  codes its own review already has, bundled files included. The upstream side is its SKILL.md alone, because an
  upstream version is a text (a blob or a manifest entry), not a folder.
"""

from __future__ import annotations

from typing import Optional, Sequence

from dokimi.utils import parse_frontmatter

from .allowed_tools import RISKY_COMMAND_CODES, grants_unrestricted_bash
from .command_checks import skill_md_findings
from .upstream import Index, Match, frontmatter_name
from .validate import Finding

UNRESTRICTED_BASH = "allowed-tools-unrestricted-bash"
RISK_CODES = frozenset((UNRESTRICTED_BASH, *RISKY_COMMAND_CODES))


def _meta(text: str) -> dict:
    """The frontmatter of a SKILL.md text; a broken block reads as none."""
    meta, _ = parse_frontmatter(text.removeprefix("\ufeff"))
    return meta if isinstance(meta, dict) else {}


def _license(meta: dict) -> Optional[str]:
    value = meta.get("license")
    text = "" if value is None else str(value).strip()
    return text or None


def upstream_risks(text: str) -> frozenset[str]:
    """The risk codes of one upstream version's SKILL.md: unrestricted Bash, and the risky commands in that text."""
    codes = {code for code, _ in skill_md_findings(text) if code in RISKY_COMMAND_CODES}
    if grants_unrestricted_bash(_meta(text).get("allowed-tools")):
        codes.add(UNRESTRICTED_BASH)
    return frozenset(codes)


def _behind_detail(match: Match) -> str:
    version = match.version
    parts = []
    if version.behind:
        parts.append(f"{version.behind} commits")
    if version.months_behind is not None:
        parts.append(f"{version.months_behind} months")
    return " and ".join(parts) + " behind the upstream head" if parts else "behind the upstream head (history not read)"


def upstream_findings(match: Optional[Match], copy_raw: bytes, copy_risks: frozenset[str],
                      head_known: bool = True) -> list[Finding]:
    """The upstream findings of one skill copy: its ``match``, its SKILL.md bytes, and its own risk codes (``RISK_CODES``).

    ``head_known`` is False for a manifest path with no entry flagged as head: such a version is not stale, because nothing says which is the head.
    """
    if match is None:
        return []
    version = match.version
    where = f"{version.upstream.name}:{version.path}"
    findings: list[Finding] = []
    if head_known and not version.head:
        findings.append(Finding(
            "note", "upstream-stale",
            f"matches {where} at an older version, {_behind_detail(match)}",
        ))
    if match.edited:
        upstream_license = _license(_meta(version.text))
        if upstream_license and _license(_meta(copy_raw.decode("utf-8", errors="replace"))) is None:
            findings.append(Finding(
                "warn", "license-dropped-from-upstream",
                f"the copy of {where} declares no license; the upstream version declares {upstream_license!r}",
            ))
    inherited = copy_risks & upstream_risks(version.text)
    if inherited:
        findings.append(Finding(
            "warn", "risk-inherited-from-upstream",
            f"keeps {', '.join(sorted(inherited))} from {where}, and the upstream version has "
            f"{'it' if len(inherited) == 1 else 'them'} too",
        ))
    return findings


def add_upstream_findings(index: Index, skills: Sequence, reviews: Sequence, ignore: frozenset = frozenset()) -> None:
    """Append the upstream findings to each loaded skill's review, in place. ``skills`` and ``reviews`` pair up by position.

    ``ignore`` is the ``--ignore`` set: an ignored risk code is not inherited either, so no finding prints it.
    """
    for entry, review in zip(skills, reviews, strict=True):
        if review is None or not entry.loaded or entry.skill is None:
            continue
        if any(f.code == "unreadable" for f in review.findings):
            continue  # the review already says the SKILL.md cannot be read: there are no bytes to match
        raw = entry.skill.path.read_bytes()
        copy_risks = frozenset(f.code for f in review.findings) & (RISK_CODES - ignore)
        match = index.match(raw, frontmatter_name(raw))
        head_known = match is None or index.head_known(match.version)
        review.findings += upstream_findings(match, raw, copy_risks, head_known=head_known)

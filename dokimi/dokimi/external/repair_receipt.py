# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The repair receipt of ``catalog repair-receipt``: which findings one edit cleared, added or left (#960).

It compares two ``catalog check --format json`` reports, one taken before the edit and one after. A finding is
identified by its whole row, the skill it belongs to plus its level, code and message, so a message whose wording
changed counts as one cleared row and one added row. The counts are rows, not affected files.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


_FIELDS = ("level", "code", "message")
_BAD = ("not a `catalog check --format json` report: each skill is an object with a `relpath` (a string or null) whose `findings` is a list of objects "
        "with a string `level`, `code` and `message` (#960, docs/external-content.md, Repair receipt)")


def _rows(report: dict) -> list[dict[str, Any]]:
    """Every finding of the report as ``{"skill", "level", "code", "message"}``; other finding fields are left out."""
    skills = report.get("skills")
    if not isinstance(skills, list):
        raise ValueError("not a `catalog check --format json` report: it has no `skills` list (#960)")
    rows = []
    for skill in skills:
        findings = skill.get("findings") if isinstance(skill, dict) else None  # `catalog check` always writes it
        if not isinstance(skill, dict) or "relpath" not in skill:
            raise ValueError(_BAD)
        name = skill["relpath"]
        if name is None:  # `catalog_scan` writes null for a pathless manifest entry; `path` then names it
            name = skill.get("path")
        if not isinstance(findings, list) or not (name is None or isinstance(name, str)):
            raise ValueError(_BAD)
        for finding in findings:
            if not isinstance(finding, dict) or not all(isinstance(finding.get(k), str) for k in _FIELDS):
                raise ValueError(_BAD)
            rows.append({"skill": name, **{k: finding[k] for k in _FIELDS}})
    return rows


def _key(row: dict) -> tuple:
    return (row["skill"], *(row[k] for k in _FIELDS))


def _minus(rows: list[dict], other: list[dict]) -> list[dict]:
    """The rows of ``rows`` left once each row of ``other`` has used up one equal row (a multiset difference)."""
    pool = Counter(map(_key, other))
    left = []
    for row in rows:
        key = _key(row)
        if pool[key] > 0:
            pool[key] -= 1
        else:
            left.append(row)
    return left


def repair_receipt(before: dict, after: dict, edited: str) -> dict:
    """The receipt for the edit of ``edited``: the findings it ``cleared``, ``added`` and ``retained``, as rows.

    A row that occurs twice before and once after is cleared once and retained once.
    """
    old, new = _rows(before), _rows(after)
    cleared, added = _minus(old, new), _minus(new, old)
    retained = _minus(new, added)
    return {
        "mode": "repair_receipt",
        "edited": edited,
        "cleared": cleared,
        "added": added,
        "retained": retained,
        "counts": {"cleared": len(cleared), "added": len(added), "retained": len(retained)},
    }

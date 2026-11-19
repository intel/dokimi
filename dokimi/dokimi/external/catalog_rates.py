# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shares of skills per state and per finding code, per skill and per repo (roadmap item 19).

A catalog can hold one repo with thousands of skills next to many with one each, so a per-skill share is
mostly the big repo's. The per-repo share weights every repo equally: the mean, over the repos, of the share
of that repo's skills that have the state or code. Both are reported side by side.

A repo is the nearest directory from a skill's own directory up to the scanned root that holds a ``.git``
entry, or the scanned root itself when none does.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

STATES = ("ok", "warn", "error")
TEXT_CODES = 5  # the codes the text report names: the most common ones


class RateTally:
    """Counts the rows by repo; :meth:`summary` turns the counts into the shares."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._abs_root = Path(os.path.abspath(root))
        self.skills: Counter[str] = Counter()  # repo -> skills
        self.states: dict[str, Counter[str]] = defaultdict(Counter)  # state -> repo -> skills
        self.codes: dict[str, Counter[str]] = defaultdict(Counter)  # code -> repo -> skills with the code
        self._repos: dict[tuple[str, ...], str] = {}

    def add(self, row: dict) -> None:
        """Count one skill row: its state, and each finding code it carries once."""
        repo = self._repo(row.get("relpath"))
        self.skills[repo] += 1
        self.states[row["state"]][repo] += 1
        for code in {finding["code"] for finding in row["findings"]}:
            self.codes[code][repo] += 1

    def _repo(self, relpath: Optional[str]) -> str:
        """The row's repo key: its nearest ``.git`` ancestor under the root, or ``.``.

        The path is normalised lexically against the root first, so a ``..`` or an absolute path under a
        relative root names its repo the same way as the plain relative path. Symlinks are not resolved.
        """
        if not relpath:
            return "."
        rel = os.path.relpath(os.path.join(self._abs_root, relpath), self._abs_root)
        parts = tuple(part for part in rel.split(os.sep) if part not in ("", "."))
        for end in range(len(parts), 0, -1):
            prefix = parts[:end]
            if prefix not in self._repos:
                is_repo = (self._abs_root.joinpath(*prefix) / ".git").exists()
                self._repos[prefix] = "/".join(prefix) if is_repo else ""
            if self._repos[prefix]:
                return self._repos[prefix]
        return "."

    def summary(self) -> dict:
        """``{"repos", "skills", "states", "codes"}``: each state and code with ``skills``, ``rate`` and ``repo_rate``.

        ``rate`` is the share of all skills that have it; ``repo_rate`` the mean over the repos of the share of
        that repo's skills that have it, so every repo counts once. Rates are rounded to four places.
        """
        total = sum(self.skills.values())
        repos = len(self.skills)

        def entry(by_repo: Counter[str]) -> dict:
            count = sum(by_repo.values())
            per_repo = sum(n / self.skills[repo] for repo, n in by_repo.items()) / repos if repos else 0.0
            return {"skills": count, "rate": round(count / total, 4) if total else 0.0,
                    "repo_rate": round(per_repo, 4)}

        return {
            "repos": repos,
            "skills": total,
            "states": {state: entry(self.states.get(state, Counter())) for state in STATES},
            "codes": {code: entry(self.codes[code]) for code in sorted(self.codes)},
        }


def text_lines(summary: dict) -> list[str]:
    """The text report's rate lines: nothing with one repo, where the two shares are the same."""
    if summary["repos"] <= 1:
        return []
    lines = [f"# rates per skill and per repo ({summary['repos']} repos, each repo counted once):"]
    for state in STATES:
        lines.append(_line(state, summary["states"][state]))
    top = sorted(summary["codes"].items(), key=lambda item: (-item[1]["skills"], item[0]))[:TEXT_CODES]
    lines.extend(_line(code, entry) for code, entry in top)
    return lines


def _line(name: str, entry: dict) -> str:
    return f"#   {name}: {entry['rate']:.1%} of skills, {entry['repo_rate']:.1%} per repo ({entry['skills']} skills)"

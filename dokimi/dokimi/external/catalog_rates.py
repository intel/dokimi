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

from dokimi.external.skill_format import not_checked

STATES = ("ok", "warn", "error", "skipped", "not_checked")
TEXT_CODES = 5  # the codes the text report names: the most common ones


class RateTally:
    """Counts the rows by repo; :meth:`summary` turns the counts into the shares."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._abs_root = Path(os.path.realpath(root))
        self.skills: Counter[str] = Counter()  # repo -> skills
        self.states: dict[str, Counter[str]] = defaultdict(Counter)  # state -> repo -> skills
        self.codes: dict[str, Counter[str]] = defaultdict(Counter)  # code -> repo -> skills with the code
        self._repo_names: dict[tuple[int, int], str] = {}
        self._repos: dict[tuple[str, ...], str] = {}

    def add(self, row: dict) -> None:
        """Count one skill row: its state, and each finding code it carries once."""
        repo = self._repo(row.get("relpath"))
        self.skills[repo] += 1
        state = "not_checked" if not_checked(row) else row["state"]
        self.states[state][repo] += 1
        for code in {finding["code"] for finding in row["findings"]}:
            self.codes[code][repo] += 1

    def _repo(self, relpath: Optional[str]) -> str:
        """The row's repo key: its nearest ``.git`` ancestor under the root, or ``.``.

        Follow directory symlinks before walking the ancestors, so a subsequent ``..`` uses the target
        directory and a link to the scan root stays in its root bucket.
        """
        if not relpath:
            return "."
        directory = Path(os.path.realpath(self._abs_root / relpath))
        if not directory.is_relative_to(self._abs_root):
            return "."
        parts = directory.relative_to(self._abs_root).parts
        for end in range(len(parts), 0, -1):
            prefix = parts[:end]
            if prefix not in self._repos:
                directory = self._abs_root.joinpath(*prefix)
                key = ""
                if (directory / ".git").exists():
                    stat = directory.stat()
                    key = "/".join(prefix)
                    if stat.st_ino:
                        key = self._repo_names.setdefault((stat.st_dev, stat.st_ino), key)
                    else:
                        key = os.path.normcase(os.path.realpath(directory))
                self._repos[prefix] = key
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
            if not total:
                return {"skills": 0, "rate": None, "repo_rate": None}
            per_repo = sum(n / self.skills[repo] for repo, n in by_repo.items()) / repos
            return {"skills": count, "rate": round(count / total, 4),
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
    top = sorted(summary["codes"].items(), key=lambda item: (-max(item[1]["rate"], item[1]["repo_rate"]), item[0]))[:TEXT_CODES]
    lines.extend(_line(code, entry) for code, entry in top)
    return lines


def _line(name: str, entry: dict) -> str:
    return f"#   {name}: {entry['rate']:.1%} of skills, {entry['repo_rate']:.1%} per repo ({entry['skills']} skills)"

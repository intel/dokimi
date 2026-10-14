#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Report the state of every open PR, for the job summary.

Every PR is meant to be based on ``main`` (CONTRIBUTING.md: no stacked PRs). With several
PRs open at once, what goes wrong is still not visible from any one PR's checks:

- a file edited by several open PRs at once -- a schema, ``pyproject.toml``, a workflow --
  where whichever merges second inherits a conflict or a silent semantic clash;
- a PR that has fallen far behind ``main``, or no longer merges cleanly onto it.

Stacked PRs still in flight from before that rule are reported too: a PR whose base
branch was merged or deleted, so it targets nothing (it needs a retarget to ``main``),
and a PR that no longer merges onto its base branch.

Read-only: it fetches branches and computes merges with ``git merge-tree``; it does not
comment, push or label. Needs ``GITHUB_TOKEN`` (pull-requests: read) and a full clone.

Usage:
    python scripts/stack_health.py --repo owner/name [--main main] >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections import defaultdict

# Files whose concurrent edits matter beyond a textual conflict.
SHARED_PREFIXES = (
    "dokimi/dokimi/schemas/",
    "dokimi/pyproject.toml",
    "dokimi/requirements",
    ".github/workflows/",
)


def _api(url: str, token: str) -> list | dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed API host  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def _paged(url: str, token: str) -> list:
    out: list = []
    page = 1
    while True:
        batch = _api(f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}", token)
        assert isinstance(batch, list)
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def _merges_cleanly(base: str, num: int) -> bool | None:
    """True/False from ``git merge-tree --write-tree``; None when git cannot tell.

    merge-tree exits 1 both on a conflict and on a ref it cannot resolve ("not something
    we can merge"), so a PR head that was never fetched is checked first: a fetch problem
    must not read as a conflict.
    """
    head = f"refs/remotes/pull/{num}"
    if _git("rev-parse", "-q", "--verify", f"{head}^{{commit}}", check=False).returncode != 0:
        return None
    r = _git("merge-tree", "--write-tree", "--name-only", f"origin/{base}",
             head, check=False)
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--main", default="main", help="the trunk branch")
    parser.add_argument("--api", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")

    base_url = f"{args.api}/repos/{args.repo}"
    prs = _paged(f"{base_url}/pulls?state=open", token)
    # PR heads through refs/pull/N/head, not by branch name: a fork PR's branch is not in this
    # repo (or is a different branch that happens to share its name).
    _git("fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*",
         "+refs/pull/*/head:refs/remotes/pull/*")
    branches = set(_git("for-each-ref", "--format=%(refname:lstrip=3)",
                        "refs/remotes/origin").stdout.split())
    # Only a same-repo PR's branch can be another PR's base.
    by_head = {pr["head"]["ref"]: pr for pr in prs
               if (pr["head"].get("repo") or {}).get("full_name") == args.repo}

    rows, touched = [], defaultdict(list)
    for pr in sorted(prs, key=lambda p: p["number"]):
        num, base = pr["number"], pr["base"]["ref"]
        problems = []
        if base != args.main and base not in by_head:
            problems.append(f"base `{base}` is not an open PR's branch"
                            + ("" if base in branches else " (deleted)"))
        clean = _merges_cleanly(base, num) if base in branches else None
        if clean is False:
            problems.append(f"conflicts with `{base}`")
        depth, root = 0, pr
        while root["base"]["ref"] in by_head and depth < 50:
            root, depth = by_head[root["base"]["ref"]], depth + 1
        behind = None
        if root["base"]["ref"] == args.main:
            r = _git("rev-list", "--count",
                     f"refs/remotes/pull/{root['number']}..origin/{args.main}", check=False)
            behind = int(r.stdout.strip()) if r.returncode == 0 else None
        files = _paged(f"{base_url}/pulls/{num}/files", token)
        for f in files:
            if f["filename"].startswith(SHARED_PREFIXES):
                touched[f["filename"]].append(num)
        rows.append((num, pr["draft"], base, depth, behind, clean, problems, pr["title"]))

    print(f"## Open PR stack health ({len(rows)} open)\n")
    print("| PR | draft | base | depth | root behind main | merges onto base | problems |")
    print("|---|---|---|---|---|---|---|")
    for num, draft, base, depth, behind, clean, problems, title in rows:
        mark = {True: "yes", False: "**no**", None: "?"}[clean]
        title = title[:60].replace("|", "\\|")  # a bare | would split the row
        print(f"| #{num} {title} | {'yes' if draft else ''} | `{base}` | {depth} "
              f"| {'' if behind is None else behind} | {mark} | {'; '.join(problems)} |")
    shared = {f: nums for f, nums in sorted(touched.items()) if len(nums) > 1}
    print(f"\n## Shared files edited by more than one open PR ({len(shared)})\n")
    for path, nums in shared.items():
        print(f"- `{path}`: " + ", ".join(f"#{n}" for n in sorted(nums)))
    n_bad = sum(1 for r in rows if r[6])
    print(f"\n{n_bad} PR(s) with problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

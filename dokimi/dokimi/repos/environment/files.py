# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""File inventory: sizes, generated and minified files, test files, large files.

``large`` is measured against what an agent reads, not what a disk holds: a 100 KB source file
is already ~30k tokens, so the default threshold is far below what a file-size lint uses.
"""

from __future__ import annotations

import re
from typing import Any

from dokimi.repos.source import GitTree, RepoTree, SourceError, WorkTree, _git

LARGE_BYTES = 100_000

_LOCKFILES = frozenset({
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb", "npm-shrinkwrap.json", "uv.lock",
    "poetry.lock", "Pipfile.lock", "pdm.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock",
    "gradle.lockfile",
})
_GENERATED = re.compile(r"(^|/)(deps|vendor|third[_-]?party|generated|__generated__)/|(^|/)tests?/(fixtures|baselines)/"
                        r"|\.generated\.|\.pb\.(go|cc|h)$|_pb2\.py$", re.I)
_MINIFIED = re.compile(r"\.(min|bundle)\.(js|css)$", re.I)
_TEST_FILE = re.compile(
    r"\.(test|spec)\.[cm]?[jt]sx?$|_test\.go$|(^|/)(test_[^/]+|[^/]+_test)\.py$"
    r"|(^|/)[A-Za-z0-9]+(Test|Tests|Spec|IT)\.(java|kt|kts|scala|cs)$|_(test|spec)\.(rb|exs?)$"
    r"|_test\.(cc|cpp|cxx|c)$|Tests?\.swift$|(^|/)(__tests__|tests?|spec|specs)/", re.I)


def is_generated(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in _LOCKFILES or _GENERATED.search(path) is not None


def sizes(tree: RepoTree) -> dict[str, int] | None:
    """Byte size of every file, or None when it cannot be had without fetching blobs."""
    if isinstance(tree, WorkTree):
        out = {}
        for f in tree.files():
            try:
                out[f] = (tree.root / f).lstat().st_size
            except OSError:
                continue
        return out
    if isinstance(tree, GitTree):
        try:
            if _git(tree.git_dir, "config", "--get", "extensions.partialclone", check=False).strip():
                return None  # `ls-tree -l` would fetch every missing blob one by one
            listing = _git(tree.git_dir, "ls-tree", "-r", "-z", "-l", tree.sha)
        except SourceError:
            return None
        out = {}
        for entry in listing.split("\0"):
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) == 4 and parts[1] == "blob" and parts[3].isdigit():
                out[path] = int(parts[3])
        return out
    return None


def inventory(files: list[str], size_of: dict[str, int] | None, large: int = LARGE_BYTES) -> dict[str, Any]:
    generated = [f for f in files if is_generated(f)]
    minified = [f for f in files if _MINIFIED.search(f)]
    tests = [f for f in files if _TEST_FILE.search(f) and not is_generated(f)]
    skip = set(generated) | set(minified)
    big = [] if size_of is None else [
        {"path": f, "bytes": -n} for n, f in sorted((-size_of.get(f, 0), f) for f in files
                                                    if f not in skip and size_of.get(f, 0) > large)]

    def total(paths: list[str]) -> int | None:
        return None if size_of is None else sum(size_of.get(f, 0) for f in paths)

    return {
        "files": len(files), "bytes": total(files),
        "generated": {"files": len(generated), "bytes": total(generated)},
        "minified": minified,
        "test_files": len(tests),
        "large_threshold_bytes": large,
        "large": big,
    }

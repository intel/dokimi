# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""L0 readiness: is a repository's guidance true of its own tree? (contract §8)

:func:`scan_repo` runs the framework's generic claim checks and build-system
adapters on one snapshot, merges the repository's own ``static`` output
(``repo_static.v1``) when one is given, and returns a ``repo_readiness.v1``
payload. No LLM call, no network beyond fetching the snapshot.
"""

from __future__ import annotations

import posixpath
from typing import Any

from ..manifest import RepoManifest, match_glob
from ..source import RepoTree, SourceError
from ..static import STATUS_CLASSES, check_static, status_class
from .adapters import ADAPTERS, detect
from .core import Claim, Scan

__all__ = ["ADAPTERS", "Claim", "Scan", "guidance_files", "scan_repo"]


def guidance_files(tree: RepoTree, manifest: RepoManifest) -> list[str]:
    """Files the manifest's guidance globs and ``also_scan`` match, in tree order."""
    patterns = (*manifest.guidance_globs, *manifest.also_scan)
    return [f for f in tree.files() if any(match_glob(p, f) for p in patterns)]


def _coverage_warnings(tree: RepoTree, manifest: RepoManifest, files: list[str]) -> tuple[list[str], bool]:
    """Warnings about guidance the manifest names but the tree lacks, and whether a gate should fail.

    A glob that matches nothing is only a warning (it may name a file a later base adds); a
    missing ``also_scan`` file, or no guidance at all, means the scan checked less than it says.
    """
    tree_files = tree.files()
    warnings = [f"guidance glob matches no file: {p}" for p in manifest.guidance_globs
                if not any(match_glob(p, f) for f in tree_files)]
    missing = [p for p in manifest.also_scan if not any(match_glob(p, f) for f in tree_files)]
    warnings += [f"guidance/also_scan matches no file: {p}" for p in missing]
    if not files:
        warnings.append("no guidance file to scan")
    return warnings, bool(missing) or not files


def _is_build_input(path: str) -> bool:
    """A Bazel input (``.bzl``, BUILD, ``.bazelrc``, MODULE/WORKSPACE) whose text the claim checks rely on."""
    name = posixpath.basename(path)
    return (name.endswith((".bzl", ".bazelrc", ".BUILD", ".BUILD.bazel")) or name.startswith(("BUILD", "WORKSPACE"))
            or name == "MODULE.bazel")


def scan_repo(tree: RepoTree, manifest: RepoManifest, static: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ``repo_readiness.v1`` payload (without ``schema_version``) for ``tree``.

    ``static`` is the document the repository's ``static`` entry point wrote.
    Its claims replace the framework's claim on the same file, line and text,
    because the repository knows its own rules better.
    """
    files = guidance_files(tree, manifest)
    scan = Scan(tree, frozenset(manifest.build_systems), repo_url=manifest.source_url, guidance=tuple(files))
    detected = not manifest.build_systems
    if detected:
        scan.adapters = frozenset(detect(scan))
    adapters = [a for a in ADAPTERS if a in scan.adapters]
    skipped: dict[str, str] = {}
    scan.preload(files, skipped)
    aliases = _aliases(tree, files)
    for f in files:
        if f not in skipped and f not in aliases:
            scan.check_file(f)
    framework = [dict(c.to_dict(), source="framework") for c in scan.claims]

    static_info: dict[str, Any] | None = None
    repository: list[dict[str, Any]] = []
    if static is not None:
        problems = check_static(static)
        extra = {k: v for k, v in static.items() if k not in ("schema", "claims")} if isinstance(static, dict) else {}
        static_info = {"errors": problems, "claims": 0, "superseded": 0, "extra": extra}
        if not problems:
            repository = [dict(c, source="repository") for c in static["claims"]]
            static_info["claims"] = len(repository)
    keys = {_key(c, scan) for c in repository if c.get("claim") is not None}
    kept = [c for c in framework if _key(c, scan) not in keys]
    if static_info is not None:
        static_info["superseded"] = len(framework) - len(kept)
    claims = sorted(repository + kept, key=lambda c: (c["file"], c["line"], c["source"]))

    warnings, incomplete = _coverage_warnings(tree, manifest, files)
    warnings += [f"guidance file not scanned: {f}: {why}" for f, why in skipped.items()]
    unread = {f: why for f, why in sorted(scan.unread.items()) if f not in skipped and _is_build_input(f)}
    warnings += [f"build file not scanned: {f}: {why}" for f, why in unread.items()]
    incomplete = incomplete or bool(skipped) or bool(unread)
    sha = getattr(tree, "sha", None)
    return {
        "repo": {"id": manifest.id, "tree": tree.label, "sha": sha, "manifest": manifest.path},
        "files": files,
        "adapters": adapters,
        "adapters_detected": detected,
        "counts": _counts(claims),
        "by_source": {"framework": _counts(framework),
                      "repository": None if static_info is None else _counts(repository)},
        "by_kind": _grouped(claims, "kind"),
        "by_file": _grouped(claims, "file"),
        "static": static_info,
        "warnings": warnings,
        "incomplete": incomplete,
        "claims": claims,
    }


def _aliases(tree: RepoTree, files: list[str]) -> set[str]:
    """Guidance files that are links to another guidance file: that file is scanned once, as itself."""
    out = set()
    for f in files:
        try:
            target = tree.resolve(f)
        except SourceError:
            continue
        if target != f and target in files:
            out.add(f)
    return out


def _key(claim: dict[str, Any], scan: Scan) -> tuple[str, int, str]:
    """Same file, line and claim; a link to this repository by URL and by path is one claim."""
    text = str(claim.get("claim"))
    return claim["file"], claim["line"], scan.own_path(text) or text


def _counts(claims: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(STATUS_CLASSES, 0)
    for c in claims:
        counts[status_class(c["status"])] += 1
    counts["total"] = len(claims)
    return counts


def _grouped(claims: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in claims:
        groups.setdefault(str(c.get(key) or "unknown"), []).append(c)
    return {k: _counts(v) for k, v in sorted(groups.items())}

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Suite manifests (``suite_manifest.v1``, contract §2.1): tasks whose base is in another repository.

A suite names the scaffold repositories, each as ``<url>@<rev>``, and the tasks of the eval tree that
take their base checkout from one of them. The task itself (prompt, grader, ``task.json``) stays in the
eval tree; only its ``base`` is looked up in the named repository. A repository that cannot be opened,
or a base that is not a commit of it, leaves those tasks' cells unsupported: not run, not failed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dokimi import process
from dokimi.artifacts import schema_errors

from .errors import PrepError
from .source import RepoSource, RepoTree, SourceError, open_source, resolve_rev
from .workspace import source_git_dir

SCHEMA_VERSION = "suite_manifest.v1"
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")  # a full commit: a branch, tag, short SHA or other ref is not


class SuiteError(ValueError):
    """The suite manifest is malformed; ``errors`` lists every problem found."""

    def __init__(self, path: str, errors: list[str]):
        self.path = path
        self.errors = errors
        super().__init__(f"{path}: invalid {SCHEMA_VERSION}:\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass(frozen=True)
class SuiteManifest:
    """A validated suite: ``repos`` maps each name to its parsed ``<url>@<rev>``, ``tasks`` each task id to a name."""

    path: str
    id: str
    repos: dict[str, RepoSource]
    tasks: dict[str, str]
    floating: bool = False  # the run allows a branch, tag or other ref as a pin


@dataclass
class SuiteBases:
    """Where each task of a suite run takes its base from, and why a task has no base.

    ``git_dirs`` and ``meta`` (``{"repo", "source"}``, for meta.json) cover the tasks whose base
    resolved; ``gaps`` gives the reason for each task that did not; ``pins`` names the commit each
    repository resolved to and ``plan`` the suite file's path, the task-to-repository mapping and the repository
    locations, which a pinned batch records.
    """

    git_dirs: dict[str, Path] = field(default_factory=dict)
    gaps: dict[str, str] = field(default_factory=dict)
    meta: dict[str, dict[str, str]] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)


def parse_suite(raw: Any, path: str = "<suite>", allow_floating: bool = False) -> SuiteManifest:
    """Validate an already-parsed suite mapping. A branch, tag or other ref is refused as a pin unless
    ``allow_floating``, in which case it is resolved when the run starts and the batch pins the result."""
    _refuse_odd_names(raw, path)
    errors = schema_errors(SCHEMA_VERSION, raw)
    if errors:
        raise SuiteError(path, errors)
    problems: list[str] = []
    repos: dict[str, RepoSource] = {}
    for name, spec in raw["repos"].items():
        try:
            source = _parse_repo(spec)
        except SourceError as exc:
            problems.append(f"repos.{name}: {exc}")
            continue
        if source.rev is None:
            problems.append(f"repos.{name}: {spec!r} is not pinned; give <url>@<sha>")
        elif not _SHA_RE.match(source.rev) and not allow_floating:
            problems.append(f"repos.{name}: {source.rev!r} is a branch, tag or ref, not a commit; pin its SHA, "
                            "or pass --allow-floating-suite-revs to resolve it when the run starts")
        else:
            repos[name] = source
    tasks: dict[str, str] = {}
    for item in raw["tasks"]:
        task_id, repo = item["id"], item["repo"]
        if task_id in tasks:
            problems.append(f"task {task_id} is listed more than once")
        elif repo not in raw["repos"]:
            problems.append(f"task {task_id}: repo {repo!r} is not declared in repos")
        else:
            tasks[task_id] = repo
    if problems:
        raise SuiteError(path, problems)
    return SuiteManifest(path=path, id=raw["id"], repos=repos, tasks=tasks, floating=allow_floating)


def _refuse_odd_names(raw: Any, path: str) -> None:
    """A ``repos`` name that is not a string is a suite error; ``schema_errors`` cannot sort it among the others."""
    repos = raw.get("repos") if isinstance(raw, dict) else None
    odd = [k for k in repos if not isinstance(k, str)] if isinstance(repos, dict) else []
    if odd:
        raise SuiteError(path, [f"repos: the name {k!r} is not a string" for k in odd])


def _parse_repo(spec: str) -> RepoSource:
    """``RepoSource.parse``, except that a pinned local path that is not a directory is kept: it is
    unavailable, which leaves its tasks unsupported (``_checkout``), not a malformed suite."""
    try:
        return RepoSource.parse(spec)
    except SourceError as exc:
        head, sep, tail = spec.strip().rpartition("@")
        if "local source is not a directory" not in str(exc) or not (sep and head and tail) or "://" in head:
            raise
        return RepoSource(location=head, rev=tail, remote=False)


def load_suite(tree: RepoTree, path: str, allow_floating: bool = False) -> SuiteManifest:
    """Read and validate the suite manifest at ``path`` inside the eval tree."""
    if not tree.is_file(path):
        raise SuiteError(path, [f"suite manifest not found in {tree.label}"])
    try:
        raw = yaml.safe_load(tree.read_text(path))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise SuiteError(path, [f"not valid YAML: {exc}"]) from exc
    return parse_suite(raw, path, allow_floating)


def _checkout(source: RepoSource, cache_dir: Path | None, refresh: bool = False) -> tuple[Path, str] | str:
    """``(git dir, commit)`` of a repository of the suite, or the reason it cannot be opened."""
    try:
        tree = open_source(source, cache_dir, refresh=refresh)
        git_dir = source_git_dir(source, tree, cache_dir)
    except (ValueError, OSError, PrepError) as exc:  # SourceError is a ValueError
        return f"{source} is unavailable: {exc}"
    sha = getattr(tree, "sha", None)
    return (git_dir, sha) if isinstance(sha, str) else f"{source} did not resolve to a commit"


def _resolve_base(source: RepoSource, git_dir: Path, base: str) -> str:
    """``base`` in ``git_dir``. A remote's cached clone may predate it, so a miss fetches it once before it is a gap."""
    try:
        return resolve_rev(git_dir, base)
    except SourceError:
        commit = re.split(r"[\^~]", base, maxsplit=1)[0]  # a parent (``<sha>^``) is fetched by its commit
        if not source.remote or not _SHA_RE.match(commit):
            raise
    process.git("-C", str(git_dir), "fetch", "origin", "--", commit)  # a failure leaves the miss below
    return resolve_rev(git_dir, base)


def resolve_suite(suite: SuiteManifest, bases: Mapping[str, str], cache_dir: Path | None = None) -> SuiteBases:
    """Open every repository of the suite and find each task's base in the repository it names.

    ``bases`` maps each suite task to the ``base`` of its task.json. Repositories are opened once
    each, all of them, so the pins do not depend on which tasks a run selects.
    """
    checkouts = {name: _checkout(source, cache_dir, refresh=suite.floating and not _SHA_RE.match(source.rev or ""))
                 for name, source in suite.repos.items()}
    for name, found in checkouts.items():  # a name shaped like a hash (a branch) is not the commit the pin says
        if suite.floating:  # the run asked for refs to be resolved: the commit it found is the pin
            break
        rev = suite.repos[name].rev
        if not isinstance(found, str) and rev and _SHA_RE.match(rev) and found[1] != rev:
            raise SuiteError(suite.path, [f"repos.{name}: {rev!r} resolves to {found[1]}, not to that commit; "
                                          "pin the full commit SHA"])
    out = SuiteBases(pins={name: found[1] for name, found in checkouts.items() if not isinstance(found, str)},
                     plan={"path": suite.path, "repos": {name: str(source) for name, source in suite.repos.items()},
                           "tasks": dict(suite.tasks)})
    for task_id, repo in suite.tasks.items():
        found = checkouts[repo]
        if isinstance(found, str):
            out.gaps[task_id] = f"base repository {repo}: {found}"
            continue
        git_dir, sha = found
        base = bases.get(task_id, "")
        try:
            _resolve_base(suite.repos[repo], git_dir, base)
        except SourceError as exc:
            out.gaps[task_id] = (f"base {base!r} is not a commit of {repo} "
                                 f"({suite.repos[repo].location}@{sha[:12]}): {exc}")
            continue
        out.git_dirs[task_id] = git_dir
        out.meta[task_id] = {"repo": repo, "source": f"{suite.repos[repo].location}@{sha}"}
    return out

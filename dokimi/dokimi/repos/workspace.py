# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Workspace preparation (contract §3) and the trees an evaluation run reads (§1.1).

Every task runs in ``<run_dir>/workspace``, made from the task's ``base``:

1. ``<base>``'s tree into the workspace, as git stores it (``read-tree`` and
   ``checkout-index`` with no user configuration: unlike ``git archive``, no
   ``core.autocrlf`` of the host and no ``export-ignore`` / ``export-subst``)
2. delete ``eval_dir``
3. ``git init`` and commit (no history, so an upstream fix cannot leak)
4. apply ``setup_patches``
5. apply the arm: its guidance part (``guidance:none`` | ``guidance:raw`` |
   ``guidance:bridge``), then its skills into ``.claude/skills/`` (:mod:`.arms`)
6. amend the commit
7. apply ``review_patch`` with ``git am`` under a neutral author
8. expire the reflog and prune unreachable objects, so the commits steps 6
   and 7 replaced (the unpatched code, the removed guidance, the upstream
   author) are not in the object store. Prep's git never runs ``gc --auto``
   (a detached one would pack those objects mid-prep, where ``prune`` cannot
   reach them), and a pack that is there anyway is rewritten first

Commits are made with a fixed identity and date and with the user's git
configuration ignored, so repetitions of one task start from the same
``start_sha``. The eval tree's ``eval_dir`` is exported once per batch to a
directory outside every workspace (``{eval_dir}``), and the image build context
is the eval tree minus the tasks directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from dokimi import process

from .arms import GUIDANCE, ArmError, Skill, parse_arm, resolve_skills, tree_sha256
from .harness_profile import HarnessProfile
from .manifest import RepoManifest, _is_within, match_glob
from .source import GitTree, RepoSource, RepoTree, SourceError, cache_path, default_cache_dir, resolve_rev

ARMS = GUIDANCE  # the guidance parts; an arm may add skills to one (:func:`.arms.parse_arm`)
SNAPSHOT_AUTHOR = ("dokimi", "dokimi@example.invalid")
REVIEW_AUTHOR = ("Contributor", "contributor@example.invalid")
FIXED_DATE = "2000-01-01T00:00:00+00:00"
_GIT_TIMEOUT_S = 1800


class PrepError(RuntimeError):
    """A workspace could not be prepared (a patch does not apply, a base is missing, ...)."""


@dataclass(frozen=True)
class Task:
    """A ``task.json`` as the runner reads it; ``dir`` is the task's directory in the exported eval dir."""

    id: str
    raw: dict[str, Any]
    dir: Path

    @property
    def base(self) -> str:
        return str(self.raw["base"])

    @property
    def family(self) -> str:
        return str(self.raw.get("family", ""))

    @property
    def setup_patches(self) -> list[str]:
        if "setup_patch" in self.raw:
            return [self.raw["setup_patch"]]
        return list(self.raw.get("setup_patches") or [])

    @property
    def review_patch(self) -> str | None:
        return self.raw.get("review_patch")

    @property
    def egress(self) -> tuple[str, ...]:
        return tuple(self.raw.get("egress") or ())

    @property
    def expected_skill(self) -> str | None:
        return self.raw.get("expected_skill") or None

    @property
    def score(self) -> dict[str, Any] | None:
        return self.raw.get("score")


def load_tasks(content_dir: Path, manifest: RepoManifest, ids: list[str] | None = None) -> list[Task]:
    """Tasks of the exported eval dir; ``ids`` selects (and must name existing tasks)."""
    eval_dir, tasks = manifest.run_paths()
    tasks_dir = content_dir / PurePosixPath(tasks).relative_to(eval_dir)
    found = {p.parent.name: p for p in sorted(tasks_dir.glob("*/task.json"))}
    wanted = ids or sorted(found)
    missing = [t for t in wanted if t not in found]
    if missing:
        raise PrepError(f"no such task(s) under {manifest.tasks}: {', '.join(missing)}")
    return [Task(t, json.loads(found[t].read_text(encoding="utf-8")), found[t].parent) for t in wanted]


def source_git_dir(source: RepoSource, tree: RepoTree, cache_dir: Path | None = None) -> Path:
    """A git directory with full history and blobs for ``source``: task bases are checked out from it.

    A local repository is used as it is (its common git dir, so a linked worktree works). A
    remote is cloned once more, bare and with blobs, because a checkout in a blob-less
    clone fetches each blob separately and ``{source_dir}`` must hold every file.
    """
    if not source.remote:
        root = Path(source.location)
        out = _run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"])
        return Path(out.strip())
    git_dir = cache_path(source.location, cache_dir or default_cache_dir()).with_suffix(".full.git")
    if not (git_dir / "HEAD").is_file():
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp = git_dir.with_name(git_dir.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        _run(["git", "clone", "--bare", "--", source.location, str(tmp)])
        tmp.rename(git_dir)
    sha = getattr(tree, "sha", None)
    if sha and not _has_commit(git_dir, sha):
        _run(["git", "-C", str(git_dir), "fetch", "origin", "--", sha])
    return git_dir


def export_eval_dir(tree: RepoTree, manifest: RepoManifest, dest: Path) -> Path:
    """Copy ``eval_dir`` of the eval tree to ``dest`` (the host side of ``{eval_dir}``)."""
    _export(tree, dest, manifest.run_paths()[0], exclude=None)
    return dest


def export_build_context(tree: RepoTree, manifest: RepoManifest, dest: Path) -> Path:
    """The image build context: the eval tree's root without the tasks directory (contract §1.1)."""
    _export(tree, dest, None, exclude=manifest.run_paths()[1])
    return dest


def _export(tree: RepoTree, dest: Path, only: str | None, exclude: str | None) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    tmp = dest.with_name(dest.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if isinstance(tree, GitTree):
        if only and not _has_object(tree.git_dir, f"{tree.sha}:{only}"):
            raise PrepError(f"{only} is not in {tree.label}")
        _checkout_tree(tree.git_dir, f"{tree.sha}:{only}" if only else tree.sha, tmp, prefix=only)
    else:
        root = Path(tree.label)
        for rel in tree.files():
            if only and not _is_within(rel, only):
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target, follow_symlinks=False)  # a link stays a link, as in git
    if exclude:
        shutil.rmtree(tmp / exclude, ignore_errors=True)
    if only:
        inner = tmp / only
        if not inner.is_dir():
            raise PrepError(f"{only} is not in {tree.label}")
        inner.rename(dest)
        shutil.rmtree(tmp)
    else:
        tmp.rename(dest)


def prepare_workspace(git_dir: Path, manifest: RepoManifest, task: Task, arm: str, run_dir: Path,
                      profile: HarnessProfile | None = None, content_dir: Path | None = None) -> dict[str, Any]:
    """Steps 1-7 into ``run_dir/workspace``; returns the ``meta.json`` fields prep owns.

    ``content_dir`` (the exported ``{eval_dir}``) is where relative skill paths of ``arm`` are found."""
    try:
        parsed = parse_arm(arm)
        skills = resolve_skills(parsed, content_dir or _content_dir(manifest, task)) if parsed.skills else []
    except ArmError as exc:
        raise PrepError(str(exc)) from exc
    bridge = profile.bridge if profile is not None else None
    if parsed.guidance == "guidance:bridge" and not bridge:
        name = profile.name if profile is not None else "no harness"
        raise PrepError(f"guidance:bridge needs a harness profile with a `bridge` key; {name} has none")
    try:
        base_sha = resolve_rev(git_dir, task.base)
    except SourceError as exc:
        raise PrepError(f"task {task.id}: base {task.base!r} is not in {git_dir}: {exc}") from exc
    base_date = _run(["git", "-C", str(git_dir), "log", "-1", "--format=%cI", base_sha]).strip()

    ws = run_dir / "workspace"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    _checkout_tree(git_dir, base_sha, ws)                                                    # 1
    shutil.rmtree(ws / manifest.run_paths()[0], ignore_errors=True)                          # 2
    env = _git_env(SNAPSHOT_AUTHOR)
    _git(ws, env, "init", "-q")                                                               # 3
    _git(ws, env, "add", "-A")
    _git(ws, env, "commit", "-q", "--no-gpg-sign", "--allow-empty", "--no-verify", "-m", "snapshot")
    patches = []
    for rel in task.setup_patches:                                                           # 4
        path = _task_file(task, rel)
        try:
            _git(ws, env, "apply", "--whitespace=nowarn", str(path))
        except PrepError as exc:
            raise PrepError(f"task {task.id}: setup patch {rel} does not apply: {exc}") from exc
        patches.append({"path": rel, "sha256": _sha256(path)})
    arm_files, bridge_skipped = _apply_arm(ws, env, manifest, parsed.guidance, bridge)       # 5
    installed = _install_skills(ws, task, skills)
    _git(ws, env, "add", "-A")                                                                # 6
    _git(ws, env, "commit", "-q", "--no-gpg-sign", "--no-verify", "--amend", "--allow-empty", "-m", "snapshot")
    if task.review_patch:                                                                     # 7
        path = _task_file(task, task.review_patch)
        review_env = _git_env(REVIEW_AUTHOR)
        try:
            _git(ws, review_env, "am", "-q", "--no-gpg-sign", str(path))
        except PrepError as exc:
            _git(ws, review_env, "am", "--abort", check=False)
            raise PrepError(f"task {task.id}: review patch {task.review_patch} does not apply: {exc}") from exc
        # the patch's own author would name the upstream contributor
        _git(ws, review_env, "commit", "-q", "--no-gpg-sign", "--no-verify", "--amend", "--no-edit", "--reset-author")
        patches.append({"path": task.review_patch, "sha256": _sha256(path)})
    _forget(ws, env)                                                                          # 8
    start_sha = _git(ws, env, "rev-parse", "HEAD").strip()
    meta: dict[str, Any] = {
        "task": task.id, "arm": arm, "base_rev": task.base, "base_sha": base_sha, "base_date": base_date,
        "patches": patches, "start_sha": start_sha, "arm_files": arm_files,
    }
    if bridge_skipped:
        meta["bridge_skipped"] = bridge_skipped
    if installed:
        meta["skills"] = installed
    return meta


def _content_dir(manifest: RepoManifest, task: Task) -> Path:
    """The exported eval dir a task directory of :func:`load_tasks` is in."""
    eval_dir, tasks = manifest.run_paths()
    return task.dir.parents[len(PurePosixPath(tasks).relative_to(eval_dir).parts)]


def _install_skills(ws: Path, task: Task, skills: list[Skill]) -> list[dict[str, str]]:
    """Copy each skill to ``.claude/skills/<name>/``; a skill the repository already has there is refused,
    as replacing it would change what the guidance arm measures."""
    out = []
    for skill in skills:
        dest = ws / skill.target
        if dest.exists():
            raise PrepError(f"task {task.id}: the workspace already has {skill.target}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill.dir, dest, symlinks=False)  # a link out of the skill would not exist in the container
        out.append({"name": skill.name, "path": skill.target, "sha256": tree_sha256(dest)})
    return out


def _forget(ws: Path, env: dict[str, str]) -> None:
    """Drop what prep replaced: the pre-amend commits in the reflog (the unpatched code, the removed
    guidance, the review patch's upstream author) and every object only they reached."""
    (ws / ".git" / "ORIG_HEAD").unlink(missing_ok=True)
    _git(ws, env, "reflog", "expire", "--expire=now", "--all")  # the agent starts from HEAD; it needs no reflog
    if any((ws / ".git" / "objects" / "pack").glob("*.pack")):  # prune only removes loose objects
        _git(ws, env, "repack", "-a", "-d", "-q")
    _git(ws, env, "prune", "--expire=now")


def _apply_arm(ws: Path, env: dict[str, str], manifest: RepoManifest, arm: str,
               bridge: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """The files the arm removed (none) or wrote (bridge), and bridge targets left alone."""
    if arm == "guidance:raw":
        return [], []
    tracked = [p for p in _git(ws, env, "ls-files", "-z").split("\0") if p]
    matched = sorted(p for p in tracked if any(match_glob(g, p) for g in manifest.guidance_globs))
    if arm == "guidance:none":
        for rel in matched:
            (ws / rel).unlink()
        return matched, []
    assert bridge is not None
    written, skipped = [], []
    for rel in matched:
        if PurePosixPath(rel).name != bridge["for"]:
            continue
        target = PurePosixPath(rel).parent / bridge["write"]
        if (ws / target).exists():
            skipped.append(target.as_posix())  # the repository's own file wins; recorded in meta.json
            continue
        (ws / target).write_text(bridge["content"] + "\n", encoding="utf-8")
        written.append(target.as_posix())
    return written, skipped


def _task_file(task: Task, rel: str) -> Path:
    path = (task.dir / rel).resolve()
    if not path.is_relative_to(task.dir.resolve()) or not path.is_file():
        raise PrepError(f"task {task.id}: {rel} is not a file in the task directory")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_commit(git_dir: Path, sha: str) -> bool:
    return _has_object(git_dir, f"{sha}^{{commit}}")


def _has_object(git_dir: Path, rev: str) -> bool:
    proc = process.git("-C", str(git_dir), "cat-file", "-e", rev, timeout=_GIT_TIMEOUT_S)
    if proc.timed_out:  # a hang is not an answer: "absent" would trigger a fetch or a wrong "not in tree"
        raise PrepError(f"git cat-file: timed out after {_GIT_TIMEOUT_S} seconds")
    return proc.ok


def _git_env(author: tuple[str, str]) -> dict[str, str]:
    """git with no user or system configuration (hooks, signing, templates) and a fixed identity and date."""
    name, email = author
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TMPDIR", "SYSTEMROOT")}
    return {**keep, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "HOME": os.devnull, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE}


def _git(ws: Path, env: dict[str, str], *args: str, check: bool = True) -> str:
    # no gc --auto: on a large tree a commit starts a detached one that packs what _forget must drop
    argv = ["git", "-C", str(ws), "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false",
            "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args]
    return _run(argv, env=env, check=check)


def _run(argv: list[str], env: dict[str, str] | None = None, check: bool = True) -> str:
    """``argv`` (a git command) with exactly ``env``, or the caller's env when it is ``None``
    (a clone or fetch needs its credential helpers); git never prompts either way."""
    policy = process.GIT_ENV if env is None else process.Env.only((), {**env, "GIT_TERMINAL_PROMPT": "0"})
    try:
        proc = process.run(argv, env=policy, timeout=_GIT_TIMEOUT_S, capture_limit=None)  # e.g. ls-files
    except OSError as exc:
        raise PrepError(f"{' '.join(argv[:4])}: {exc}") from exc
    if proc.timed_out:
        raise PrepError(f"{' '.join(argv[:4])}: timed out after {_GIT_TIMEOUT_S} seconds")
    if check and proc.returncode:
        why = process.git_failure(proc)
        lines = [ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
        if why in lines:  # no hint: the last lines, as a patch failing on two files names both
            why = " | ".join(lines[-3:])
        raise PrepError(f"{' '.join(argv[:5])} exited {proc.returncode}: {why}")
    return proc.stdout


def _checkout_tree(git_dir: Path, treeish: str, dest: Path, prefix: str | None = None) -> None:
    """``treeish`` written into ``dest`` (under ``prefix``) through a throwaway index, with no user or
    system git configuration and no line-ending conversion: the same bytes on every host. Unlike
    ``git archive``, ``export-ignore`` drops no tracked file and ``export-subst`` rewrites none."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**_git_env(SNAPSHOT_AUTHOR), "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git = ["git", "-C", str(git_dir), f"--work-tree={dest.resolve()}", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
        _run([*git, "read-tree", *([f"--prefix={prefix}/"] if prefix else []), treeish], env=env)
        _run([*git, "checkout-index", "-a", "-f"], env=env)

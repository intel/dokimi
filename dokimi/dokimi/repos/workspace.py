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
5. apply the arm (``guidance:none`` | ``guidance:raw`` | ``guidance:bridge`` |
   ``guidance:rev:<sha>`` | ``patch:<name>@<digest>``, see :class:`Arm`)
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

An arm that brings content (another revision's guidance, a patch) writes files
into the workspace, as a checkout would: no object of the source's git dir is
copied, so the workspace's object store gains only the blobs of the files it
holds, and step 8 drops the ones the arm replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from dokimi import process

from .harness_profile import HarnessProfile
from .manifest import RepoManifest, _is_within, match_glob
from .source import GitTree, RepoSource, RepoTree, SourceError, cache_path, default_cache_dir, resolve_rev

ARMS = ("guidance:none", "guidance:raw", "guidance:bridge")
REV_ARM, PATCH_ARM = "guidance:rev:", "patch:"
#: Every form ``repo run --arm`` takes
ARM_FORMS = (*ARMS, f"{REV_ARM}<rev>", f"{PATCH_ARM}<file>")
SNAPSHOT_AUTHOR = ("dokimi", "dokimi@example.invalid")
REVIEW_AUTHOR = ("Contributor", "contributor@example.invalid")
FIXED_DATE = "2000-01-01T00:00:00+00:00"
_GIT_TIMEOUT_S = 1800
# No leading "-" (never read as a git option) and no "," (the --arm separator); "/" is allowed
# (origin/main), as the arm is renamed to the SHA it resolves to.
_ARM_REV_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/^~-]*$")
_FULL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class PrepError(RuntimeError):
    """A workspace could not be prepared (a patch does not apply, a base is missing, ...)."""


class Arm(str):
    """An arm under the name cells, ``meta.json`` and ``repo compare`` use, with what prep needs to apply it.

    The name names the content and holds no ``/`` (``repo compare`` reads ``BATCH/ARM``) and no ``,``
    (the ``--arm`` separator). ``guidance:rev:<rev>`` is renamed ``guidance:rev:<full sha>`` by
    :func:`pin_arm`, so a branch that moved gives a new arm rather than more cells of the old one.
    ``patch:<file>`` is named ``patch:<file name>@<first 12 hex of its sha256>``; the file is read
    once, so every cell applies the same bytes, and its path and full digest go to ``meta.json``.
    """

    spec: str
    rev: str | None
    rev_sha: str | None
    patch: bytes | None
    patch_path: str | None
    patch_sha256: str | None

    def __new__(cls, name: str, *, spec: str | None = None, rev: str | None = None, rev_sha: str | None = None,
                patch: bytes | None = None, patch_path: str | None = None) -> Arm:
        self = super().__new__(cls, name)
        self.spec = spec or name
        self.rev, self.rev_sha = rev, rev_sha
        self.patch, self.patch_path = patch, patch_path
        self.patch_sha256 = hashlib.sha256(patch).hexdigest() if patch is not None else None
        return self


def parse_arm(spec: str) -> Arm:
    """``spec`` as ``repo run --arm`` takes it. A patch file is read here; a revision is resolved by
    :func:`pin_arm`, which needs the source's git dir."""
    if isinstance(spec, Arm):
        return spec
    if spec in ARMS:
        return Arm(spec)
    if spec.startswith(REV_ARM):
        rev = spec[len(REV_ARM):]
        if not _ARM_REV_RE.match(rev) or ".." in rev:
            raise PrepError(f"arm {spec!r}: {rev!r} is not a revision (a SHA, tag or branch of the source)")
        return Arm(spec, rev=rev)
    if spec.startswith(PATCH_ARM):
        path = Path(spec[len(PATCH_ARM):]).expanduser()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PrepError(f"arm {spec!r}: cannot read the patch: {exc}") from exc
        if not data.strip():
            raise PrepError(f"arm {spec!r}: the patch is empty")
        # one "_" at most in a row: cell names join their parts with "__"
        name = re.sub(r"[^A-Za-z0-9.-]+", "_", path.name).strip("_") or "patch"
        digest = hashlib.sha256(data).hexdigest()
        return Arm(f"{PATCH_ARM}{name}@{digest[:12]}", spec=spec, patch=data, patch_path=str(path.resolve()))
    raise PrepError(f"unknown arm {spec!r} (known: {', '.join(ARM_FORMS)})")


def pin_arm(arm: Arm, git_dir: Path, fetch: bool = False) -> Arm:
    """``guidance:rev:<rev>`` renamed ``guidance:rev:<full sha>``; any other arm as it is.

    ``fetch`` is for a remote source's cached clone: a branch or tag is fetched first, so it
    names the remote's commit of now and not the one of the clone's last fetch, and a SHA the
    clone lacks is fetched. A local repository is only read.
    """
    if arm.rev is None or arm.rev_sha is not None:
        return arm
    if fetch and not (_FULL_SHA_RE.match(arm.rev) and _has_commit(git_dir, arm.rev)):
        refs = [arm.rev] if _FULL_SHA_RE.match(arm.rev) else ["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"]
        _run(["git", "-C", str(git_dir), "fetch", "origin", "--", *refs])
    proc = process.git("-C", str(git_dir), "rev-parse", "--verify", "--quiet", f"{arm.rev}^{{commit}}",
                       timeout=_GIT_TIMEOUT_S)
    if proc.timed_out:
        raise PrepError(f"git rev-parse: timed out after {_GIT_TIMEOUT_S} seconds")
    sha = proc.stdout.strip() if proc.ok else ""
    if not _FULL_SHA_RE.match(sha):
        raise PrepError(f"arm {arm.spec}: no commit {arm.rev!r} in {git_dir}")
    return Arm(REV_ARM + sha, spec=arm.spec, rev=arm.rev, rev_sha=sha)


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
                      profile: HarnessProfile | None = None) -> dict[str, Any]:
    """Steps 1-7 into ``run_dir/workspace``; returns the ``meta.json`` fields prep owns.

    ``arm`` is an :class:`Arm` (``repo run`` parses and pins each once per batch) or an arm
    string, parsed and pinned here.
    """
    arm = pin_arm(parse_arm(arm), git_dir)
    bridge = profile.bridge if profile is not None else None
    if arm == "guidance:bridge" and not bridge:
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
    try:
        arm_files, arm_meta = _apply_arm(ws, env, git_dir, manifest, arm, bridge)            # 5
    except PrepError as exc:
        raise PrepError(f"task {task.id}: {exc}") from exc
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
        "task": task.id, "arm": str(arm), "base_rev": task.base, "base_sha": base_sha, "base_date": base_date,
        "patches": patches, "start_sha": start_sha, "arm_files": arm_files, **arm_meta,
    }
    return meta


def _forget(ws: Path, env: dict[str, str]) -> None:
    """Drop what prep replaced: the pre-amend commits in the reflog (the unpatched code, the removed
    guidance, the review patch's upstream author) and every object only they reached."""
    (ws / ".git" / "ORIG_HEAD").unlink(missing_ok=True)
    _git(ws, env, "reflog", "expire", "--expire=now", "--all")  # the agent starts from HEAD; it needs no reflog
    if any((ws / ".git" / "objects" / "pack").glob("*.pack")):  # prune only removes loose objects
        _git(ws, env, "repack", "-a", "-d", "-q")
    _git(ws, env, "prune", "--expire=now")


def _apply_arm(ws: Path, env: dict[str, str], git_dir: Path, manifest: RepoManifest, arm: Arm,
               bridge: dict[str, str] | None) -> tuple[list[str], dict[str, Any]]:
    """The files the arm removed (none), wrote (bridge, rev) or changed (patch), and its other
    ``meta.json`` fields: bridge targets left alone, the revision, the patch file."""
    if arm == "guidance:raw":
        return [], {}
    if arm.patch is not None:
        return _apply_patch(ws, env, manifest, arm)
    tracked = [p for p in _git(ws, env, "ls-files", "-z").split("\0") if p]
    matched = sorted(p for p in tracked if any(match_glob(g, p) for g in manifest.guidance_globs))
    if arm == "guidance:none" or arm.rev_sha is not None:
        for rel in matched:
            (ws / rel).unlink()
        if arm.rev_sha is None:
            return matched, {}
        return _apply_rev(ws, git_dir, manifest, arm, matched)
    assert bridge is not None
    return _apply_bridge(ws, matched, bridge)


def _apply_rev(ws: Path, git_dir: Path, manifest: RepoManifest, arm: Arm,
               removed: list[str]) -> tuple[list[str], dict[str, Any]]:
    """The guidance files of ``arm.rev_sha`` into ``ws``, whose own were ``removed``."""
    assert arm.rev_sha is not None
    written, links = _guidance_at(git_dir, manifest, arm.rev_sha)
    files = [p for p in written if p not in links]
    if files:  # checked out as files, as in step 1: no object of the source is copied
        _checkout_tree(git_dir, arm.rev_sha, ws, paths=files)
    for rel, target in links.items():  # the target's text at the link's path; the base's own copy of it stays
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(_run(["git", "-C", str(git_dir), "cat-file", "blob", f"{arm.rev_sha}:{target}"],
                                   env=_git_env(SNAPSHOT_AUTHOR)), encoding="utf-8")
    return written, {"arm_rev": {"rev": arm.rev, "sha": arm.rev_sha, "removed": sorted(set(removed) - set(written))}}


def _apply_bridge(ws: Path, matched: list[str], bridge: dict[str, str]) -> tuple[list[str], dict[str, Any]]:
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
    return written, ({"bridge_skipped": skipped} if skipped else {})


def _guidance_at(git_dir: Path, manifest: RepoManifest, sha: str) -> tuple[list[str], dict[str, str]]:
    """The files of commit ``sha`` the guidance globs match, outside ``eval_dir`` (step 2 deletes it), and for each
    one that is a symbolic link the file it resolves to in that commit. A link out of the tree, into ``eval_dir``
    or to nothing is refused."""
    eval_dir = manifest.run_paths()[0]
    listing = _run(["git", "-C", str(git_dir), "ls-tree", "-r", "-z", "--full-tree", sha], env=_git_env(SNAPSHOT_AUTHOR))
    entries = {}
    for entry in filter(None, listing.split("\0")):
        info, _, path = entry.partition("\t")
        mode, kind, oid = info.split()
        if kind == "blob":
            entries[path] = (mode, oid)
    found = sorted(p for p in entries if not _is_within(p, eval_dir)
                   and any(match_glob(g, p) for g in manifest.guidance_globs))
    links = {}
    for path in found:
        target = path
        for _ in range(8):  # a chain of links; a loop runs out
            mode, oid = entries.get(target, ("", ""))
            if mode != "120000":
                break
            dest = _run(["git", "-C", str(git_dir), "cat-file", "blob", oid], env=_git_env(SNAPSHOT_AUTHOR))
            target = posixpath.normpath(posixpath.join(posixpath.dirname(target), dest))
            if dest.startswith("/") or target == ".." or target.startswith("../"):
                raise PrepError(f"guidance {path} at {sha[:12]} links out of the tree ({dest})")
            if _is_within(target, eval_dir):
                raise PrepError(f"guidance {path} at {sha[:12]} links into eval_dir {eval_dir}")
        if target != path:
            if entries.get(target, ("",))[0] in ("", "120000"):
                raise PrepError(f"guidance {path} at {sha[:12]} links to {target}, which is not a file there")
            links[path] = target
    return found, links


def _apply_patch(ws: Path, env: dict[str, str], manifest: RepoManifest, arm: Arm) -> tuple[list[str], dict[str, Any]]:
    """``git apply`` of the arm's patch on the raw workspace; the files it changed."""
    assert arm.patch is not None
    _git(ws, env, "add", "-A")
    before = _git(ws, env, "write-tree").strip()  # after the setup patches; unreachable once step 6 commits
    with tempfile.TemporaryDirectory() as tmp:  # the bytes read once per batch, not the file as it is now
        path = Path(tmp) / "arm.patch"
        path.write_bytes(arm.patch)
        try:
            touched = _patch_paths(_git(ws, env, "apply", "--numstat", "-z", str(path)))
            _git(ws, env, "apply", "--whitespace=nowarn", str(path))
        except PrepError as exc:
            raise PrepError(f"arm {arm}: patch {arm.patch_path} does not apply: {exc}") from exc
    _git(ws, env, "add", "-A")
    changed = sorted(p for p in _git(ws, env, "diff", "--cached", "--name-only", "--no-renames", "-z", before).split("\0")
                     if p)
    eval_dir = manifest.run_paths()[0]
    # the patch's own paths too: a file .gitignore hides is written but never staged
    inside = sorted(p for p in set(changed) | touched if _is_within(p, eval_dir))
    if inside:  # the agent must not see an eval_dir, not even a patched-in one
        raise PrepError(f"arm {arm}: patch {arm.patch_path} writes into eval_dir {eval_dir}: {', '.join(inside)}")
    return changed, {"arm_patch": {"path": arm.patch_path, "sha256": arm.patch_sha256}}


def _patch_paths(numstat: str) -> set[str]:
    """Every path ``git apply --numstat -z`` names: ``added<TAB>deleted<TAB>path``, or for a rename an empty path
    followed by the old and the new one."""
    paths: set[str] = set()
    fields = numstat.split("\0")
    i = 0
    while i < len(fields):
        rest = fields[i].split("\t", 2)
        i += 1
        if len(rest) < 3:
            continue
        if rest[2]:
            paths.add(rest[2])
        else:
            paths.update(f for f in fields[i:i + 2] if f)
            i += 2
    return paths


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


def _checkout_tree(git_dir: Path, treeish: str, dest: Path, prefix: str | None = None,
                   paths: list[str] | None = None) -> None:
    """``treeish`` (only ``paths`` of it, if given) written into ``dest`` (under ``prefix``) through a
    throwaway index, with no user or system git configuration and no line-ending conversion: the
    same bytes on every host. Unlike ``git archive``, ``export-ignore`` drops no tracked file and
    ``export-subst`` rewrites none."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**_git_env(SNAPSHOT_AUTHOR), "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git = ["git", "-C", str(git_dir), f"--work-tree={dest.resolve()}", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
        _run([*git, "read-tree", *([f"--prefix={prefix}/"] if prefix else []), treeish], env=env)
        _run([*git, "checkout-index", "-f", *(["--", *paths] if paths is not None else ["-a"])], env=env)

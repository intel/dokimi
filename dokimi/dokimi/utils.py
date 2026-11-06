# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Mapping, Tuple

from dokimi.defaults import BEDROCK_MODEL_IDS

if TYPE_CHECKING:
    from dokimi.process import Completed, Env


def parse_frontmatter(text: str) -> "Tuple[Dict[str, Any], str]":
    """Split a Markdown document into (frontmatter dict, body).

    Supports a leading YAML frontmatter block delimited by ``---`` lines, the
    same convention used by Claude Code skill files and most static-site
    generators. When no frontmatter is present, returns ``({}, text)``. Frontmatter
    that is not valid YAML also returns ``{}``; use :func:`parse_frontmatter_strict`
    to see that error.

    Args:
        text: Raw file contents.

    Returns:
        Tuple of (metadata dict, body string with frontmatter stripped).
    """
    return _split_frontmatter(text, strict=False)


def parse_frontmatter_strict(text: str) -> "Tuple[Dict[str, Any], str]":
    """Like :func:`parse_frontmatter`, but frontmatter that is not valid YAML raises.

    For a validator that must name the YAML error rather than read the document
    as having no metadata. The lenient :func:`parse_frontmatter` is unchanged for
    every other caller.

    Raises:
        yaml.YAMLError: The frontmatter block is not valid YAML.
    """
    return _split_frontmatter(text, strict=True)


def _split_frontmatter(text: str, *, strict: bool) -> "Tuple[Dict[str, Any], str]":
    import yaml

    if not text:
        return {}, ""

    stripped = text.lstrip("﻿")  # tolerate a UTF-8 BOM
    lines = stripped.splitlines()
    # Same rule as the closing fence: a line that is exactly ``---``. A thematic
    # break (``-----``) or a ``--- title`` line opens nothing.
    if not lines or lines[0].rstrip() != "---":
        return {}, stripped

    # First line is the opening '---'; find the closing fence.
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw_meta = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1:]).lstrip("\n")
            try:
                meta = yaml.safe_load(raw_meta) or {}
            except yaml.YAMLError:
                if strict:
                    raise
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, body

    # No closing fence — treat the whole thing as body.
    return {}, stripped


def digest_text(text: str) -> str:
    """Content-digest a prompt or other text blob.

    Uses the ``sha256:<hexdigest>`` prefix convention the plugin-set and subject
    digests already use, so provenance fields read the same way across artifacts.
    The digest itself is the full 64-char hex: the plugin-set digest is truncated
    to 32, so the two are comparable in shape but never equal for the same input.

    Args:
        text: The text to digest.

    Returns:
        ``"sha256:"`` followed by the hex digest of the UTF-8 encoding.
    """
    import hashlib

    return "sha256:" + hashlib.sha256((text or "").encode("utf-8", errors="surrogatepass")).hexdigest()


def _plain_path(text: str) -> str:
    r"""A Windows absolute path without its extended-length prefix. os.readlink gives an absolute link target as
    \\?\C:\... (or \\?\UNC\...), while the anchors are plain: the two read as different drives.
    Any other text is returned unchanged."""
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\"):]
    return text


def _settled(target: str) -> str:
    """An absolute ``target`` with each ``..`` that follows a real directory folded into it, as the kernel reads the
    path. A ``..`` that follows a link is kept: the kernel resolves it from the link's target, which a fold would miss."""
    anchor, *names = Path(target).parts
    kept: list[str] = []
    for name in names:
        if name == "..":
            top = kept[-1] if kept else None
            here = os.path.join(anchor, *kept)
            if top not in (None, "..") and os.path.isdir(here) and not os.path.islink(here):
                kept.pop()
                continue
        kept.append(name)
    return os.path.join(anchor, *kept)


def _posix_separators(text: str, sep: str = os.sep) -> str:
    """``text`` with its native separator ``sep`` written as ``/``, so a relative link target digests the same on Windows
    (where os.readlink gives backslashes) as on POSIX. On POSIX a backslash is part of a name and is kept."""
    return text if sep == "/" else text.replace(sep, "/")


@dataclass(frozen=True)
class GitTree:
    """A work tree a digest reads through git: its top (a real path), the walk's base (top-relative, "" for the top), the
    untracked, ignored paths git leaves out (top-relative, a directory by its name as ``ls-files --directory`` gives it),
    and the blob ids of the tracked files git finds unchanged (their index entries). A tree is a snapshot: build one per
    digest. ``indexed`` is left out of the hash only: a mapping cannot hash, and equality must see the snapshot.
    ``tracked`` is every path the top's index lists, with the directories on the way to them. ``nested`` caches what
    each nested repository (top-relative) ignores, read on first use."""

    top: Path
    base: str
    ignored: frozenset[str]
    indexed: Mapping[str, str] = field(hash=False)
    tracked: frozenset[str] = field(default=frozenset(), hash=False, repr=False)
    nested: dict[str, frozenset[str]] = field(default_factory=dict, hash=False, compare=False, repr=False)

    def top_rel(self, path: Path) -> str | None:
        """``path`` relative to the top, as POSIX, its own link (if it is one) not followed; None outside the tree."""
        real = Path(os.path.realpath(path.parent)) / path.name
        try:
            return real.relative_to(self.top).as_posix()
        except ValueError:
            return None

    def skipped(self, path: Path) -> bool:
        """True for what git leaves out: an ignored path below the base, or anything under a ``.git`` (a nested repository's
        own). Inside a nested repository, what that repository's own ignore rules leave out. An ignored directory above the
        base is not this walk's to apply."""
        rel = self.top_rel(path)
        if rel is None:
            return False
        parts = rel.split("/")
        first = len(self.base.split("/")) + 1 if self.base else 1
        if ".git" in parts or any("/".join(parts[:i]) in self.ignored for i in range(first, len(parts) + 1)):
            return True
        return self._nested_skipped(path, rel, parts)

    def _nested_skipped(self, path: Path, rel: str, parts: list[str]) -> bool:
        """True when the innermost nested repository holding ``path`` ignores it (or a directory above it). False outside
        any nested repository, and for a path the top tracks (or a directory on the way to one): the top stores it, so its
        rules win. Raises OSError when git cannot say what that repository ignores."""
        repo = self._innermost_repo(parts)
        if repo is None or rel in self.tracked:
            return False
        if repo not in self.nested:
            self.nested[repo] = _nested_ignored(self.top / repo, repo)
        ignored = self.nested[repo]
        return any("/".join(parts[:i]) in ignored for i in range(len(repo.split("/")) + 1, len(parts) + 1))

    def _innermost_repo(self, parts: list[str]) -> str | None:
        """The deepest directory above the path (below the top) that holds a ``.git``: a nested repository, or None."""
        found = None
        for i in range(1, len(parts)):
            anc = "/".join(parts[:i])
            if (self.top / anc / ".git").exists():
                found = anc
        return found


def _nested_ignored(repo: Path, rel: str) -> frozenset[str]:
    """The paths the nested repository at ``repo`` (``rel`` is its place in the top) ignores, top-relative, read by its
    own git as :func:`git_tree` reads the top's: a denylist, so a name git spells otherwise (``core.ignorecase``) is not
    dropped. Its ``.git`` is pinned as the repository, so a ``.git`` git cannot read fails here instead of letting git
    walk up to the enclosing repository. A git failure raises an OSError naming the repository."""
    marker = repo / ".git"
    top = _work_git(repo, ["rev-parse", "--show-toplevel"], 60, marker=marker).stdout_bytes
    if Path(os.path.realpath(os.fsdecode(top).rstrip("\n"))) != repo:
        raise OSError(None, "git reads this .git as a repository elsewhere", os.fspath(repo))
    # plain discovery here (rev-parse has shown it finds this .git): git's ownership check applies to discovery only
    listed = _work_git(repo, ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
                       120).stdout_bytes
    return frozenset(f"{rel}/{os.fsdecode(name).rstrip('/')}" for name in listed.split(b"\0") if name)


def _git_env() -> "Env":
    from dokimi.process import Env

    # GIT_* variables (a hook's GIT_DIR or GIT_INDEX_FILE) would point git at some other repository
    return Env.only(allow=[k for k in os.environ if not k.startswith("GIT_")])


def git_tree(base: Path) -> GitTree | None:
    """The work tree holding ``base``, with what git leaves out of it. None when ``base`` is in no work tree: the digest
    then hashes the bytes on disk and leaves nothing out. A work tree git cannot read raises OSError naming it and git's
    own words (no silent fallback: the digest would count other bytes than git stores)."""
    marker = _work_marker(base)
    if marker is None:
        return None
    # read the nearest .git as the repository: plain discovery skips a broken one and finds an enclosing repository
    top_dir = os.fsdecode(_work_git(base, ["rev-parse", "--show-toplevel"], 60, marker=marker).stdout_bytes).rstrip("\n")
    # the ignored paths git would not descend into; a tracked file is never listed here
    listed = _work_git(base, ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"], 120,
                       cwd=Path(top_dir))
    top_real = Path(os.path.realpath(top_dir))
    try:
        base_rel = Path(os.path.realpath(base)).relative_to(top_real).as_posix()
    except ValueError:
        return None
    ignored = frozenset(os.fsdecode(name).rstrip("/") for name in listed.stdout_bytes.split(b"\0") if name)
    indexed, tracked = _index_blobs(top_dir)
    return GitTree(top_real, "" if base_rel == "." else base_rel, ignored, indexed, tracked)


def _work_marker(base: Path) -> Path | None:
    """The nearest ``.git`` (a directory, or a file in a worktree) at ``base`` or above it, on its filesystem: where git
    looks for a work tree, and it does not look past a filesystem boundary. None for a plain directory tree."""
    here = base.resolve()
    try:
        dev = os.stat(here).st_dev
        for directory in (here, *here.parents):
            if os.stat(directory).st_dev != dev:
                return None
            if os.path.lexists(directory / ".git"):
                return directory / ".git"
    except OSError:
        return None
    return None


def _work_git(base: Path, args: list[str], timeout: float, cwd: Path | None = None,
              marker: Path | None = None, input: bytes | None = None) -> "Completed":
    """The result of one git call in the work tree of ``base`` (run in ``cwd`` when given), with ``marker`` (a ``.git``)
    as the only repository it may read. A git that cannot run, fails or times out raises OSError naming ``base``."""
    from dokimi.process import run

    where = os.fspath(cwd or base)
    pinned = [f"--git-dir={marker}", f"--work-tree={marker.parent}"] if marker is not None else []
    try:
        done = run(["git", "-C", where, *pinned, *args], timeout=timeout, env=_git_env(), input=input,
                   capture_limit=None)
    except OSError as exc:
        raise OSError(None, f"git {args[0]} could not run: {exc.strerror or exc}", os.fspath(base)) from exc
    if not done.ok:
        why = done.stderr.strip() or ("timed out" if done.timed_out else f"exit {done.returncode}")
        raise OSError(None, f"git {args[0]} failed in this work tree: {why}", os.fspath(base))
    return done


def _index_blobs(top: str) -> tuple[dict[str, str], frozenset[str]]:
    """Every path the index lists (with its parent directories), and the index's blob id for each tracked file that git finds unchanged in the work tree. ``hash-object`` does not read
    the index, so it would fold a committed CRLF file under core.autocrlf. Flagged entries (skip-worktree, assume-unchanged)
    are left out: git does not look at those files, so their disk content is hashed."""
    staged = _work_git(Path(top), ["ls-files", "-s", "-z"], 120)
    tags = _work_git(Path(top), ["ls-files", "-v", "-z"], 120)
    # --no-optional-locks: status refreshes its view in memory and does not rewrite the index
    dirty = _work_git(Path(top), ["--no-optional-locks", "status", "--porcelain=v1", "-z", "-uno"], 120)
    changed = _worktree_changed(dirty.stdout_bytes) | _flagged(tags.stdout_bytes)
    out: dict[str, str] = {}
    tracked: set[str] = set()
    for entry in staged.stdout_bytes.split(b"\0"):
        meta, _, name = entry.partition(b"\t")
        fields = meta.split()
        if not name:
            continue
        rel = os.fsdecode(name)
        parts = rel.split("/")
        tracked.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        if len(fields) == 3 and fields[2] == b"0" and rel not in changed:
            out[rel] = os.fsdecode(fields[1])
    return out, frozenset(tracked)


def _worktree_changed(status: bytes) -> set[str]:
    """The tracked paths whose work tree differs from the index, from ``git status --porcelain=v1 -z``: an entry is
    ``XY path``, and a rename or copy in the index is followed by its origin path."""
    changed: set[str] = set()
    items = iter(status.split(b"\0"))
    for entry in items:
        if len(entry) < 4:
            continue
        if b"R" in entry[:2] or b"C" in entry[:2]:
            next(items, None)
        if entry[1:2] != b" ":
            changed.add(os.fsdecode(entry[3:]))
    return changed


def _flagged(tags: bytes) -> set[str]:
    """The index entries git does not check against the disk: skip-worktree (``S``) and assume-unchanged (lower case)."""
    return {os.fsdecode(entry[2:]) for entry in tags.split(b"\0") if entry[:1] == b"S" or entry[:1].islower()}


def _blob_ids(top: Path, rels: list[str]) -> list[str]:
    """``git hash-object`` of each path (relative to ``top``) with the work tree's filters, in one call. A git that
    fails (a required clean filter, a timeout) or answers for fewer paths raises OSError naming ``top``."""
    data = b"".join(os.fsencode(rel) + b"\n" for rel in rels)
    done = _work_git(top, ["hash-object", "--stdin-paths"], 600, input=data)
    ids = [os.fsdecode(i) for i in done.stdout_bytes.split()]
    if len(ids) != len(rels):
        raise OSError(None, f"git hash-object gave {len(ids)} ids for {len(rels)} paths", os.fspath(top))
    return ids


def file_contents(paths: Iterable[Path], tree: GitTree | None) -> dict[Path, bytes]:
    """What a digest holds for each file: git's blob id for a file in ``tree`` (the index's when git finds it unchanged,
    else ``hash-object``'s with the work tree's filters). Otherwise the sha256 of its bytes: no tree, a file outside it,
    or a name git's stdin reader would misread. A git that fails raises OSError (see :func:`_blob_ids`)."""
    found = list(dict.fromkeys(paths))
    out = _blob_contents(found, tree) if tree is not None else {}
    for path in found:
        if path not in out:
            out[path] = hashlib.sha256(path.read_bytes()).digest()
    return out


def _blob_contents(paths: list[Path], tree: GitTree) -> dict[Path, bytes]:
    out: dict[Path, bytes] = {}
    hashed: list[tuple[Path, str]] = []
    for path in paths:
        rel = tree.top_rel(path)
        if rel is None:
            continue
        if rel in tree.indexed:
            out[path] = bytes.fromhex(tree.indexed[rel])
        elif "\n" not in rel and not rel.startswith('"') and not rel.endswith("\r"):  # --stdin-paths misreads the rest
            hashed.append((path, rel))
    ids = _blob_ids(tree.top, [rel for _, rel in hashed]) if hashed else []
    out.update({path: bytes.fromhex(blob) for (path, _), blob in zip(hashed, ids, strict=True)})
    return out


def file_digest(content: bytes, executable: bool) -> str:
    """The digest of one file alone, as :func:`tree_digest` gives a file ``root`` (its path is ``.``)."""
    return hashlib.sha256(b"F .\0" + f"{int(executable)}\0".encode() + content).hexdigest()


def link_text(link: Path, root: Path) -> str:
    """A symlink's target as the digests hold it: as written, except an absolute target inside ``root``, which is
    written relative to the link. So a tree digests the same at another checkout path (the link's text is the same).
    A ``..`` in such a target is kept as written: after a link the kernel resolves it from the link's target, so
    collapsing it lexically would hash two different files alike."""
    target = os.readlink(link)
    if not os.path.isabs(target):
        return _posix_separators(target)
    target = _plain_path(target)
    if link == root:  # the root is hashed by its target, not walked: kept as written, so a loop through it stays a loop
        return target
    # the tree may be named by a path with links in it: match the target under the name as given and as resolved
    # (a link that is the root itself is not resolved, or its own target would count as inside it)
    anchors = [os.fspath(root)] if root.is_symlink() else [os.fspath(root), os.path.realpath(root)]
    for anchor in anchors:
        # Windows folds case in paths (and drive letters), so the comparison is on the normalized spelling; the target
        # keeps its own spelling in the text it returns
        key, key_anchor = os.path.normcase(target), os.path.normcase(anchor)
        try:
            inside = os.path.commonpath([key, key_anchor]) == key_anchor and key.startswith(key_anchor)
        except ValueError:  # different drives, or a relative path mixed in
            continue
        if inside:
            rest = [part for part in target[len(anchor):].split(os.sep) if part not in ("", ".")]
            depth = len(link.relative_to(root).parent.parts)  # the link's directory, as the text is read from there
            slash = "/" if target.endswith((os.sep, os.sep + ".")) else ""  # "x/" and "x/." need a directory
            if ".." in rest:
                # a target whose ".." leads out of the tree (and does not come back in) is external: hashed as written,
                # so two trees whose external targets differ do not digest alike
                lexical = os.path.normcase(os.path.normpath(target))
                if lexical != key_anchor and not lexical.startswith(key_anchor.rstrip(os.sep) + os.sep):
                    return target
                # a ".." that leaves the tree and comes back in (tree/../tree/x) is folded, so the name of the checkout's
                # directory does not reach the digest; the ".." after a link stays, as the kernel reads it
                settled = _settled(target)
                key_settled = os.path.normcase(settled)
                if key_settled != key_anchor and not key_settled.startswith(key_anchor.rstrip(os.sep) + os.sep):
                    return target
                rest = [part for part in settled[len(anchor):].split(os.sep) if part not in ("", ".")]
                return ("/".join([".."] * depth + rest) or ".") + slash  # the root itself is "." before the slash
            here = os.path.join(anchor, *link.relative_to(root).parent.parts)
            return os.path.relpath(os.path.join(anchor, *rest), here).replace(os.sep, "/") + slash
    return target  # outside the tree: as written


def _tracked_modes(base: Path, spec: str) -> dict[str, bool]:
    """The executable bit git records (mode 100755) for each file ``spec`` names under ``base``, keyed by the path
    relative to ``base``. Empty when git cannot say (no git, or ``base`` is not in a repository)."""
    from dokimi.process import Env, run

    # GIT_* variables (a hook's GIT_DIR or GIT_INDEX_FILE) would point git at some other repository's index
    env = Env.only(allow=[k for k in os.environ if not k.startswith("GIT_")])
    try:
        # literal: the name is a path, not a pattern; no capture limit: a large tree's listing must be read whole
        done = run(["git", "--literal-pathspecs", "-C", os.fspath(base), "ls-files", "--stage", "-z", "--", spec],
                   timeout=60, env=env, capture_limit=None)
    except OSError:
        return {}
    if not done.ok:
        return {}
    modes: dict[str, bool] = {}
    for record in done.stdout_bytes.split(b"\0"):
        meta, tab, name = record.partition(b"\t")
        if tab:
            modes[os.fsdecode(name)] = meta.split()[0] == b"100755"
    return modes


def _entries(root: Path, skip: tuple[str, ...], *, strict: bool, drop: Callable[[Path], bool] | None = None) -> list[Path]:
    """Every entry under ``root``, directories included, a symlink as itself and never descended into. Entries under
    a ``skip`` prefix, and those ``drop`` names, are not read. Non-strict is ``rglob``, which skips a directory it cannot
    read (and the files behind it); strict raises that error, so a digest cannot leave out what it could not read."""
    if not strict:
        return [p for p in root.rglob("*") if drop is None or not drop(p)]
    found: list[Path] = []
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as it:
            for entry in it:
                path = Path(entry.path)
                rel = path.relative_to(root).as_posix()
                if any(rel == e or rel.startswith(e + "/") for e in skip) or (drop is not None and drop(path)):
                    continue
                found.append(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return found


def tree_digest(root: Path, *, exclude: Iterable[str] = (), strict: bool = False,
                tracked: Mapping[str, bool] | None = None, git: GitTree | bool | None = False) -> str:
    """sha256 (bare hex) over every path, mode bit and byte under ``root``; a link by its target.

    A file ``root`` digests as itself (its path is ``.``), and so does a symlink ``root``
    (by its target, dangling or not; it is not walked). Entries whose path, relative to
    ``root``, equals one of ``exclude`` or sits under it are left out, so a skill's digest can
    leave its eval directory out. Output is deterministic: sorted walk, no timestamps.

    The same tree digests the same on every OS: a file's mode bit is the one git records (the index, so a
    Windows checkout with no exec bit still matches), and an untracked file takes its owner's exec bit
    from the disk. A link inside ``root`` with an absolute target is hashed by a relative target (see
    ``link_text``); one pointing outside ``root`` is hashed as written.

    ``strict``: a directory under ``root`` that cannot be read raises its OSError (the default skips it silently).
    ``tracked``: the executable bits git records, keyed by path relative to ``root`` (to its parent for a file root),
    as :func:`_tracked_modes` returns them; None reads them from git. A caller digesting many files reads them once.
    ``git``: hash what git stores, not the bytes on disk. A file digests as its blob id (:func:`file_contents`), so a
    CRLF checkout with core.autocrlf matches the LF one; what git leaves out (ignored paths, a nested ``.git``, and what a
    nested repository's own ignore rules leave out) is skipped,
    and so are directories, which git does not store. A ``GitTree`` is a work tree already read; True reads
    it from ``root``; None (or no work tree) hashes the bytes on disk and leaves nothing out.
    """
    skip = tuple(e.strip("/") for e in exclude)
    # a symlink is never looked through here: its target may be unreadable (or gone), and it digests by its text
    is_link = root.is_symlink()
    as_file = not is_link and root.is_file()
    if isinstance(git, GitTree):
        tree: GitTree | None = git
    else:
        tree = git_tree(root.parent if is_link or as_file else root) if git else None
    # sorted by the parts of the relative path, compared as code points: the same order on every OS (Path
    # ordering folds case on Windows)
    entries = ([root] if is_link or as_file
               else sorted(_entries(root, skip, strict=strict, drop=tree.skipped if tree else None),
                           key=lambda p: p.relative_to(root).parts))
    if tree is not None:
        entries = [p for p in entries if p.is_symlink() or not p.is_dir()]
    base, spec = (root.parent, root.name) if as_file else (root, ".")
    modes: Mapping[str, bool]
    if tracked is None:
        modes = _tracked_modes(base, spec) if any(not p.is_symlink() and p.is_file() for p in entries) else {}
    else:  # the caller read the index already: keyed as below, by the path relative to ``base``
        modes = tracked
    holds = file_contents([p for p in entries if not p.is_symlink() and p.is_file()], tree)
    h = hashlib.sha256()
    for path in entries:
        rel = "." if path == root else path.relative_to(root).as_posix()
        if any(rel == e or rel.startswith(e + "/") for e in skip):
            continue
        # names and link targets are bytes on POSIX and may hold undecodable bytes: encode them as the OS does
        name = os.fsencode(rel)
        if path.is_symlink():
            h.update(b"L " + name + b"\0" + os.fsencode(link_text(path, root)) + b"\0")
        elif path.is_file():
            key = path.relative_to(base).as_posix()
            if key in modes:
                executable = modes[key]
            else:
                executable = bool(os.lstat(path).st_mode & stat.S_IXUSR)
            h.update(b"F " + name + f"\0{int(executable)}\0".encode())
            h.update(holds[path])
        elif path.is_dir():
            h.update(b"D " + name + b"\0")
    return h.hexdigest()


def retry_writable(func: Any, path: str, _exc: Any) -> None:
    """``shutil.rmtree`` ``onerror`` hook: set the write bit on ``path`` and retry once.

    Windows will not delete a read-only file, and git leaves its object files
    read-only. A symlink is left alone, since chmod would follow it out of the
    tree. A retry that still fails is ignored; the caller checks what is left.
    Only the delete calls are retried: on POSIX ``rmtree`` also reports a failed
    ``os.open`` or ``os.scandir`` (an unreadable dir), which ``func(path)`` would
    call with the wrong arguments and raise ``TypeError``.
    """
    import stat

    try:
        mode = os.lstat(path).st_mode
        if not stat.S_ISLNK(mode):
            os.chmod(path, stat.S_IMODE(mode) | stat.S_IWRITE)
            if func in (os.unlink, os.remove, os.rmdir):
                func(path)
    except OSError:
        pass


# Bedrock cross-region inference profiles: the same model served from another region. They are dropped
# from any provider's name, not only Bedrock's: no non-Bedrock id is known to start with one of them.
_REGION_PROFILES = ("us-gov.", "global.", "apac.", "us.", "eu.", "jp.", "au.", "ca.")


# Leading segments that name a route (provider or gateway: ``anthropic/``, ``openrouter/``), not a model; any other
# segment (an HF org such as ``meta-llama/``) is part of the id. Holds ``llm.ANSWER_PROVIDERS`` (test-pinned) + litellm routes.
_ROUTES = frozenset({
    "openai", "openai-codex", "anthropic", "google", "gemini", "google-vertex", "openrouter", "amazon-bedrock",
    "bedrock", "converse", "vertex_ai", "azure", "azure_ai", "models", "groq", "ollama", "mistral", "deepseek",
    "together_ai", "fireworks_ai", "xai", "cohere", "huggingface", "claude-cli",
})


# Vendors whose Bedrock ids start with ``vendor.`` (``anthropic.claude-...``, ``zai.glm-4.7``, ``xai.grok-4.6``).
_VENDORS = frozenset({
    "ai21", "amazon", "anthropic", "cohere", "deepseek", "google", "luma", "meta", "minimax", "mistral",
    "moonshotai", "nvidia", "openai", "qwen", "stability", "twelvelabs", "writer", "xai", "zai",
})
_VENDOR_SLUGS = {"z-ai": "zai", "x-ai": "xai", "mistralai": "mistral"}  # OpenRouter's spelling of a vendor
_BEDROCK_ARN = re.compile(r"(?:^|/)arn:aws[a-z-]*:bedrock:[a-z0-9-]*:\d*:(?:inference-profile|foundation-model)/")


def _model_core(model: str) -> tuple[str, str | None]:
    """A model name reduced to ``(id, snapshot date or None)``. A snapshot is ``-YYYYMMDD`` or OpenAI's ``-YYYY-MM-DD``.

    The route goes (``openrouter/anthropic/``, ``bedrock/``, ``vertex_ai/``, an ARN's
    ``inference-profile/``), and so do the case, the region profile (``eu.``) and a Bedrock
    revision (``-v1:0`` or ``-1:0``). Vertex's ``@20250929`` is the snapshot ``-20250929``. The organization of an
    ``org/name`` id stays, so a re-upload under another organization is another model; a known vendor
    (``qwen/``, ``z-ai/``) goes, as its Bedrock ``vendor.`` prefix does, unless a ``huggingface/`` route precedes it.
    """
    name = (model or "").strip().lower()
    arn = _BEDROCK_ARN.search(name)  # only a Bedrock ARN's tail: ``foundation-model/X`` alone is an org/name id
    if arn:
        name = name[arn.end():]
    parts = name.split("/")
    hub = False
    while len(parts) > 1 and parts[0] in _ROUTES:
        hub = parts.pop(0) == "huggingface"
        if hub:  # a Hub id: the route goes, its organization stays, even behind another route
            break
    if not hub and len(parts) > 1 and _VENDOR_SLUGS.get(parts[0], parts[0]) in _VENDORS:
        parts.pop(0)  # OpenRouter's ``qwen/X`` is Bedrock's ``qwen.X``: the vendor, not an organization
    name = "/".join(parts)
    name = re.sub(r"-v?\d+:\d+$", "", name)
    for prefix in _REGION_PROFILES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = re.sub(r"@(?=\d{8}$)", "-", name)
    snapshot = re.search(r"-(\d{8}|\d{4}-\d{2}-\d{2})$", name)
    return (name[:snapshot.start()], snapshot.group(1).replace("-", "")) if snapshot else (name, None)


def model_identity(model: str) -> tuple[str | None, str | None]:
    """``(id, snapshot date or None)`` of a model name with its route, region profile and vendor prefix dropped;
    the id is ``None`` for a name with no id. Two names with equal identities are one model and one snapshot."""
    base, date = _model_core(model)
    if not base:
        return None, date
    # a known vendor before the first dot (``anthropic.``, ``openai.``, as in Bedrock ids) is dropped; any other
    # dotted name is part of the id (``alice.model``), and so is a dot after a digit or a dash (``gpt-4.1``)
    head, dot, rest = base.partition(".")
    ident = rest if dot and head in _VENDORS else base
    return (ident if re.search(r"[a-z0-9]", ident) else None), date  # separators alone name no model


def same_model(a: str, b: str) -> bool:
    """Whether two model names are one model.

    The same id matches, and so does a dated snapshot and its undated alias (the alias runs
    some snapshot of it), or the same id behind another route or region profile. Two dated
    ids match only on the same date, and a bare family name (``haiku``) is not a full id.

    The provider is not part of the identity: the same weights on Bedrock and on the direct
    API are still one model for the self-judge warning and for a baseline's model check.
    """
    (base_a, date_a), (base_b, date_b) = model_identity(a), model_identity(b)
    if base_a is None or base_b is None or (date_a and date_b and date_a != date_b):
        return False
    return base_a == base_b


def same_evaluator(answer_provider: str, answer_model: str, judge_provider: str, judge_model: str) -> bool:
    """Whether the answer and judge are one model, for the independence warning and its stored flag.

    The model-name rule, or the CLI alias rule when a logged-in CLI route is involved.
    """
    from dokimi import llm_cli

    return (same_model(answer_model, judge_model)
            or llm_cli.same_model(answer_provider, answer_model, judge_provider, judge_model))


def question_id(q: Dict[str, Any]) -> Any:
    """The question's ``id`` or ``question_id``; ``None`` when neither is set.

    ``0`` is an id (``questions.v1`` allows integer ids), so ``q.get("id") or ...``
    — which reads it as missing — is not a substitute.
    """
    for key in ("id", "question_id"):
        if q.get(key) not in (None, ""):
            return q[key]
    return None


class InputError(ValueError):
    """A user-supplied input that cannot be used: missing, unreadable, not UTF-8 or JSON, or the wrong shape.

    Library loaders raise it (or a subclass) with a message that names the file, and do not
    print or exit. ``cli.main`` prints ``Error: <message>`` and exits 2, the code for bad input;
    exit 1 stays the verdict of ``--strict`` gates and ``eval judge-validity``. A ``ValueError``,
    so code that already catches bad values keeps catching these.
    """


_BEDROCK_ID = re.compile(r"[a-z0-9]+\.", re.IGNORECASE)


def check_bedrock_model(provider: str, model: str) -> None:
    """Raise :class:`InputError` for a judge name Bedrock cannot run, before a run pays for answers.

    A Bedrock id starts with a region or vendor and a dot (``us.anthropic.``, ``amazon.``); a dot
    after a hyphen is a version (``claude-sonnet-4.5``), not an id. An ARN or a litellm route has a
    ``/``, and a bare name runs only when ``BEDROCK_MODEL_IDS`` maps it to a profile.
    """
    if (provider == "amazon-bedrock" and "/" not in model and not _BEDROCK_ID.match(model)
            and model not in BEDROCK_MODEL_IDS):
        raise InputError(f"Bedrock has no model '{model}': pass its inference profile id "
                         f"(us.anthropic.<name>) or an ARN; the bare names that run are "
                         f"{', '.join(BEDROCK_MODEL_IDS)}")


def read_json_input(path: str | os.PathLike[str], *, flag: str | None = None) -> Any:
    """The parsed JSON at ``path``, or :class:`InputError` naming the file and the reason.

    Covers a missing or unreadable file, bytes that are not UTF-8, text that is not JSON and
    JSON nested too deeply to parse. With ``flag`` (the option the file was given to) the
    message is that option's own, ``<flag> <path>: <reason>``.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        reason = exc.strerror or exc
        raise InputError(f"{flag} {path}: {reason}" if flag else f"cannot read {path}: {reason}") from exc
    except UnicodeDecodeError as exc:
        raise InputError(f"{flag} {path}: {exc}" if flag else f"{path} is not UTF-8 text: {exc}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:  # deep nesting is named as bad JSON, as on main
        raise InputError(f"{flag} {path}: {exc}" if flag else f"{path} is not valid JSON: {exc}") from exc
    except ValueError as exc:  # e.g. "embedded null byte" in the path: pathlib raises a bare ValueError
        raise InputError(f"{flag} {path}: {exc}" if flag else f"cannot read {path}: {exc}") from exc

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Repository sources: a working tree, or a commit in a cached clone.

A source spec is either a local path (``/src/oneDAL``), a local path pinned to
a revision (``/src/oneDAL@a492e703``), or a remote URL pinned to a revision
(``https://github.com/uxlfoundation/oneDAL@a492e703``). Remote sources are
cloned once, bare and blob-less, into a cache directory and reused; the
revision is fetched on demand. Both kinds are read through the same
:class:`RepoTree` interface, so manifest validation never needs a checkout.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol

from dokimi import process

# A revision is a SHA, tag or single-segment branch, optionally with ^ / ~N
# suffixes. No "/" (so "user@host:path" URLs are not split) and no leading "-"
# (so a revision can never be read as a git option).
_REV_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._^~-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_REMOTE_PREFIXES = ("https://", "ssh://", "file://", "git@")
_GIT_TIMEOUT_S = 1800
READ_MANY_LIMIT = 2 * 1024 * 1024  # bytes; larger files are not text guidance or code to search


class SourceError(ValueError):
    """Raised for an unusable source spec or a git operation that failed."""


@dataclass(frozen=True)
class RepoSource:
    """A parsed source spec. ``rev`` is ``None`` for an unpinned working tree."""

    location: str
    rev: str | None
    remote: bool

    @classmethod
    def parse(cls, spec: str) -> RepoSource:
        spec = spec.strip()
        if not spec or spec.startswith("-"):
            raise SourceError(f"invalid repository source: {spec!r}")
        location, rev = spec, None
        head, sep, tail = spec.rpartition("@")
        if sep and head and _REV_RE.match(tail):
            location, rev = head, tail
        remote = location.startswith(_REMOTE_PREFIXES)
        if location.startswith("http://"):
            raise SourceError("plain http:// sources are refused; use https://")
        if remote and rev is None:
            raise SourceError(f"a remote source must be pinned to a revision: {spec}@<sha>")
        if not remote and not Path(location).is_dir():
            raise SourceError(f"local source is not a directory: {location}")
        return cls(location=location, rev=rev, remote=remote)

    def __str__(self) -> str:
        return self.location if self.rev is None else f"{self.location}@{self.rev}"


class RepoTree(Protocol):
    """Read-only view of one repository snapshot. Paths are POSIX, root-relative."""

    label: str

    def files(self) -> list[str]: ...

    def is_file(self, path: str) -> bool: ...

    def is_dir(self, path: str) -> bool: ...

    def read_text(self, path: str) -> str: ...

    def resolve(self, path: str, dirs: bool = False) -> str:
        """The file ``path`` names once symlinks are followed, as in a checkout; ``path`` itself
        if it is not a link. :class:`SourceError` if it escapes the tree, dangles, loops or
        names a directory (with ``dirs``, a link to a directory gives the directory)."""
        ...

    def read_many(self, paths: list[str], max_bytes: int = ...,
                  skipped: dict[str, str] | None = None) -> dict[str, str]:
        """Text of each path, links followed. A path left out (binary, over ``max_bytes``, a link
        that dangles or escapes) is added to ``skipped`` with the reason."""
        ...


class WorkTree:
    """A directory on disk, read as it is (uncommitted changes included)."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.label = str(self.root)
        self._files: list[str] | None = None

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise SourceError(f"path escapes the repository: {path}")
        return target

    def files(self) -> list[str]:
        """Every file, or inside a git working tree the files git would show: tracked or not ignored.

        The root may be a subdirectory of the working tree; ``ls-files -C`` lists paths relative to it.
        """
        if self._files is None and _git(self.root, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true":
            listed = _git_entries(self.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                  check=False, warn=True)
            if listed:  # a link is an entry even when it dangles or names a directory, as in git
                self._files = sorted({p for p in listed
                                      if (self.root / p).is_file() or (self.root / p).is_symlink()})
        if self._files is None:
            out: list[str] = []
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames if d != ".git"]
                rel = Path(dirpath).relative_to(self.root)
                # A link to a directory is an entry, as in git, though walk lists it as a directory.
                links = [d for d in dirnames if (Path(dirpath) / d).is_symlink()]
                out.extend((rel / name).as_posix() for name in filenames + links)
            self._files = sorted(p.removeprefix("./") for p in out)
        return self._files

    def is_file(self, path: str) -> bool:
        return self._resolve(path).is_file()

    def is_dir(self, path: str) -> bool:
        return self._resolve(path).is_dir()

    def read_text(self, path: str) -> str:
        return self._resolve(path).read_text(encoding="utf-8")

    def resolve(self, path: str, dirs: bool = False) -> str:
        try:
            target = self._resolve(path)
        except (RuntimeError, OSError) as exc:  # a link loop (RuntimeError before Python 3.13)
            raise SourceError(f"too many levels of symbolic links: {path}") from exc
        if target.is_dir() and dirs:
            return target.relative_to(self.root).as_posix()
        if target.is_dir():
            raise SourceError(f"{path} links to a directory: {target.relative_to(self.root).as_posix()}")
        if not target.is_file():
            raise SourceError(f"{path} links to nothing: {target.relative_to(self.root).as_posix()}")
        return target.relative_to(self.root).as_posix()

    def read_many(self, paths: list[str], max_bytes: int = READ_MANY_LIMIT,
                  skipped: dict[str, str] | None = None) -> dict[str, str]:
        out = {}
        skipped = {} if skipped is None else skipped
        for path in paths:
            try:
                target = self.root / self.resolve(path)
                size = target.stat().st_size
                if size > max_bytes:
                    skipped[path] = _too_big(size, max_bytes)
                elif (text := _text(target.read_bytes())) is None:
                    skipped[path] = "binary (a NUL byte)"
                else:
                    out[path] = text
            except (OSError, SourceError) as exc:
                skipped[path] = str(exc)
        return out


class GitTree:
    """One commit of a git repository, read with ``git ls-tree`` / ``git show``."""

    def __init__(self, git_dir: str | Path, sha: str, label: str | None = None):
        self.git_dir = Path(git_dir)
        self.sha = sha
        self.label = label or f"{self.git_dir}@{sha[:12]}"
        self._files: list[str] | None = None
        self._dirs: set[str] | None = None
        self._symlinks: set[str] | None = None

    def files(self) -> list[str]:
        if self._files is None:
            self._files = sorted(_git_entries(self.git_dir, "ls-tree", "-r", "-z", "--name-only", self.sha, warn=True))
        return self._files

    def _dir_set(self) -> set[str]:
        if self._dirs is None:
            dirs: set[str] = set()
            for path in self.files():
                dirs.update(str(parent) for parent in PurePosixPath(path).parents)
            self._dirs = dirs
        return self._dirs

    def is_file(self, path: str) -> bool:
        return _norm(path) in set(self.files())

    def is_dir(self, path: str) -> bool:
        return _norm(path) in self._dir_set()

    def _links(self) -> set[str]:
        if self._symlinks is None:
            self._symlinks = {entry.split("\t", 1)[1] for entry in _git_entries(self.git_dir, "ls-tree", "-r", "-z", self.sha)
                              if entry.startswith("120000 ")}
        return self._symlinks

    def read_text(self, path: str) -> str:
        """The file's text; a symlink is followed, as it is in a checkout."""
        return _git(self.git_dir, "show", f"{self.sha}:{self.resolve(path)}")

    def resolve(self, path: str, dirs: bool = False) -> str:
        start = path = _norm(path)
        for _ in range(40):
            if path not in self._links():
                if path in self._dir_set() and dirs:
                    return path
                if path in self._dir_set():
                    raise SourceError(f"{start} links to a directory: {path}" if path != start
                                      else f"{path} is a directory")
                if path != start and path not in self.files():
                    raise SourceError(f"{start} links to nothing: {path}")
                return path
            text = _git(self.git_dir, "show", f"{self.sha}:{path}")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(path), text))
            if posixpath.isabs(text) or target == ".." or target.startswith("../"):
                raise SourceError(f"path escapes the repository: {path} -> {text}")
            path = target
        raise SourceError(f"too many levels of symbolic links: {start}")

    def read_many(self, paths: list[str], max_bytes: int = READ_MANY_LIMIT,
                  skipped: dict[str, str] | None = None) -> dict[str, str]:
        """Read in one ``git cat-file --batch``; in a blob-less clone the missing blobs are
        fetched first in one request, not one by one."""
        skipped = {} if skipped is None else skipped
        # No `-l`: listing sizes makes git fetch every missing blob of a partial clone, one by one.
        oid_of: dict[str, str] = {}
        for entry in _git_entries(self.git_dir, "ls-tree", "-r", "-z", self.sha):
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                oid_of[path] = parts[2]
        by_oid: dict[str, list[str]] = {}  # oid -> requested paths (identical files and links share one blob)
        for requested in dict.fromkeys(paths):
            try:
                real = self.resolve(requested)
            except SourceError as exc:
                skipped[requested] = str(exc)
                continue
            if real not in oid_of:
                skipped[requested] = f"not a file in {self.sha[:12]}: {real}"
                continue
            by_oid.setdefault(oid_of[real], []).append(requested)
        if not by_oid:
            return {}
        _prefetch(self.git_dir, self.sha, set(by_oid))
        sizes = _git_bytes(self.git_dir, ["cat-file", "--batch-check"], "".join(f"{o}\n" for o in by_oid).encode())
        blobs = {}
        for line in sizes.decode().splitlines():
            oid, _, rest = line.partition(" ")
            kind, _, size = rest.partition(" ")
            if kind == "blob" and size.isdigit() and int(size) <= max_bytes:
                blobs[oid] = by_oid[oid]
            elif oid in by_oid:
                reason = _too_big(int(size), max_bytes) if size.isdigit() else f"blob {oid} {rest or 'missing'}"
                skipped.update(dict.fromkeys(by_oid[oid], reason))
        if not blobs:
            return {}
        data = _git_bytes(self.git_dir, ["cat-file", "--batch"], "".join(f"{o}\n" for o in blobs).encode())
        out, pos = {}, 0
        while pos < len(data):
            end = data.index(b"\n", pos)
            header = data[pos:end].decode().split()
            if len(header) != 3:  # "<oid> missing"
                skipped.update(dict.fromkeys(blobs.get(header[0], []), f"blob {header[0]} missing"))
                pos = end + 1
                continue
            oid, kind, size = header
            body = data[end + 1:end + 1 + int(size)]
            pos = end + 1 + int(size) + 1
            text = _text(body) if kind == "blob" else None
            if text is not None:
                out.update(dict.fromkeys(blobs[oid], text))
            else:
                skipped.update(dict.fromkeys(blobs[oid], "binary (a NUL byte)"))
        return out


def _too_big(size: int, limit: int) -> str:
    return f"{size} bytes, over the {limit // (1024 * 1024)} MB read limit"


def _text(data: bytes) -> str | None:
    """``data`` as UTF-8 text, or ``None`` for a binary file (a NUL byte, as git decides)."""
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", "replace")


def _prefetch(git_dir: Path, sha: str, oids: set[str]) -> None:
    """Fetch the missing ``oids`` of a partial clone in one request, as git's own lazy fetch does.

    ``rev-list --missing=print`` lists what is absent without fetching it; ``cat-file``
    would fetch each absent object on its own.
    """
    if not _git(git_dir, "config", "--get", "remote.origin.promisor", check=False).strip():
        return
    # Only the "?<oid>" lines are used; a path printed beside an object may not be UTF-8.
    listed = _run_git(git_dir, ["rev-list", "--objects", "--no-walk", "--missing=print", sha], None,
                      True).stdout_bytes.decode("utf-8", "replace")
    absent = [line[1:] for line in listed.splitlines() if line.startswith("?") and line[1:] in oids]
    if absent:
        _git_bytes(git_dir, ["-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin", "--no-tags",
                             "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none",
                             "--stdin"], "".join(f"{o}\n" for o in absent).encode())


def default_cache_dir() -> Path:
    """Where remote sources are cloned: ``$DOKIMI_REPO_CACHE`` or ~/.cache."""
    env = os.environ.get("DOKIMI_REPO_CACHE")
    return Path(env) if env else Path.home() / ".cache" / "dokimi" / "repos"


def cache_path(url: str, cache_dir: Path) -> Path:
    """Stable per-URL clone directory: readable slug plus a hash of the full URL."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rstrip("/").rsplit("/", 1)[-1]) or "repo"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return cache_dir / f"{slug.removesuffix('.git')}-{digest}.git"


def open_source(source: RepoSource, cache_dir: Path | None = None) -> RepoTree:
    """Return a readable tree for ``source``, cloning or fetching as needed."""
    if not source.remote:
        if source.rev is None:
            return WorkTree(source.location)
        # ls-tree lists paths relative to -C, git show reads them from the root.
        prefix = _git(Path(source.location), "rev-parse", "--show-prefix").strip()
        if prefix:
            raise SourceError(f"{source.location} is {prefix!r} inside a repository; give its root")
        sha = resolve_rev(Path(source.location), source.rev)
        return GitTree(Path(source.location), sha, label=f"{source.location}@{sha[:12]}")
    git_dir = ensure_clone(source.location, cache_dir or default_cache_dir())
    assert source.rev is not None  # parse() refuses unpinned remotes
    sha = ensure_rev(git_dir, source.rev)
    return GitTree(git_dir, sha, label=f"{source.location}@{sha[:12]}")


def base_opener(source: RepoSource, tree: RepoTree) -> Callable[[str], RepoTree] | None:
    """Open other commits of the repository behind ``tree``, such as task bases.

    ``None`` when ``tree`` has no git history to read them from. A remote
    clone fetches a missing commit; a local repository is only read.
    """
    if isinstance(tree, GitTree):
        git_dir = tree.git_dir
    elif isinstance(tree, WorkTree) and (tree.root / ".git").exists():
        git_dir = tree.root
    else:
        return None
    shallow = _git(git_dir, "rev-parse", "--is-shallow-repository").strip() == "true"

    def open_base(rev: str) -> RepoTree:
        try:
            sha = ensure_rev(git_dir, rev) if source.remote else resolve_rev(git_dir, rev)
        except SourceError as exc:
            if not shallow:
                raise
            raise SourceError(f"{exc}; the clone is shallow, so the base may only be missing: "
                              "fetch the full history (actions/checkout: fetch-depth: 0)") from exc
        return GitTree(git_dir, sha, label=f"{source.location}@{sha[:12]}")

    return open_base


def ensure_clone(url: str, cache_dir: Path) -> Path:
    """Clone ``url`` bare and blob-less into the cache once; return the git dir."""
    git_dir = cache_path(url, cache_dir)
    if (git_dir / "HEAD").is_file():
        return git_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = git_dir.with_name(git_dir.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    _git(None, "clone", "--bare", "--filter=blob:none", "--", url, str(tmp))
    tmp.rename(git_dir)
    return git_dir


def ensure_rev(git_dir: Path, rev: str) -> str:
    """Resolve ``rev`` in the cached clone; a branch or tag is re-fetched on every call."""
    base = re.split(r"[\^~]", rev, maxsplit=1)[0]
    if _SHA_RE.match(base):
        try:
            return resolve_rev(git_dir, rev)
        except SourceError:
            pass
        try:
            _git(git_dir, "fetch", "--filter=blob:none", "origin", "--", base)
            return resolve_rev(git_dir, rev)
        except SourceError:
            pass
    _fetch_refs(git_dir)
    try:
        return resolve_rev(git_dir, rev)
    except SourceError as exc:
        short = _SHA_RE.match(base) and len(base) < 40
        hint = "; a short SHA on no branch or tag cannot be fetched, give the full SHA" if short else ""
        raise SourceError(f"revision not found in origin: {rev}{hint}") from exc


def _fetch_refs(git_dir: Path) -> None:
    _git(git_dir, "fetch", "--filter=blob:none", "origin",
         "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")


def resolve_rev(git_dir: Path, rev: str) -> str:
    """Full commit SHA for ``rev``; raises :class:`SourceError` if unknown."""
    if not _REV_RE.match(rev):
        raise SourceError(f"invalid revision: {rev!r}")
    return _git(git_dir, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()


def _norm(path: str) -> str:
    return PurePosixPath(path).as_posix().removeprefix("./").rstrip("/") or "."


def _git(git_dir: Path | None, *args: str, check: bool = True) -> str:
    raw = _run_git(git_dir, list(args), None, check).stdout_bytes
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")  # strict, as a blob read always was


def _git_entries(git_dir: Path, *args: str, check: bool = True, warn: bool = False) -> list[str]:
    """The entries of a ``-z`` listing. A file name that is not UTF-8 cannot be named in a
    report, so its entry is left out, with a warning on stderr when ``warn`` is set."""
    out = []
    for entry in _run_git(git_dir, list(args), None, check).stdout_bytes.split(b"\0"):
        try:
            out.append(entry.decode("utf-8"))
        except UnicodeDecodeError:
            if warn:
                print(f"warning: skipped a file name that is not UTF-8: {entry.decode('utf-8', 'backslashreplace')}",
                      file=sys.stderr)
    return [e for e in out if e]


def _git_bytes(git_dir: Path, args: list[str], stdin: bytes) -> bytes:
    """``git -C git_dir *args`` with binary stdin and stdout."""
    return _run_git(git_dir, args, stdin, True).stdout_bytes


def _run_git(git_dir: Path | None, args: list[str], stdin: bytes | None, check: bool) -> process.Completed:
    # core.fsmonitor=false: a repository's own config must not run a command when it is read.
    where = ["-c", "core.fsmonitor=false"] + (["-C", str(git_dir)] if git_dir is not None else [])
    try:
        proc = process.git(*where, *args, input=stdin, timeout=_GIT_TIMEOUT_S)
    except OSError as exc:
        raise SourceError(f"git {args[0]} failed: {exc}") from exc
    if proc.timed_out:
        raise SourceError(f"git {args[0]} failed: timed out after {_GIT_TIMEOUT_S} seconds")
    if proc.returncode != 0 and check:
        raise SourceError(f"git {args[0]} failed: {process.git_failure(proc)}")
    return proc

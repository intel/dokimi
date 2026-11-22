# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Provenance of a skill copy: the upstream version it came from, how far behind head that is, and whether it was edited.

The upstreams are the caller's, offline, and nothing is fetched: a git checkout (``--upstream``), whose history gives
every version of each ``SKILL.md``, or a manifest (``--upstream-manifest``), a JSON file that carries the bytes of each
version it lists. No upstream list is built in.

A copy is matched in two steps, by its bytes first, even when it does not load:

- ``exact``: its ``SKILL.md`` is byte-identical to an upstream ``SKILL.md`` at the upstream head or in its history
  (the git blob sha). The copy is not edited. When several versions have those bytes, the newest one is the match.
- ``similar``: no blob matches, and an upstream version with the same frontmatter ``name`` has a word Jaccard
  similarity of at least :data:`SIMILAR_JACCARD` to the copy's text. The copy is edited.

Otherwise the copy is ``unmatched``. Blob shas are git's own (``sha1("blob <size>\\0" + bytes)``), so a checkout, a
manifest and the copy agree with ``git hash-object``, in a sha-1 or a sha-256 repository. A copy with CRLF line
endings also matches by its LF form, as ``git hash-object`` does in a checkout with ``core.autocrlf``.

The history of a checkout is read in two git calls (``log --raw`` over every ``SKILL.md``, ``ls-tree`` at head) and one
``cat-file --batch``. ``behind`` counts the commits that touched the path after the version; the months are the head
commit date less the version's commit date. A shallow clone sees only its own history, and says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from dokimi import process
from dokimi.utils import InputError, parse_frontmatter

from .catalog_git import _git

SIMILAR_JACCARD = 0.8
MONTH_DAYS = 30.44
MANIFEST_SCHEMA = "dokimi.upstream.v1"
_SKILL_PATHSPEC = ":(glob,icase)**/SKILL.md"
_WORDS = re.compile(r"[a-z0-9]+")


def _is_zero(sha: str) -> bool:
    """The all-zero object id git prints for an absent side of a change, in a sha-1 or a sha-256 repository."""
    return set(sha) == {"0"}


def blob_sha(raw: bytes) -> str:
    """The git blob sha of ``raw``: what ``git hash-object`` prints for the same bytes."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw, usedforsecurity=False).hexdigest()


def blob_sha256(raw: bytes) -> str:
    """The blob id of ``raw`` in a sha-256 repository (``git init --object-format=sha256``), as ``ls-tree`` prints it."""
    return hashlib.sha256(b"blob %d\0" % len(raw) + raw).hexdigest()


def _exact_rank(version: "Version") -> tuple:
    """The order of versions with the same bytes: the head, then the earlier upstream on the command line, then the
    newest commit date within that upstream, then the fewest commits behind (a manifest gives no commit count)."""
    newest = (0, -version.date.timestamp()) if version.date is not None else (1, 0.0)
    behind = version.behind if version.behind is not None else 1 << 30
    return (not version.head, version.upstream.order, newest, behind)


def _tokens(text: str) -> frozenset[str]:
    """The lowercase ASCII words of ``text``. The same words the corpus study measured the 0.8 threshold on."""
    return frozenset(_WORDS.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _norm_name(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.strip().strip("\"'").strip().lower() or None


def _version_label(meta: dict) -> Optional[str]:
    """The ``version`` a skill declares: top level, or under ``metadata``. None when it declares none."""
    value = meta.get("version")
    if value is None and isinstance(meta.get("metadata"), dict):
        value = meta["metadata"].get("version")
    return None if value is None else str(value)


def _parse_date(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"upstream: {value!r} is not an ISO 8601 date") from exc
    # A date without a zone is read as UTC: the head and the versions are compared, and an aware and a naive one do not subtract.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Upstream:
    """One upstream: its name (the checkout's directory name, or the manifest's ``name``) and its head."""

    name: str
    order: int  # its place on the command line; a tie between two upstreams goes to the earlier one
    head_commit: Optional[str]
    head_date: Optional[datetime]
    history: str  # "full", "shallow" (a shallow clone: older versions are not seen), "partial" or "manifest" (as listed)
    missing: int = 0  # blobs of the history the clone does not hold: their versions are not in the index


@dataclass(frozen=True)
class Version:
    """One ``SKILL.md`` of an upstream, at one version: its bytes, and where in the upstream's history it sits."""

    upstream: Upstream
    path: str
    blob_sha: str
    name: Optional[str]
    version: Optional[str]
    text: str
    commit: Optional[str]
    date: Optional[datetime]
    head: bool
    behind: Optional[int]  # commits that touched the path after this version; None when the history does not say
    removed: Optional[bool] = None  # the path is not in the upstream head tree; None when the upstream does not say

    @property
    def months_behind(self) -> Optional[float]:
        """Months from this version's commit date to the upstream head's, or None when either date is unknown."""
        if self.head:
            return 0.0
        if self.date is None or self.upstream.head_date is None:
            return None
        days = (self.upstream.head_date - self.date).total_seconds() / 86400
        return round(max(days, 0.0) / MONTH_DAYS, 1)


@dataclass(frozen=True)
class Match:
    kind: str  # "exact" or "similar"
    version: Version
    edited: bool
    jaccard: Optional[float]  # set for "similar"


class Index:
    """Every upstream version, by blob sha and by frontmatter name."""

    def __init__(self, upstreams: Sequence[Upstream], versions: Iterable[Version]):
        self.upstreams = tuple(upstreams)
        self._by_blob: dict[str, list[Version]] = {}
        self._by_name: dict[str, list[Version]] = {}
        for version in versions:
            self._by_blob.setdefault(version.blob_sha, []).append(version)
            if version.name is not None:
                self._by_name.setdefault(version.name, []).append(version)

    def match(self, raw: bytes, name: Optional[str]) -> Optional[Match]:
        """The upstream version a copy with bytes ``raw`` and frontmatter ``name`` came from, or None."""
        # A CRLF checkout (core.autocrlf) of an LF blob is the same file to git: its LF form is looked up too.
        forms = (raw, raw.replace(b"\r\n", b"\n")) if b"\r\n" in raw else (raw,)
        exact: list[Version] = []
        for form in forms:
            exact = [v for sha in (blob_sha(form), blob_sha256(form)) for v in self._by_blob.get(sha, [])]
            if exact:
                break
        if exact:
            best = min(exact, key=_exact_rank)
            return Match("exact", best, edited=False, jaccard=None)
        key = _norm_name(name)
        candidates = self._by_name.get(key, []) if key else []
        if not candidates:
            return None
        words = _tokens(raw.decode("utf-8", errors="replace"))
        scored = [(_jaccard(words, _tokens(v.text)), v) for v in candidates]
        best_score, best = max(
            scored,
            key=lambda pair: (pair[0], pair[1].head, -pair[1].upstream.order,
                              pair[1].date.timestamp() if pair[1].date is not None else float("-inf"),
                              -(pair[1].behind if pair[1].behind is not None else 1 << 30)),
        )
        if best_score < SIMILAR_JACCARD:
            return None
        return Match("similar", best, edited=True, jaccard=round(best_score, 3))


def _unquote(path: str) -> str:
    """A path git printed with C-style quoting (``"a\\tb"``, octal bytes for non-ASCII) as the path it names."""
    if not (path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1].encode("utf-8", errors="surrogateescape")
    out = bytearray()
    i = 0
    escapes = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10, ord("v"): 11, ord("f"): 12, ord("r"): 13,
               ord('"'): 34, ord("\\"): 92}
    while i < len(body):
        if body[i] == ord("\\") and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                i += 2
                continue
            digits = body[i + 1:i + 4]
            if len(digits) == 3 and all(48 <= d <= 55 for d in digits):
                out.append(int(digits, 8))
                i += 4
                continue
        out.append(body[i])
        i += 1
    return out.decode("utf-8", errors="surrogateescape")


def _head_blobs(root: Path) -> dict[str, str]:
    """The ``SKILL.md`` path → blob sha at HEAD."""
    listing = _git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    if listing.returncode:
        raise ValueError(f"{root}: cannot list HEAD: {_git_error(listing)}")
    blobs: dict[str, str] = {}
    for field in listing.stdout_bytes.split(b"\0"):
        if not field:
            continue
        meta, _, path = field.decode("utf-8", errors="surrogateescape").partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and path.rsplit("/", 1)[-1].lower() == "skill.md":
            blobs[path] = parts[2]
    return blobs


def _git_error(done: process.Completed) -> str:
    return process.git_failure(done)


def _read_blobs(root: Path, shas: Sequence[str]) -> dict[str, bytes]:
    """The bytes of each blob sha that is present in the object store (a partial clone may lack some)."""
    unique = sorted(set(shas))
    if not unique:
        return {}
    done = _git(root, "cat-file", "--batch", input=("\n".join(unique) + "\n").encode())
    if done.returncode:
        raise ValueError(f"{root}: cannot read blobs: {_git_error(done)}")
    data, pos, out = done.stdout_bytes, 0, {}
    for sha in unique:
        end = data.index(b"\n", pos)
        header = data[pos:end].split()
        pos = end + 1
        if len(header) != 3 or header[1] != b"blob":
            continue  # missing, or not a blob: its version is left out
        size = int(header[2])
        out[sha] = data[pos:pos + size]
        pos += size + 1
    return out


def _history(root: Path) -> tuple[list[tuple[str, str, list[tuple[str, str]]]], dict[str, list[str]]]:
    """``(commit, date, [(path, blob sha)])`` for each commit that changed a ``SKILL.md``, newest first, and the
    commits that touched each path (newest first). A deletion is a touch with no blob. A merge counts as a touch
    only where it gives the path a blob it did not have (a conflict resolution)."""
    # first-parent merge diffs, --root and --no-show-signature: git config in the checkout must not hide a version
    # or run its gpg.program.
    log = _git(root, "-c", "core.quotepath=off", "log", "--raw", "--no-abbrev", "--no-renames", "--full-history",
               "--diff-merges=first-parent", "--root", "--no-show-signature", "--format=C %H %cI %P", "HEAD", "--",
               _SKILL_PATHSPEC)
    if log.returncode:
        raise ValueError(f"{root}: cannot read history: {_git_error(log)}")
    commits: list[tuple[str, str, list[tuple[str, str]]]] = []
    merges: set[str] = set()
    for line in log.stdout_bytes.decode("utf-8", errors="surrogateescape").split("\n"):  # not splitlines(): U+2028 and the like may be in a path
        if line.startswith("C "):
            _, sha, date, *parents = line.split(" ")
            commits.append((sha, date, []))
            if len(parents) > 1:
                merges.add(sha)
        elif line.startswith(":") and commits and "\t" in line:
            meta, _, path = line.partition("\t")
            new_sha = meta.split()[3]
            path = _unquote(path)
            commits[-1][2].append((path, new_sha))
    # A merge's first-parent diff repeats the edit its branch made: keep the older commit, as ``git log -- path``.
    last: dict[str, str] = {}
    for commit, _, changes in reversed(commits):
        if commit in merges:
            changes[:] = [(path, new_sha) for path, new_sha in changes if last.get(path) != new_sha]
        last.update(changes)
    touches: dict[str, list[str]] = {}
    for commit, _, changes in commits:
        for path, _ in changes:
            touches.setdefault(path, []).append(commit)
    return commits, touches


def load_checkout(root: Path, order: int) -> tuple[Upstream, list[Version]]:
    """The upstream and every ``SKILL.md`` version a git checkout holds, from its history."""
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory: --upstream takes the top level of a git checkout")
    top = _git(root, "rev-parse", "--show-toplevel")
    # git walks up from any directory to an enclosing repository: a plain folder inside one is not a checkout.
    if top.returncode or Path(top.stdout.strip()).resolve() != root.resolve():
        raise ValueError(f"{root} is not a git checkout: --upstream takes the top level of a git checkout")
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if head.returncode:
        raise ValueError(f"{root} is not a git checkout with a commit: --upstream takes a git checkout")
    head_sha = head.stdout.strip()
    head_date_text = _git(root, "log", "-1", "--no-show-signature", "--format=%cI", head_sha).stdout.strip()
    shallow = _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    heads = _head_blobs(root)
    commits, touches = _history(root)
    first_seen: dict[tuple[str, str], tuple[str, str]] = {}  # (path, blob) → (commit, date), newest occurrence
    for commit, date, changes in commits:
        for path, new_sha in changes:
            if not _is_zero(new_sha):
                first_seen.setdefault((path, new_sha), (commit, date))
    keys = set(first_seen) | {(path, sha) for path, sha in heads.items()}
    texts = _read_blobs(root, [sha for _, sha in keys])
    missing = len({sha for _, sha in keys if sha not in texts})
    upstream = Upstream(
        name=root.resolve().name, order=order, head_commit=head_sha,
        head_date=_parse_date(head_date_text or None),
        history="partial" if missing else "shallow" if shallow else "full", missing=missing,
    )
    versions: list[Version] = []
    for path, sha in sorted(keys):
        if sha not in texts:
            continue  # a blob the clone does not hold: counted in ``missing``
        at: tuple[str, str] | None = first_seen.get((path, sha))
        at_commit = at[0] if at else None
        is_head = heads.get(path) == sha
        behind = touches[path].index(at_commit) if at_commit is not None and path in touches else (0 if is_head else None)
        text = texts[sha].decode("utf-8", errors="replace")
        meta = _meta(text)
        versions.append(Version(
            upstream=upstream, path=path, blob_sha=sha, name=_norm_name(meta.get("name")),
            version=_version_label(meta), text=text, commit=at_commit,
            date=_parse_date(at[1] if at else None), head=is_head, behind=behind,
            removed=path not in heads,
        ))
    return upstream, versions


def load_manifest(path: Path, order: int) -> tuple[Upstream, list[Version]]:
    """The upstream and versions a manifest lists. Each entry is ``{"path", "content", "head"?, "commit"?, "date"?}``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: not a readable JSON manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise InputError(f"{path}: a manifest is a JSON object with \"schema\": \"{MANIFEST_SCHEMA}\"")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InputError(f"{path}: the manifest needs a \"name\"")
    head_raw = data.get("head")
    if head_raw is not None and not isinstance(head_raw, dict):
        raise InputError(f"{path}: \"head\" must be an object {{commit, date}}")
    head: dict[str, Any] = head_raw or {}
    upstream = Upstream(
        name=name.strip(), order=order, head_commit=_optional_str(head, "commit", path, "head"),
        head_date=_parse_date(_optional_str(head, "date", path, "head")), history="manifest",
    )
    entries = data.get("skills")
    if not isinstance(entries, list):
        raise InputError(f"{path}: \"skills\" must be a list of {{path, content}} entries")
    versions = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("content"), str):
            raise InputError(f"{path}: each entry needs a string \"path\" and \"content\"")
        text = entry["content"]
        meta = _meta(text)
        where = f"skills entry {entry['path']!r}"
        if entry.get("head") is not None and not isinstance(entry["head"], bool):
            raise InputError(f"{path}: \"head\" in {where} must be true or false")
        versions.append(Version(
            upstream=upstream, path=entry["path"], blob_sha=blob_sha(text.encode("utf-8")),
            name=_norm_name(meta.get("name")), version=_version_label(meta), text=text,
            commit=_optional_str(entry, "commit", path, where),
            date=_parse_date(_optional_str(entry, "date", path, where)),
            head=entry.get("head") is True, behind=None,
        ))
    return upstream, versions


def _optional_str(obj: dict, key: str, path: Path, where: str) -> Optional[str]:
    """``obj[key]`` when it is a string, None when it is absent or null; anything else is refused."""
    value = obj.get(key)
    if value is not None and not isinstance(value, str):
        raise InputError(f"{path}: \"{key}\" in {where} must be a string or null, not {value!r}")
    return value


def frontmatter_name(raw: bytes) -> Optional[str]:
    """The frontmatter ``name`` of a skill copy, or None when it has none (no fallback to the directory name)."""
    return _norm_name(_meta(raw.decode("utf-8", errors="replace")).get("name"))


def _meta(text: str) -> dict:
    """The frontmatter of an upstream version. A broken block reads as none: the version then has no name."""
    meta, _ = parse_frontmatter(text)
    return meta if isinstance(meta, dict) else {}


def build(checkouts: Sequence[str | Path] = (), manifests: Sequence[str | Path] = ()) -> Index:
    """The index over the given checkouts, then manifests (the order is the tie-break). Names must be distinct."""
    upstreams: list[Upstream] = []
    versions: list[Version] = []
    sources = [(load_checkout, Path(p)) for p in checkouts] + [(load_manifest, Path(p)) for p in manifests]
    for order, (loader, path) in enumerate(sources):
        try:
            upstream, found = loader(path, order)
        except InputError:
            raise
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        if any(u.name == upstream.name for u in upstreams):
            raise InputError(f"two upstreams are named {upstream.name!r}; rename one checkout directory")
        upstreams.append(upstream)
        versions.extend(found)
    return Index(upstreams, versions)


def to_row(match: Optional[Match]) -> dict:
    """The report fields of one skill's match; ``match`` None gives ``match: null`` and nothing else."""
    if match is None:
        return {"match": None, "upstream": None, "path": None, "commit": None, "date": None, "version": None,
                "at_head": None, "behind_commits": None, "behind_months": None, "edited": None, "jaccard": None,
                "removed_at_head": None}
    v = match.version
    return {
        "match": match.kind,
        "upstream": v.upstream.name,
        "path": v.path,
        "commit": v.commit[:12] if v.commit else None,
        "date": v.date.date().isoformat() if v.date else None,
        "version": v.version,
        "at_head": v.head,
        "behind_commits": 0 if v.head else v.behind,
        "behind_months": v.months_behind,
        "edited": match.edited,
        "jaccard": match.jaccard,
        "removed_at_head": v.removed,
    }


def upstream_rows(index: Index) -> list[dict]:
    """One report row per upstream: its name, head, and how its history was read."""
    return [
        {"name": u.name, "head_commit": u.head_commit[:12] if u.head_commit else None,
         "head_date": u.head_date.date().isoformat() if u.head_date else None, "history": u.history,
         "missing_blobs": u.missing}
        for u in index.upstreams
    ]


def summary(rows: Sequence[dict]) -> dict:
    """Counts of the matches, the share of matched copies at head, and the median months behind among the others."""
    matched = [r for r in rows if r["match"] is not None]
    behind = [r["behind_months"] for r in matched if not r["at_head"] and r["behind_months"] is not None]
    return {
        "skills": len(rows),
        "exact": sum(r["match"] == "exact" for r in rows),
        "similar": sum(r["match"] == "similar" for r in rows),
        "unmatched": sum(r["match"] is None for r in rows),
        "edited": sum(r["edited"] is True for r in rows),
        "at_head": sum(r["at_head"] is True for r in rows),
        "median_months_behind": round(statistics.median(behind), 1) if behind else None,
    }

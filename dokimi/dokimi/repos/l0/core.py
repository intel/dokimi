# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The generic L0 claim checks: links, paths, identifiers and pasted shell (contract §8).

Every check reads one snapshot through :class:`~dokimi.repos.source.RepoTree`,
so a scan needs no checkout. Status per claim: ``ok`` (``ok_basename`` when only
a bare file name matched), ``unresolved`` (needs a human look, not a failure)
or ``fail``.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import shlex
import textwrap
from urllib.parse import unquote
from dataclasses import dataclass, field
from typing import Any

from ..source import RepoTree, SourceError
from . import workflows
from .gitignore import GitIgnore, Rule
from .markdown import PYTHON_LANGS, SHELL_LANGS, STARLARK_LANGS, Fence, split

# Files whose words are prose, not code: an identifier found only there is not in the code.
# Build inputs that happen to end in .txt are code.
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
ROOT_FILES = frozenset({
    "Makefile", "makefile", "GNUmakefile", "CMakeLists.txt", "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel",
    "BUILD", "BUILD.bazel", ".bazelrc", ".bazelversion", "pyproject.toml", "setup.py", "setup.cfg",
    "conftest.py", "pytest.ini", "tox.ini", "requirements.txt", ".pre-commit-config.yaml", ".clang-format",
    ".editorconfig", ".gitignore", "Dockerfile",
})
# Names of naming conventions: guidance mentions them, code never needs to.
CONVENTION_WORDS = frozenset({"CamelCase", "camelCase", "PascalCase", "snake_case", "kebab_case",
                              "SCREAMING_SNAKE_CASE", "UPPER_CASE", "UPPER_SNAKE_CASE", "lower_case"})
_EXT = (r"h|hh|hpp|hxx|c|cc|cpp|cxx|cu|i|py|pyx|pxd|pyi|bzl|bazel|md|rst|txt|yml|yaml|json|toml|cfg|ini|"
        r"mk|cmake|in|sh|bat|ps1|ver|js|ts|go|rs|java")
_PATHLIKE = re.compile(rf"^(?:\.{{0,2}}/)?[\w.@\-*<>{{}}]+(?:/[\w.@\-*<>{{}},]*)+$|^[\w\-*.]+\.(?:{_EXT})$")
_IDENT = re.compile(r"^[A-Za-z_]\w*(::\w+)+$|^[A-Z][A-Z0-9_]{3,}$|^[a-z]+_[a-z_]+$|^[A-Z]\w*[a-z]\w*[A-Z]\w*$")
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_]\w*\+?=")
_PROMPT = re.compile(r"^\s*(?:\$|>|#\s*\$)\s+")
_PLACEHOLDER = re.compile(r"^(?:<[^>]+>|\{[^}]+\}|\$\w+|\$\{[^}]+\}|\.\.\.)$")
# `actions/checkout@v6`, `owner/repo/sub@<sha>…`: a GitHub Action reference, not a path in the tree; without the ref,
# an Action a workflow step uses is `ok`. A ref ending in a file extension (`assets/icon@2x.png`) is a file name.
_ACTION_REF = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@(?![\w.-]*\.[A-Za-z][A-Za-z0-9]{0,4}$)"
                         r"[\w.-]+?(?:\.\.\.|…)?$")
_WRAPPED = re.compile(r"^`([^`]+)`(\s+#.*)?$")  # a whole command in backticks, maybe with a comment
# `2>&1`, `> log`, `>>log`, `&>/dev/null`, `< in`: a redirection and its target, not arguments.
_REDIRECT = re.compile(r"(?:(?<=\s)|^)[0-9]?(?:&>>?|>>?|<)(?:&[0-9]+|\s*[^\s|;&<>]+)")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)")
# `<name>`, `<path/to/src>` or `<source dir>`: a placeholder to fill in, not redirections. Its first word is a name
# (`<in.txt scripts/x.py 2>` is an input redirection: a dot ends the first word), and it does not end in a space, in a
# file descriptor (`2>`) or before `&` (`2>&1`); a heredoc `<<EOF>` is not a placeholder either.
_FILL_IN = re.compile(r"(?<!<)<[A-Za-z_][\w/-]*(?:[ \t][^<>\n;&|]*[^\s<>;&|])?(?<!\s\d)>(?!&)")
_CONTROL = frozenset({"&&", "||", "|", ";", "&", "|&", ";;", "(", ")"})
_CONTROL_RUN = re.compile(r"&&|\|\||\|&|;;|[;&|()]")
# A ref that is one path segment: a default branch, a commit or a version tag. Any other ref may hold slashes.
_PLAIN_REF = re.compile(r"^(?:main|master|trunk|HEAD|[0-9a-f]{7,40}|v?\d+(?:\.\d+)+[\w.+-]*)$")


def dynamic(word: str) -> bool:
    """A shell word holding an expansion (``$X``, ``${X}``, ``$(cmd)``, a backtick substitution): its value is not in the text."""
    return "$" in word or "`" in word


def glob_regex(pat: str) -> re.Pattern[str]:
    """``**/`` spans zero or more directories; ``*`` may also cross ``/``, since guidance writes ``tests/*.py`` loosely."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat[i] == "*":
            out.append(".*")
            i += 2 if pat.startswith("**", i) else 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_FILLS = ("x", "dokimi-probe")  # what a `*` becomes in a probe of the files a glob names


def _fill(pat: str, fill: str) -> str:
    """``pat`` with its wildcards filled in: ``**/`` as nothing, ``*`` as ``fill``, ``?`` as its first letter."""
    return re.sub(r"\?", fill[0], re.sub(r"\*+", fill, pat.replace("**/", "")))


def is_doc(path: str) -> bool:
    name = posixpath.basename(path)
    if name == "CMakeLists.txt" or name.startswith("requirements"):
        return False
    return path.lower().endswith(DOC_SUFFIXES)


@dataclass
class Claim:
    file: str
    line: int
    kind: str
    claim: str
    status: str
    why: str
    ctx: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "kind": self.kind, "claim": self.claim,
                "status": self.status, "why": self.why, "ctx": self.ctx[:200]}


@dataclass
class Scan:
    """One L0 scan of one snapshot: the facts it reads lazily, and the claims it found."""

    tree: RepoTree
    adapters: frozenset[str]
    repo_url: str | None = None
    guidance: tuple[str, ...] = ()
    claims: list[Claim] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.files = self.tree.files()
        self.file_set = set(self.files)
        self.dir_set = {"."} | {posixpath.dirname(f) or "." for f in self.files}
        for d in list(self.dir_set):
            while d not in (".", ""):
                d = posixpath.dirname(d) or "."
                self.dir_set.add(d)
        self.by_name: dict[str, list[str]] = {}  # basename -> files and directories
        for x in sorted(self.file_set | (self.dir_set - {"."})):
            self.by_name.setdefault(posixpath.basename(x), []).append(x)
        self._texts: dict[str, str] = {}
        self.unread: dict[str, str] = {}  # path -> why it was not read (binary, too big, dangling link)
        self._words: set[str] | None = None
        self._cache: dict[str, Any] = {}  # adapter facts
        self.submodules = self._submodules()
        url = (self.repo_url or "").removesuffix(".git").rstrip("/")
        self._own_link = re.compile(re.escape(url) + r"/(blob|tree)/(.+)") if url else None
        self._cd: list[str] | None = None  # directories a shell fence has `cd`-ed to; [] when not in the tree
        self._cd_stack: list[list[str] | None] = []  # saved at `(`

    # ---------- reading ----------
    def preload(self, paths: list[str], skipped: dict[str, str] | None = None) -> None:
        # a path already found unreadable (by detect, say) is not read again, but the caller still learns why
        if skipped is not None:
            skipped.update({p: self.unread[p] for p in paths if p in self.unread})
        missing = [p for p in paths if p not in self._texts and p not in self.unread and p in self.file_set]
        if missing:
            skip = {} if skipped is None else skipped
            self._texts.update(self.tree.read_many(missing, skipped=skip))
            self.unread.update(skip)

    def text(self, path: str) -> str:
        if path not in self._texts and path in self.file_set:
            self.preload([path])
        return self._texts.get(path, "")

    def unread_reason(self, path: str) -> str | None:
        """Why ``path`` is in the tree but its text was not read; None when it was read (or never was)."""
        self.text(path)
        return self.unread.get(path)

    def words(self) -> set[str]:
        """Every word in tracked non-doc text files, for identifier claims."""
        if self._words is None:
            corpus = [f for f in self.files if not is_doc(f) and f not in self.guidance]
            self.preload(corpus)
            words: set[str] = set()
            for f in corpus:
                words.update(re.findall(r"\w+", self._texts.get(f, "")))
            self._words = words
        return self._words

    def cached(self, key: str, make: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def exists(self, path: str) -> bool:
        """``path`` is in the tree, following a link to a directory on the way, as a checkout does."""
        p = posixpath.normpath(path.lstrip("/")) if path.strip("/") else "."
        return p in self.file_set or p in self.dir_set or self._through_link(p) is not None

    def _through_link(self, path: str, depth: int = 0) -> str | None:
        """``path`` with a leading link to a directory replaced by that directory, if the result exists."""
        parts = path.split("/")
        for i in range(1, len(parts)):
            head = "/".join(parts[:i])
            if head in self.dir_set:
                continue
            if head not in self.file_set or depth > 8:
                return None
            try:
                target = self.tree.resolve(head, dirs=True)
            except SourceError:
                return None
            if target not in self.dir_set:  # a plain file, or a link to one
                return None
            p = posixpath.normpath(posixpath.join(target, *parts[i:]))
            if p in self.file_set or p in self.dir_set:
                return p
            return self._through_link(p, depth + 1)
        return None

    def is_file(self, path: str) -> bool:
        """A file, not a directory (a link to a directory counts as the directory)."""
        p = posixpath.normpath(path)
        return p in self.file_set and p not in self.dir_set and self._dir_link(p) is None

    def _dir_link(self, path: str) -> str | None:
        try:
            target = self.tree.resolve(path, dirs=True)
        except SourceError:
            return None
        return target if target in self.dir_set else None

    def submodule_of(self, cands: list[str]) -> str | None:
        return next((m for m in self.submodules for c in cands if c == m or c.startswith(m + "/")), None)

    def _submodules(self) -> list[str]:
        if ".gitmodules" not in self.file_set:
            return []
        return [m.strip().strip("/") for m in re.findall(r"^\s*path\s*=\s*(.+)$", self.text(".gitmodules"), re.M)]

    def own_link(self, url: str) -> tuple[str, bool, int] | None:
        """``(/path, guessed, splits)`` for a blob or tree link to this repository, else ``None``.

        The URL holds ``<ref>/<path>`` and a ref may contain slashes (``release/1.0``), so the
        split is tried after each segment, shortest ref first, and the first remainder that is in
        the tree wins. ``refs/heads/`` and ``refs/tags/`` belong to the ref. A default branch, a
        commit or a version tag is one segment and is not tried any other way. When no split is in
        the tree the path is the one after the first segment (``splits`` were tried), and ``guessed`` is true for a tree
        link whose ref may be longer (``tree/release/1.0`` names a branch, not a directory).
        A link to a ref alone is not a path claim.
        """
        m = self._own_link.match(url) if self._own_link else None
        if not m:
            return None
        raw = unquote(re.split(r"[?#]", m.group(2))[0])
        segs = [s for s in raw.split("/") if s]
        first = 3 if segs[:2] in (["refs", "heads"], ["refs", "tags"]) else 1
        if len(segs) <= first:
            return None
        plain = _PLAIN_REF.match(segs[first - 1]) is not None
        rests = ["/".join(segs[i:]) for i in range(first, first + 1 if plain else len(segs))]
        tail = "/" if raw.endswith("/") else ""
        for r in rests:
            if self.exists(r):
                return "/" + r + tail, False, len(rests)
        return "/" + rests[0] + tail, m.group(1) == "tree" and not plain, len(rests)

    def own_path(self, url: str) -> str | None:
        """``/path`` for a blob or tree link to this repository, else ``None``."""
        link = self.own_link(url)
        return link[0] if link else None

    def add(self, file: str, line: int, kind: str, claim: str, status: str, why: str, ctx: str = "") -> None:
        self.claims.append(Claim(file, line, kind, claim, status, why, ctx.strip()))

    # ---------- generic checks ----------
    def resolve_path(self, file: str, raw: str) -> tuple[str, str] | None:
        """(status, why) for a path claim made in ``file``, or ``None`` if it is not a path claim.

        Tried at the file's directory and each ancestor. Not found there, a path is ``fail``
        when its parent directory exists at one of those bases (a rename or a deleted file),
        ``ok_suffix`` when it ends some path in the tree (a root file naming paths inside a
        subproject), and ``unresolved`` otherwise (a build output, say).
        """
        p = raw.strip().rstrip(".,:;")
        if not p or p.startswith(("http", "~", "-", "%")) or dynamic(p) or "=" in p or " " in p:
            return None
        bases = ancestors(file)
        head, _, rest = p.partition("/")
        if rest and _PLACEHOLDER.match(head):
            return self._under_placeholder(head, rest.rstrip("/"))
        if p.startswith("/"):  # root-relative, as GitHub renders it
            bases = [""]
        elif self._cd is not None:  # in a shell fence after `cd`: relative to that directory only
            if not self._cd:
                return "unresolved", "after a `cd` to a directory this tree does not hold (or not a literal one)"
            bases = self._cd
        rel = re.sub(r"(^|/)\.\.\.(?=/|$)", r"\1**", p.lstrip("/"))  # `a/.../b` elides directories
        cands = list(dict.fromkeys(posixpath.normpath(posixpath.join(b, rel)) for b in bases))
        if _ACTION_REF.match(p) and not any(self.exists(c) for c in cands):
            return None
        if any(c.startswith("..") for c in cands) and all(c.startswith("..") for c in cands):
            return "fail", f"resolves to {cands[0]}, outside the repository"
        held = self._submodule_or_action(p, cands)
        if held:
            return held
        if any(c in rel for c in "*<>{"):
            return self._glob(bases, rel)
        for c in cands:
            if self.exists(c):
                if rel.endswith("/") and self.is_file(c):
                    return "fail", f"{c} is a file, not a directory"
                return "ok", c
        target = posixpath.normpath(rel).strip("/")
        if "/" not in target:
            hits = self.by_name.get(target, [])
            if hits:
                return "ok_basename", f"bare name; {len(hits)} match(es), e.g. {hits[0]}"
            return "unresolved", "tried " + ", ".join(cands)
        if target.startswith(".."):
            return "unresolved", "tried " + ", ".join(cands)
        for c in cands:
            parent = posixpath.dirname(c)
            if parent and parent in self.dir_set:
                return self._missing([c], rel.endswith("/"), f"{parent}/ exists but {c} does not" + self._unread_note(p, cands))
        hits = [x for x in self.by_name.get(posixpath.basename(target), []) if x.endswith("/" + target)]
        if hits:
            return "ok_suffix", f"below a directory the text does not name; {len(hits)} match(es), e.g. {hits[0]}"
        return self._missing(cands, rel.endswith("/"), "tried " + ", ".join(cands) + self._unread_note(p, cands), "unresolved")

    def _submodule_or_action(self, name: str, cands: list[str]) -> tuple[str, str] | None:
        """``unresolved`` for a path into a submodule, else ``ok`` for a used Action no candidate holds, else ``None``.

        The Action is one a workflow step uses as ``uses: <name>@<ref>``, named here without the ref; the
        submodule verdict comes first, as the snapshot does not hold what the name may be there. A name
        whose directory the tree holds at the root (``tools/gone`` beside ``tools/``) is a path, stale or not.
        """
        sub = self.submodule_of(cands)
        if sub:
            return "unresolved", f"inside the submodule {sub}, which this snapshot does not hold"
        used = workflows.action_users(self).get(name) if self._reads_as_action(name, cands) else None
        return ("ok", f"used by {used}") if used else None

    def _reads_as_action(self, name: str, cands: list[str]) -> bool:
        """``name`` has an Action's shape, no candidate holds it and its directory is not one at the root."""
        return (workflows.ACTION.match(name) is not None and not any(self.exists(c) for c in cands)
                and posixpath.dirname(name) not in self.dir_set)

    def _unread_note(self, name: str, cands: list[str]) -> str:
        """``; <file> was not read (<why>)`` for each workflow or local action the Action reading of ``name`` skipped."""
        if not self._reads_as_action(name, cands):
            return ""
        return "".join(f"; {f} was not read ({why})" for f, why in workflows.unread_action_files(self).items())

    def _glob(self, bases: list[str], rel: str) -> tuple[str, str]:
        """A glob is judged like the plain path it generalises (see :meth:`resolve_path`)."""
        template = re.search(r"<[^>]+>|\{[^},]*\}", rel) is not None  # `<run>`: a name to fill in, maybe no instance yet
        pat = posixpath.normpath(re.sub(r"\{[^}]*\}", "*", re.sub(r"<[^>]+>", "*", rel)).rstrip("/"))
        every = self._every()
        for b in bases:
            full = posixpath.normpath(posixpath.join(b, pat))
            rx = glob_regex(full)
            hit = next((x for x in every if rx.match(x)), None)
            if hit:
                return "ok", f"glob {full} -> e.g. {hit}"
        parent = posixpath.dirname(pat)
        if parent and not template and not any(c in parent for c in "*?["):  # a literal parent directory
            for b in bases:
                d = posixpath.normpath(posixpath.join(b, parent))
                if d in self.dir_set:
                    probes = self._probes(posixpath.normpath(posixpath.join(b, pat)))
                    return self._missing(probes, rel.endswith("/"),
                                         f"{d}/ exists but nothing in it matches {posixpath.basename(pat)}")
        if parent:
            rx = glob_regex("*/" + pat)
            hit = next((x for x in every if rx.match(x)), None)
            if hit:
                return "ok_suffix", f"below a directory the text does not name; e.g. {hit}"
        probes = [p for b in bases for p in self._probes(posixpath.normpath(posixpath.join(b, pat)))]
        return self._missing(probes, rel.endswith("/"),
                             f"glob {pat} matches nothing in the tree; a build may create it", "unresolved")

    def _probes(self, full: str) -> list[str]:
        """Files the glob ``full`` names, to ask the ``.gitignore`` about: a probe of a few names, not every one.

        Its wildcards filled in, and each name an ignoring rule matches that the glob matches too
        (``gen.h`` for ``g*.h``); two fillings, so one re-included (``!x.cxx``) leaves the other.
        Rule names count only for a last segment a literal starts or ends: ``*`` or ``*_*`` matches
        the name of an unrelated rule (``_skbuild``), which is no evidence the glob is ignored.
        """
        folder, name = posixpath.split(full)
        rx = glob_regex(full)
        named = name[:1] not in "*?[" or name[-1:] not in "*?]"
        paths = [posixpath.join(_fill(folder, fill), n) for fill in _FILLS
                 for n in (_fill(name, fill), *(self.gitignore().names(fill) if named else ()))]
        return [p for p in dict.fromkeys(paths) if rx.match(p)]

    def _missing(self, paths: list[str], is_dir: bool, why: str, status: str = "fail") -> tuple[str, str]:
        """``status`` for a path the tree lacks, unless a ``.gitignore`` ignores it: a build output.

        ``paths`` are the files a glob names, or the path at each base it is tried from; any of them
        being ignored is enough.
        """
        rule = next(filter(None, (self._ignored_by(p, is_dir) for p in paths)), None)
        if rule is None:
            return status, why
        return "unresolved", (f"{why}; {rule.source}:{rule.line} (`{rule.pattern}`) ignores it, "
                              "so it is likely generated rather than tracked")

    def _ignored_by(self, path: str, is_dir: bool) -> Rule | None:
        """The rule that ignores ``path``, read as a directory too: without a trailing slash it may
        still name one (``dist`` under ``dist/``).

        A path no ``!`` line names (``!/setup.py``, ``!*.py``; ``!*/`` names every directory, not this
        one) also counts when every file probed directly inside it would be (``include/*``, or ``out/*``
        with ``!*/``): a plain name and each name a ``!`` line re-includes, so a whitelist (``*``,
        ``!*/``, ``!*.py``) does not hide a renamed directory.
        """
        ig = self.gitignore()
        readings = (True,) if is_dir else (False, True)
        rule = next(filter(None, (ig.ignored_by(path, d) for d in readings)), None)
        if rule is not None:
            return rule
        if any(r and r.name.strip("*") for r in (ig.reincluded_by(path, d) for d in readings)):
            return None
        inside = [ig.ignored_by(posixpath.join(path, n)) for n in ("file", *ig.names("x", negated=True))]
        return inside[0] if all(inside) else None

    def gitignore(self) -> GitIgnore:
        def load() -> GitIgnore:
            self.preload([f for f in self.files if posixpath.basename(f) == ".gitignore"])
            return GitIgnore(self.files, self.text)
        return self.cached("gitignore", load)

    def _every(self) -> list[str]:
        return self.cached("every", lambda: sorted(self.file_set | self.dir_set))

    def _under_placeholder(self, head: str, rest: str) -> tuple[str, str]:
        """A path such as ``<out>/reach.json`` or ``{run_dir}/grade.json``: its root is not in the tree."""
        pat = re.sub(r"\{[^}]*\}", "*", re.sub(r"<[^>]+>", "*", rest))
        rx = re.compile(fnmatch.translate(pat))
        sub = re.compile(fnmatch.translate("*/" + pat))
        hit = next((x for x in self._every() if rx.match(x) or sub.match(x)), None)
        if hit:
            return "ok", f"{head} stands for a directory; {pat} matches {hit}"
        return "unresolved", f"{head} stands for a directory outside the tree; nothing to check {pat} against"

    def check_link(self, file: str, line: int, target: str, ctx: str) -> None:
        tgt = target.strip("<>")
        if tgt.startswith(("#", "mailto:")):
            return
        own = self.own_link(tgt)
        guessed, splits = False, 1
        if own:
            tgt, guessed, splits = own
        elif re.match(r"^[a-z][a-z0-9+.-]*:", tgt):
            return  # another site; not checked offline
        tgt = unquote(tgt.split("#")[0].split("?")[0])
        if not tgt:
            return
        path = tgt.lstrip("/") if tgt.startswith("/") else posixpath.join(posixpath.dirname(file), tgt)
        path = posixpath.normpath(path)
        sub = self.submodule_of([path])
        if path.startswith(".."):
            self.add(file, line, "link", target, "fail", f"resolves to {path}, outside the repository", ctx)
        elif sub:
            self.add(file, line, "link", target, "unresolved",
                     f"inside the submodule {sub}, which this snapshot does not hold", ctx)
        elif tgt.endswith("/") and self.is_file(path):
            self.add(file, line, "link", target, "fail", f"{path} is a file, not a directory", ctx)
        else:
            found = self.exists(path)
            if not found and guessed:
                self.add(file, line, "link", target, "unresolved",
                         f"{path} is not in the tree; the ref may hold slashes, so this may name a branch", ctx)
            else:
                more = f"; none of the {splits} splits of ref and path is in the tree" if splits > 1 and not found else ""
                self.add(file, line, "link", target, "ok" if found else "fail", f"resolves to {path}{more}", ctx)

    def check_span(self, file: str, line: int, span: str, ctx: str) -> None:
        from . import adapters

        toks = tokens(span)
        if toks and toks[0] in adapters.COMMANDS:
            self.check_command(file, line, span, ctx)
            self._cd, self._cd_stack = None, []
            return
        if "//" in span and adapters.label_claims(self, file, line, span):
            return
        if _PATHLIKE.match(span) or span in ROOT_FILES:
            r = self.resolve_path(file, span)
            if r:
                self.add(file, line, "path", span, r[0], r[1], ctx)
        elif _IDENT.match(span) and span not in CONVENTION_WORDS:
            tok = span.split("::")[-1]
            if tok in self.words():
                self.add(file, line, "identifier", span, "ok", f"`{tok}` found in tracked non-doc files", ctx)
            elif _ALLCAPS.match(span):  # often an environment variable read by a tool, not by this code
                self.add(file, line, "identifier", span, "unresolved",
                         f"`{tok}` not in tracked non-doc files; it may be read outside this tree", ctx)
            else:
                self.add(file, line, "identifier", span, "fail", f"`{tok}` not found in tracked non-doc files", ctx)

    def check_command(self, file: str, line: int, text: str, ctx: str) -> None:
        from . import adapters

        items = shell_items(text)
        ops = [x if isinstance(x, str) else "" for x in items]  # the operator at each position, if it is one
        for i, toks in enumerate(items):
            if isinstance(toks, str):
                self._scope(toks)
                continue
            if toks[0] in ("cd", "pushd", "popd"):
                self._chdir(file, toks, ops[i - 1] if i else "", ops[i + 1] if i + 1 < len(ops) else "")
                continue
            if toks[0] in ("python", "python3") and len(toks) > 1 and toks[1].endswith(".py"):
                r = self.resolve_path(file, toks[1])
                if r:
                    self.add(file, line, "script_path", toks[1], r[0], r[1], text)
            entry = adapters.COMMANDS.get(toks[0])
            if entry is not None and entry[0] in self.adapters:
                entry[1](self, file, line, toks, text)

    @property
    def cd_dirs(self) -> list[str] | None:
        """Where a literal ``cd`` in this fence or span moved to (``""`` is the root); ``[]`` after a ``cd`` that
        cannot be followed, ``None`` when there was no ``cd``."""
        return None if self._cd is None else list(self._cd)

    def _scope(self, control: str) -> None:
        """A `(` saves the directory a subshell starts in, a `)` restores it."""
        if control == "(":
            self._cd_stack.append(self._cd)
        elif control == ")" and self._cd_stack:
            self._cd = self._cd_stack.pop()

    def _chdir(self, file: str, toks: list[str], before: str, after: str) -> None:
        """Follow a literal `cd dir` that really changes the directory; anything else leaves it unknown (``[]``).

        Unknown: `pushd`/`popd`, no argument, `-`, `~`, `$VAR`, an absolute path, a directory this tree
        lacks (a build directory), and a `cd` after `||` or `|` or before `|` or `&`, where it may not run
        or runs in a subshell.
        """
        args = [a for a in toks[1:] if a not in ("-L", "-P", "-e", "-@", "--")]
        target = args[0] if toks[0] == "cd" and len(args) == 1 else ""
        if (not target or target.startswith(("/", "~", "-")) or re.search(r"[$`*?]", target)
                or before in ("||", "|", "|&", "&") or after in ("|", "|&", "&")):
            self._cd = []
            return
        bases = ancestors(file) if self._cd is None else self._cd
        dirs = dict.fromkeys(posixpath.normpath(posixpath.join(b, target)) for b in bases)
        self._cd = ["" if d == "." else d for d in dirs
                    if not d.startswith("..") and self.exists(d) and not self.is_file(d)]

    def check_fence(self, file: str, fence: Fence) -> None:
        from . import adapters

        if fence.lang in STARLARK_LANGS or fence.lang in PYTHON_LANGS:
            code = textwrap.dedent("\n".join(text for _, text in fence.lines)).split("\n")
            fence = Fence(fence.line, fence.lang, [(n, t) for (n, _), t in zip(fence.lines, code, strict=True)])  # in a list item
            if "bazel" in self.adapters and (fence.lang in STARLARK_LANGS or adapters.reads_as_build(self, fence)):
                adapters.check_starlark(self, file, fence)
            return
        if fence.lang not in SHELL_LANGS:
            return
        for n, raw in logical_lines(fence.lines):
            s = raw.strip()
            wrapped = _WRAPPED.match(s)
            if wrapped:
                self.add(file, n, "shell_fence", s, "fail",
                         "command wrapped in backticks inside a code fence: pasted, it runs as command substitution",
                         raw)
                s = wrapped.group(1)
            s = _PROMPT.sub("", s)
            s = re.sub(r"\s+#.*$", "", s)
            if s and not s.startswith("#"):
                self.check_command(file, n, s, raw)
        self._cd, self._cd_stack = None, []

    def check_file(self, file: str) -> None:
        from . import versions

        pieces = split(self.text(file))
        for n, target, ctx in pieces.links:
            self.check_link(file, n, target, ctx)
        for n, span, ctx in pieces.spans:
            self.check_span(file, n, span, ctx)
        for fence in pieces.fences:
            self.check_fence(file, fence)
        versions.check(self, file, pieces)


def ancestors(file: str) -> list[str]:
    """The root, then the directory of ``file`` and each directory above it: where a relative path in it is tried."""
    bases = [""]
    d = posixpath.dirname(file)
    while d:
        bases.append(d)
        d = posixpath.dirname(d)
    return bases


def _heredoc_tag(text: str) -> str:
    """The delimiter of a heredoc opened on this line, or "". A here-string (<<<), a shift in ``((...))`` and
    a ``<<`` inside quotes open nothing."""
    i, quote = 0, ""
    while i < len(text):
        c = text[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif text.startswith(("$((", "(("), i):
            end = text.find("))", i)
            i = len(text) if end < 0 else end + 1
        elif text.startswith("<<<", i):
            i += 2
        elif text.startswith("<<", i):
            m = _HEREDOC.match(text, i)
            return m.group(2) if m else ""
        i += 1
    return ""


def logical_lines(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Physical fence lines -> shell lines: backslash continuations joined and heredoc bodies dropped.

    A joined line keeps the number of its first line. A comment line ends a continuation, as in a shell.
    """
    out: list[tuple[int, str]] = []
    first, joined = 0, ""
    heredoc = ""
    for n, raw in lines:
        if heredoc:
            heredoc = "" if raw.strip() == heredoc else heredoc
            continue
        code = "" if raw.lstrip().startswith("#") else re.sub(r"\s+#.*$", "", raw)
        if code.rstrip().endswith("\\") and raw.rstrip().endswith("\\"):  # not a backslash then a comment
            first = first if joined else n
            joined += code.rstrip()[:-1] + " "
            continue
        text = raw if not joined else joined + code
        out.append((first if joined else n, text))
        first, joined = 0, ""
        heredoc = _heredoc_tag(text)
    if joined:
        out.append((first, joined))
    return out


def _substitutions(text: str) -> str:
    """``$(...)``, ``${...}`` and backtick substitutions -> ``$_``, so each stays inside its word."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "$" and text.startswith(("$(", "${"), i):
            close = ")" if text[i + 1] == "(" else "}"
            depth, j = 0, i + 1
            while j < len(text):
                depth += text[j] == text[i + 1]
                depth -= text[j] == close
                j += 1
                if depth == 0:
                    break
            if depth == 0:
                out.append("$_")
                i = j
                continue
        elif c == "`" and text.find("`", i + 1) > 0:
            out.append("$_")
            i = text.find("`", i + 1) + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def tokens(text: str) -> list[str]:
    """Shell words of the first simple command in ``text`` (see :func:`commands`)."""
    found = commands(text)
    return found[0] if found else []


def commands(text: str) -> list[list[str]]:
    """The simple commands of a shell line, split at ``&&``, ``|``, ``;`` and the like."""
    return [x for x in shell_items(text) if isinstance(x, list)]


def shell_items(text: str) -> list[list[str] | str]:
    """The simple commands of a shell line, with the operators between them as strings.

    Redirections and their targets are dropped, and so are leading ``VAR=value``
    assignments and ``sudo`` / ``env`` / ``time``. A ``<placeholder>`` becomes ``$_``, like an
    expansion: the word it is in is not literal text.
    """
    text = _REDIRECT.sub(" ", _FILL_IN.sub("$_", _substitutions(text)))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        words = text.split()
    out: list[list[str] | str] = []
    current: list[str] = []
    for w in words:
        if w == "\\":  # a continuation that was not joined
            continue
        # `);` or `&&(` is one word to the lexer: cut a run of operators into the operators
        for piece in _CONTROL_RUN.findall(w) if set(w) <= set("&|;()") else [w]:
            if piece in _CONTROL:
                out.extend([current, piece])
                current = []
            else:
                current.append(piece)
    out.append(current)
    result: list[list[str] | str] = []
    for cmd in out:
        if isinstance(cmd, list):
            while cmd and (_ENV_ASSIGN.match(cmd[0]) or cmd[0] in ("sudo", "env", "time")):
                cmd = cmd[1:]
            if not cmd:
                continue
        result.append(cmd)
    return result

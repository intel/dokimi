# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""L1 reach probe (guidance_reach.v1): which guidance files a harness actually loads.

The probe never trusts a harness's documented loading rules. It builds a
workspace that holds the repository's guidance files at their real paths, puts
a head token and a tail token in each one, and asks the agent to list every
token it can see:

- ``cwd=.``: started at the repository root, tools disabled (``.`` cannot be a
  guidance directory, so the id never collides with a ``cwd=<dir>`` scenario)
- ``cwd=<dir>``: started in each directory that holds guidance, tools disabled
- ``read=<dir>``: started at the root, told to read one file in ``<dir>`` with
  the read tool and then list tokens (finds lazy, subtree-triggered loading)
- ``bash=<dir>``: as ``read=<dir>``, but the file is printed with ``cat`` through
  the Bash tool (whether a shell read triggers that loading); only when asked for,
  and only for a profile with a ``bash`` tool mode
- ``shadow=.`` and ``shadow=<dir>`` (only when asked for): as ``cwd=.`` and
  ``read=<dir>``, with the profile's ``guidance.shadow_file`` written, seeded, at
  the root or in ``<dir>`` for that scenario only, to show what it turns off in a
  tree that holds none

A file is ``loaded`` when both tokens are reported, ``truncated`` when only
one is, ``fetched`` when it was reported only after the agent read the file
itself, and ``not_loaded`` otherwise. ``error`` means the scenario gave no usable
answer: it did not start, timed out, exited non-zero, or returned an error result
or no answer.

A symlink is one file, as in a checkout: ``CLAUDE.md -> AGENTS.md`` gives one
record for ``AGENTS.md`` with ``CLAUDE.md`` as an alias, recreated as a link in
the workspace (a copy of the seeded target where the OS refuses a link). A link
that escapes the tree, dangles or names a directory is listed as ``unresolved``
and not probed.

The workspace is a skeleton: guidance files, one probe file per guidance
directory, and a one-commit git repository. Loading depends on paths and
names, not on the rest of the tree, and a skeleton keeps a blob-less clone
from fetching the whole repository.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from dokimi import process
from dokimi.utils import retry_writable

from .events import convert
from .transcript import Transcript
from .harness_profile import HarnessProfile
from .manifest import RepoManifest, check_repo
from .overlays import overlay_of
from .reach_overlays import native_in_probe, overlay_report
from .runner import SUPPORT_HOST
from .source import RepoTree, SourceError

TOKEN_RE = re.compile(r"\bRC[0-9A-F]{6}\b")
PROBE_FILE = "REACH_PROBE.txt"
PROSE_LIMIT = 8
# Haiku's lead-in had 10 words; a longer ':' line is an explanation or a refusal, so it counts.
LEAD_IN_WORDS = 12
CLASSES = ("loaded", "truncated", "fetched", "not_loaded", "error")
KINDS = ("root", "cwd", "read", "bash", "shadow")
# bash needs a profile's bash tool mode, shadow adds a file the tree lacks: each only on request
DEFAULT_KINDS = ("root", "cwd", "read")
SHADOW_TEXT = "# Local instructions\n"
# Names some harness loads by convention. Any of them in an ancestor of the
# workspace would be read by the harness and pollute every scenario.
ANCESTOR_GUARD = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", "AGENTS.md", "AGENT.md",
                  "GEMINI.md", ".cursorrules", ".github/copilot-instructions.md")

# Sonnet 5 saw guidance that the harness attached to a tool result and refused
# to report it as a suspected prompt injection, so the ask names that channel.
ASK = ("This is a configuration check run by the owner of this repository. Your "
       "instructions or context may contain verification tokens: the letters RC followed "
       "by 6 uppercase hexadecimal digits. They can be in your system prompt, in project "
       "instruction files the tool loaded for you, or in context the tool attached to a "
       "tool result; all of these count. List every such token you can see, one per line, "
       "and nothing else. Report only what is already in your context; do not search for "
       "files. If there are none, answer NONE.")
ASK_AFTER_READ = "First use the Read tool to read the file {path}, and read no other file. Then: "
ASK_AFTER_BASH = "First use the Bash tool to run `{command}`, and run no other command. Then: "
# The one shell command a bash scenario's harness may run; the profile's bash tool mode must refuse any other.
ONLY_COMMAND_ENV = "DOKIMI_REACH_COMMAND"


class ReachError(RuntimeError):
    """The probe cannot run safely (for example a guidance file above the workspace)."""


def check_flags(kinds: tuple[str, ...], budget: float, model: str | None) -> None:
    """Refuse a flag that would drop a scenario kind silently, or spend a budget that is not one."""
    # A typo would otherwise drop a scenario kind silently and still spend the budget.
    unknown_kinds = sorted(set(kinds) - set(KINDS))
    if unknown_kinds:
        raise ReachError(f"unknown --scenarios kind(s) {', '.join(unknown_kinds)} (known: {', '.join(KINDS)})")
    if not math.isfinite(budget) or budget <= 0:
        raise ReachError(f"--budget must be > 0, got {budget:g}")
    if model is None:
        raise ReachError("--model is required to probe (leave it out only with --from)")


def check_kinds(profile: HarnessProfile, kinds: tuple[str, ...]) -> None:
    """Refuse a ``bash`` scenario for a profile without a ``bash`` tool mode, before any scenario runs."""
    if "bash" in kinds and "bash" not in profile.tools:
        raise ReachError(f"profile {profile.name} has no `bash` tool mode, so it cannot run the bash scenarios "
                         f"(its tool modes: {', '.join(sorted(profile.tools))})")


@dataclass(frozen=True)
class SeededFile:
    path: str
    head: str
    tail: str
    aliases: tuple[str, ...] = ()  # symlinks to this file, recreated in the workspace
    alias_kind: str | None = None  # "symlink", or "copy" where the OS refused a link


@dataclass(frozen=True)
class Scenario:
    id: str
    cwd: str  # relative to the workspace root; "" is the root
    tools: str
    read: str | None = None  # file the agent is told to read first (with ``cat`` in the bash tool mode)
    shadow: str | None = None  # the shadow file written for this scenario only

    @property
    def command(self) -> str | None:
        """The shell command a ``bash`` scenario names, the only one its harness may run."""
        return f"cat {shell_word(self.read)}" if self.read and self.tools == "bash" else None

    @property
    def prompt(self) -> str:
        if not self.read:
            return ASK
        if self.command:
            return ASK_AFTER_BASH.format(command=self.command) + ASK
        return ASK_AFTER_READ.format(path=self.read) + ASK


def shell_word(path: str) -> str:
    """``path`` as one operand of a POSIX command: quoted (a space, ``$`` or ``;`` in a directory name), and with
    ``./`` before a leading ``-``, which would make it an option."""
    return shlex.quote(f"./{path}" if path.startswith("-") else path)


@dataclass(frozen=True)
class Shadow:
    """The profile's ``guidance.shadow_file``, and the tree's paths, which a shadow file never replaces."""

    file: str
    taken: frozenset[str]  # casefolded, as a case-insensitive disk would collide them
    tree: RepoTree | None = None  # asked for a directory at the path: the file list has no empty one

    def free(self, path: str) -> bool:
        """No tree path at ``path``, under it (a directory) or above it (a file where ``path`` needs a directory)."""
        key = path.casefold()
        above = {p.as_posix() for p in PurePosixPath(key).parents} - {"."}
        if key in self.taken or above & self.taken or any(t.startswith(key + "/") for t in self.taken):
            return False
        try:
            return self.tree is None or not self.tree.is_dir(path)
        # A link git ignores, so not in files(), that leads out of the tree or loops (RuntimeError before
        # Python 3.13, as WorkTree.resolve maps it): not free.
        except (SourceError, RuntimeError, OSError):
            return False


def check_shadow(profile: HarnessProfile, kinds: tuple[str, ...]) -> None:
    """Refuse the shadow kind for a profile that names no shadow file, rather than run nothing for it."""
    if "shadow" in kinds and not profile.shadow_file:
        raise ReachError(f"--scenarios shadow needs guidance.shadow_file in the harness profile; {profile.name} "
                         "sets none")


def shadow_for(profile: HarnessProfile, tree: RepoTree, kinds: tuple[str, ...]) -> Shadow | None:
    """What the shadow scenarios need from the profile and the tree; None when the kind is not selected."""
    check_shadow(profile, kinds)
    name = profile.shadow_file
    if "shadow" not in kinds or not name:
        return None
    if not _safe_path(name):
        raise ReachError(f"{profile.path}: guidance.shadow_file {name!r} is not a path inside a directory")
    return Shadow(name, frozenset(p.casefold() for p in tree.files()), tree)


def token(salt: str, path: str, position: str) -> str:
    digest = hashlib.sha256(f"{salt}\0{path}\0{position}".encode("utf-8", "surrogateescape")).hexdigest()
    return "RC" + digest[:6].upper()


def probe_files(tree: RepoTree, manifest: RepoManifest, profile: HarnessProfile) -> list[str]:
    """Guidance by the manifest's globs, plus files named like the harness's own guidance."""
    if profile.native_prompt:
        raise ReachError(f"{profile.name} runs its own prompt (native_prompt), so it cannot be asked to report "
                         "what it loaded; probe the same CLI's exec profile instead")
    declared = set(check_repo(tree, manifest).guidance_files)
    names = set(profile.guidance_names)
    native = {p for p in tree.files() if PurePosixPath(p).name in names}
    # Every overlay, even where the globs miss it: whether a foreign one loads is the question (repos/overlays.py)
    overlays = {p for p in tree.files() if overlay_of(p) is not None}
    return sorted(p for p in declared | native | overlays if not manifest.in_eval_dir(p))


def check_files(files: list[str], profile: HarnessProfile) -> None:
    """Refuse a tree with no guidance file to probe (``files``: probe_files), before any scenario is selected."""
    if not files:
        raise ReachError("no guidance files to probe: the manifest's globs and the "
                         f"{profile.name} guidance names match nothing")


def scenarios(files: list[str], kinds: tuple[str, ...] = DEFAULT_KINDS,
              only: tuple[str, ...] = (), shadow: Shadow | None = None) -> list[Scenario]:
    dirs = sorted({str(PurePosixPath(f).parent) for f in files} - {"."})
    out: list[Scenario] = []
    if "root" in kinds:
        out.append(Scenario("cwd=.", "", "none"))
    if "cwd" in kinds:
        out.extend(Scenario(f"cwd={d}", d, "none") for d in dirs)
    for kind in ("read", "bash"):  # started at the root, told to read the probe file of each directory
        if kind in kinds:
            out.extend(Scenario(f"{kind}={d}", "", kind, read=f"{d}/{PROBE_FILE}") for d in dirs)
    if "shadow" in kinds:
        out.extend(_shadow_scenarios(files, shadow))
    return [s for s in out if s.id in only] if only else out


def _shadow_scenarios(files: list[str], shadow: Shadow | None) -> list[Scenario]:
    """``shadow=.``, then ``shadow=<dir>`` for each directory with a guidance file not named ``CLAUDE*`` (an
    AGENTS.md-class file). A directory whose shadow path the tree already holds gets none: it would be overwritten."""
    if shadow is None:
        raise ReachError("the shadow scenarios need the profile's guidance.shadow_file (see shadow_for)")
    dirs = sorted({str(PurePosixPath(f).parent) for f in files if not PurePosixPath(f).name.startswith("CLAUDE")}
                  - {"."})
    out = [Scenario("shadow=.", "", "none", shadow=shadow.file)] if shadow.free(shadow.file) else []
    return out + [Scenario(f"shadow={d}", "", "read", read=f"{d}/{PROBE_FILE}", shadow=f"{d}/{shadow.file}")
                  for d in dirs if shadow.free(f"{d}/{shadow.file}")]


def check_ancestors(workspace: Path, names: tuple[str, ...] = ANCESTOR_GUARD) -> None:
    found = [str(parent / name) for parent in workspace.resolve().parents for name in names
             if (parent / name).exists()]
    if found:
        raise ReachError("guidance files above the workspace would leak into every scenario: "
                         + ", ".join(found) + " (use --work-dir outside that tree)")


def resolve_links(tree: RepoTree, files: list[str]) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """Group ``files`` by the file each names once links are followed (``{target: [links]}``),
    and list the links that cannot be followed inside the tree, with the reason."""
    groups: dict[str, list[str]] = {}
    unresolved = []
    for path in files:
        try:
            target = tree.resolve(path)
        except SourceError as exc:
            unresolved.append({"path": path, "reason": str(exc)})
            continue
        if not _safe_path(target):
            unresolved.append({"path": path, "reason": f"{path} links to an unsafe path: {target}"})
            continue
        links = groups.setdefault(target, [])
        if target != path:
            links.append(path)
    return dict(sorted(groups.items())), unresolved


def followable(tree: RepoTree, files: list[str]) -> tuple[list[str], dict[str, list[str]], list[dict[str, str]]]:
    """``files`` without the links resolve_links cannot follow, then its groups and unresolved links. A run plans its
    scenarios from that list, so --dry-run does too: an unresolved link's directory gets no scenario. Refuses
    ``files`` whose every path is such a link: nothing is left to probe, in a run or a dry run alike."""
    groups, unresolved = resolve_links(tree, files)
    if files and not groups:
        raise ReachError("no guidance file to probe: each is a link that cannot be followed: "
                         + "; ".join(u["reason"] for u in unresolved))
    dropped = {u["path"] for u in unresolved}
    return [p for p in files if p not in dropped], groups, unresolved


def build_workspace(tree: RepoTree, files: list[str], workspace: Path, salt: str,
                    aliases: dict[str, list[str]] | None = None,
                    runner: process.Runner = process.run) -> list[SeededFile]:
    """Write the seeded guidance files, the links to them and the probe files, then commit them."""
    aliases = aliases or {}
    unsafe = [p for p in files + [a for f in files for a in aliases.get(f, [])] if not _safe_path(p)]
    if unsafe:
        raise ReachError("tree paths that would be written outside the workspace or into its "
                         ".git: " + ", ".join(map(repr, unsafe)))
    seeded = []
    for path in files:
        head, tail = token(salt, path, "head"), token(salt, path, "tail")
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = tree.read_text(path)
        except (UnicodeDecodeError, SourceError) as exc:
            raise ReachError(f"cannot read guidance file {path}: {exc}") from exc
        target.write_text(seed_text(text, path, head, tail), encoding="utf-8")
        links = tuple(aliases.get(path, ()))
        kind = _write_aliases(workspace, path, links) if links else None
        seeded.append(SeededFile(path, head, tail, links, kind))
    for directory in {str(PurePosixPath(f).parent) for s in seeded for f in (s.path, *s.aliases)}:
        (workspace / directory / PROBE_FILE).write_text("probe\n", encoding="utf-8")
    env = process.Env.only((), {"GIT_AUTHOR_NAME": "reach", "GIT_AUTHOR_EMAIL": "reach@invalid",
                                "GIT_COMMITTER_NAME": "reach", "GIT_COMMITTER_EMAIL": "reach@invalid",
                                "PATH": "/usr/bin:/bin:/usr/local/bin"})
    git = shutil.which("git") or "git"  # looked up on the caller's PATH, not the minimal one above
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--no-gpg-sign", "-m", "snapshot"]):
        try:
            proc = runner([git, "-C", str(workspace), *args], env=env, timeout=process.GIT_TIMEOUT_SEC)
        except OSError as exc:
            raise ReachError(f"git {args[0]} in the probe workspace failed: {exc}") from exc
        if not proc.ok:
            why = "timed out" if proc.timed_out else f"exit {proc.returncode}"
            raise ReachError(f"git {args[0]} in the probe workspace failed: {why} {proc.stderr.strip()}")
    return seeded


def _write_aliases(workspace: Path, path: str, links: tuple[str, ...]) -> str:
    """Recreate each link to ``path``. Where the OS refuses a symlink (Windows without the
    privilege), write a copy of the seeded file, so the alias still carries its tokens."""
    kind = "symlink"
    for link in links:
        where = workspace / link
        where.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(os.path.relpath(workspace / path, where.parent), where)
        except OSError:
            shutil.copyfile(workspace / path, where)
            kind = "copy"
    return kind


def _safe_path(path: str) -> bool:
    """A git tree can hold names Windows reads as a drive (``C:``) or a separator (``..\\x``).
    Trailing dots and spaces are stripped because Windows drops them (``.git.`` is ``.git``)."""
    return not ("\\" in path or ":" in path
                or any(part.rstrip(". ").casefold() in ("", ".git") for part in path.split("/")))


def distinct_salt(tree: RepoTree, files: list[str], salt: str, attempts: int = 50,
                  shadows: tuple[str, ...] = ()) -> str:
    """``salt``, or ``salt-N`` for the first N that gives every file, and every shadow file, its own two tokens.

    Tokens are 24 bits, so two files can share one, and a guidance file can already
    contain a token-shaped word; either would mark a file as seen that was not.
    """
    present: set[str] = set()
    for path in files:
        try:
            present |= set(TOKEN_RE.findall(tree.read_text(path)))
        except (UnicodeDecodeError, SourceError) as exc:
            raise ReachError(f"cannot read guidance file {path}: {exc}") from exc
    for n in range(attempts):
        candidate = salt if n == 0 else f"{salt}-{n}"
        tokens = [token(candidate, p, pos) for p in (*files, *shadows) for pos in ("head", "tail")]
        if len(set(tokens)) == len(tokens) and not present & set(tokens):
            return candidate
    raise ReachError(f"no salt in {attempts} tries gives every guidance file distinct tokens")


def seed_text(text: str, path: str, head: str, tail: str) -> str:
    """Head token after any YAML front matter (so `applyTo:` stays valid), tail token at the end."""
    head_line = f"Verification token (head) for {path}: {head}\n\n"
    tail_line = f"\n\nVerification token (tail) for {path}: {tail}\n"
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text.removeprefix(bom)
    body = text if text.endswith("\n") or not text else text + "\n"
    if body.startswith("---\n"):
        end = body.find("\n---\n", 3)
        if end != -1:
            cut = end + len("\n---\n")
            return bom + body[:cut] + head_line + body[cut:] + tail_line
    return bom + head_line + body + tail_line


def _prose(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z]{2,}", TOKEN_RE.sub(" ", text))
    return [w for w in words if w != "NONE"]


def prose_words(answer: str) -> int:
    """Words in the answer besides tokens and NONE, not counting a first non-empty line
    of at most LEAD_IN_WORDS words that ends in ':' when a later line holds a token (a
    lead-in such as "Tokens found:"). A long tail usually means the model explained or
    refused instead of listing, so its not_loaded cells are suspect."""
    lines = [line for line in answer.splitlines() if line.strip()]
    if (lines and lines[0].rstrip().endswith(":") and len(_prose(lines[0])) <= LEAD_IN_WORDS
            and any(TOKEN_RE.search(line) for line in lines[1:])):
        answer = "\n".join(lines[1:])
    return len(_prose(answer))


def classify(seeded: SeededFile, transcript: Transcript | None) -> dict[str, Any]:
    if transcript is None:
        return {"head": False, "tail": False, "read_by_tool": False, "class": "error"}
    reported = set(TOKEN_RE.findall(transcript.answer))
    head, tail = seeded.head in reported, seeded.tail in reported
    reads = transcript.reads()
    read_by_tool = any(p in reads for p in (seeded.path, *seeded.aliases))
    if not (head or tail):
        cls = "not_loaded"
    elif read_by_tool:
        cls = "fetched"
    else:
        cls = "loaded" if head and tail else "truncated"
    return {"head": head, "tail": tail, "read_by_tool": read_by_tool, "class": cls}


def _shadow_seed(scenario: Scenario, salt: str) -> SeededFile | None:
    path = scenario.shadow
    return SeededFile(path, token(salt, path, "head"), token(salt, path, "tail")) if path else None


@contextmanager
def _shadowed(workspace: Path, item: SeededFile | None) -> Iterator[None]:
    """The shadow file, seeded, in the workspace while its scenario runs and never after (an error included).
    It stays out of the workspace's commit."""
    if item is None:
        yield
        return
    _check_shadow_path(workspace, item.path)
    dirs: list[Path] = []  # the directories this call made, outermost first
    written: list[Path] = []  # the file, once this call has begun to write it
    try:
        try:
            _write_shadow(workspace, item, dirs, written)
        except OSError as exc:
            raise ReachError(f"cannot write the shadow file {item.path} in the probe workspace: {exc}") from exc
        yield
    finally:
        for path in written:
            path.unlink(missing_ok=True)
        if dirs and dirs[0].exists():
            shutil.rmtree(dirs[0], onerror=retry_writable)


def _check_shadow_paths(workspace: Path, plan: list[Scenario]) -> None:
    """Check every planned shadow path once the workspace is built, before any scenario runs: the workspace is the same
    in every scenario, so a refusal found late would only come after scenarios that ran and were billed."""
    for scenario in plan:
        if scenario.shadow:
            _check_shadow_path(workspace, scenario.shadow)


def _check_shadow_path(workspace: Path, path: str) -> None:
    """Refuse a shadow path the workspace already holds, or one under a workspace entry that is not a directory (a
    probe file, a seeded file, a link). scenarios() skips a tree path; this guards the probe's own files."""
    parts = PurePosixPath(path).parts
    for n in range(1, len(parts)):
        above = workspace.joinpath(*parts[:n])
        if not os.path.lexists(above):
            break
        if above.is_symlink() or not above.is_dir():
            raise ReachError(f"the shadow file {path} needs {'/'.join(parts[:n])}, a file of the probe workspace, "
                             "as a directory")
    if os.path.lexists(workspace / path):
        raise ReachError(f"the shadow file {path} would replace a file of the probe workspace")


def _write_shadow(workspace: Path, item: SeededFile, dirs: list[Path], written: list[Path]) -> None:
    """Make the shadow file's missing directories, each added to ``dirs`` once made, then the file."""
    target = workspace / item.path
    for parent in reversed(PurePosixPath(item.path).parents[:-1]):  # outermost first; [-1] is "."
        if not (workspace / parent).is_dir():
            (workspace / parent).mkdir()
            dirs.append(workspace / parent)
    written.append(target)  # before the write: one that fails part way can leave the file
    target.write_text(seed_text(SHADOW_TEXT, item.path, item.head, item.tail), encoding="utf-8")


def _score_row(row: dict[str, Any], transcript: Transcript | None, known: set[str],
               shadow: SeededFile | None) -> None:
    """The row's reported, unknown and prose from the answer, and the result of its shadow file, which is kept on
    the row: it is not a tree file, so it gets no records and no matrix row."""
    if transcript is not None:
        own = {shadow.head, shadow.tail} if shadow else set()
        reported = sorted(set(TOKEN_RE.findall(transcript.answer)))
        row.update(reported=reported, unknown=[t for t in reported if t not in known | own],
                   prose=prose_words(transcript.answer))
    if shadow is not None:
        row["shadow"] = {"path": shadow.path, "tokens": [shadow.head, shadow.tail], **classify(shadow, transcript)}


def run_reach(
    *,
    tree: RepoTree,
    manifest: RepoManifest,
    profile: HarnessProfile,
    model: str,
    out_dir: Path,
    bin_path: str | None = None,
    work_dir: Path | None = None,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    only: tuple[str, ...] = (),
    salt: str | None = None,
    max_turns: int = 4,
    budget_usd: float = 0.10,
    timeout_s: int = 300,
    keep: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run every scenario once and return the guidance_reach.v1 payload (unsaved).

    With ``dry_run``, stop before the first harness call (the version command) and return ``{"files", "runs"}``,
    the files and the planned runs: the dry run makes every check a run makes up to there, the built workspace
    included, so it refuses what the run would refuse."""
    check_kinds(profile, kinds)
    files = probe_files(tree, manifest, profile)
    check_files(files, profile)
    probed, groups, unresolved = followable(tree, files)
    shadows = shadow_for(profile, tree, kinds)
    plan = scenarios(probed, kinds, only, shadows)
    salt = salt or hashlib.sha256(f"{tree.label}\0{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:12]
    base = _make_base(work_dir)
    workspace = base / "ws"
    try:
        salt, seeded = _prepare(tree, profile, groups, plan, workspace, salt)
        if dry_run:
            return {"files": files, "runs": planned(probed, profile, model, kinds, only, bin_path, max_turns,
                                                    budget_usd, shadows)}
        version_env = profile.env(base / "cfg-version")
        version = profile.version(version_env, bin_path)
        trace_dir = out_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        for old in trace_dir.iterdir():  # traces/ is the record of this run only
            if re.match(r"^\d{2,}-.*\.(stdout|events\.jsonl)$", old.name) and old.is_file():
                old.unlink()
        known = {s.head for s in seeded} | {s.tail for s in seeded}
        scenario_rows, records = [], []
        for n, scenario in enumerate(plan):
            shadow = _shadow_seed(scenario, salt)
            with _shadowed(workspace, shadow):
                transcript, row = _run_one(profile, scenario, model, workspace, base / f"cfg-{n}",
                                           bin_path, max_turns, budget_usd, timeout_s)
            slug = f"{n:02d}-" + re.sub(r"[^A-Za-z0-9._-]+", "_", scenario.id)
            (trace_dir / f"{slug}.stdout").write_text(row.pop("_stdout"), encoding="utf-8")
            if transcript is not None:
                (trace_dir / f"{slug}.events.jsonl").write_text(transcript.events_jsonl(), encoding="utf-8")
                version = transcript.harness_version or version
            _score_row(row, transcript, known, shadow)
            scenario_rows.append(row)
            for item in seeded:
                records.append({"file": item.path, "scenario": scenario.id, "harness": profile.name,
                                "harness_version": version, "model": model, **classify(item, transcript)})
    finally:
        if dry_run or not keep:  # a dry run names no workspace, so it keeps none
            shutil.rmtree(base, onerror=retry_writable)

    by_class = {c: sum(1 for r in records if r["class"] == c) for c in CLASSES}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": {"id": manifest.id, "tree": tree.label, "sha": getattr(tree, "sha", None),
                 "manifest": manifest.path},
        "harness": {"name": profile.name, "version": version, "profile": profile.path,
                    "declared": profile.guidance},
        "model": model,
        "salt": salt,
        "ask": ASK,
        "workspace": str(workspace) if keep else None,
        "files": [{"path": s.path, "head": s.head, "tail": s.tail,
                   **({"aliases": list(s.aliases), "alias_kind": s.alias_kind} if s.aliases else {})}
                  for s in seeded],
        "unresolved": unresolved,
        "scenarios": scenario_rows,
        "records": records,
        "overlays": overlay_report(records, profile.overlay, {s.path: s.aliases for s in seeded},
                                   native_in_probe(seeded, profile.guidance_names)),
        "summary": {
            "cost_usd": round(sum(r.get("cost_usd") or 0 for r in scenario_rows), 6),
            "by_class": by_class,
            "matrix": _matrix(records),
        },
    }


def _make_base(work_dir: Path | None) -> Path:
    """The run's own directory, made under ``work_dir`` (the system's temporary directory without one) and resolved: a
    harness reports physical paths (macOS /var is /private/var). One that is missing, not a directory or not writable
    is a ReachError naming it."""
    try:
        return Path(tempfile.mkdtemp(prefix="reach-", dir=str(work_dir) if work_dir else None)).resolve()
    except OSError as exc:
        where = f"--work-dir {work_dir}" if work_dir else tempfile.gettempdir()
        raise ReachError(f"cannot make the probe workspace in {where}: {exc.strerror or exc}") from exc


def _prepare(tree: RepoTree, profile: HarnessProfile, groups: dict[str, list[str]], plan: list[Scenario],
             workspace: Path, salt: str) -> tuple[str, list[SeededFile]]:
    """Build the workspace and make the checks a run makes before its first harness call: distinct tokens, no
    guidance file above the workspace, a tree that can be seeded, free shadow paths. Returns the salt and the seeded.
    A write the workspace refuses (a full disk) is a ReachError naming the workspace."""
    salt = distinct_salt(tree, list(groups), salt, shadows=tuple(s.shadow for s in plan if s.shadow))  # the tree only
    try:
        workspace.mkdir()
        check_ancestors(workspace, _guarded(profile, plan))
        seeded = build_workspace(tree, list(groups), workspace, salt, groups)
    except OSError as exc:
        raise ReachError(f"cannot build the probe workspace {workspace}: {exc}") from exc
    _check_shadow_paths(workspace, plan)
    return salt, seeded


def _guarded(profile: HarnessProfile, plan: list[Scenario]) -> tuple[str, ...]:
    """The paths check_ancestors looks for: ANCESTOR_GUARD, the profile's guidance names and, when a shadow scenario is
    planned, its shadow file as given (a relative path checked by shadow_for), which the harness loads too."""
    shadow = (profile.shadow_file,) if profile.shadow_file and any(s.shadow for s in plan) else ()
    return tuple(dict.fromkeys(ANCESTOR_GUARD + profile.guidance_names + shadow))


def _run_one(profile: HarnessProfile, scenario: Scenario, model: str, workspace: Path,
             config_dir: Path, bin_path: str | None, max_turns: int, budget_usd: float,
             timeout_s: int) -> tuple[Transcript | None, dict[str, Any]]:
    config_dir.mkdir()
    # As repo run does: a profile's config files (a Codex provider, a Claude Code hook) apply to the probe too.
    profile.write_config(config_dir, {"config_dir": config_dir.as_posix(), "support_dir": SUPPORT_HOST.as_posix()})
    argv = profile.argv(prompt=scenario.prompt, model=model, tools=scenario.tools,
                        max_turns=max_turns, budget_usd=budget_usd, bin_path=bin_path)
    row: dict[str, Any] = {"id": scenario.id, "cwd": scenario.cwd, "tools": scenario.tools,
                           "read": scenario.read, "returncode": None, "error": None,
                           "cost_usd": None, "num_turns": None, "reported": [], "unknown": [], "prose": None}
    try:
        proc = process.run(argv, cwd=workspace / scenario.cwd, env=scenario_env(profile, scenario, config_dir),
                           timeout=timeout_s, capture_limit=None)  # the trace is parsed whole
    except OSError as exc:
        row.update(error=f"{type(exc).__name__}: {exc}", _stdout="")
        return None, row
    if proc.timed_out:  # a killed run is no verdict; its partial trace is kept for reading
        row.update(error=f"TimeoutExpired: {argv[0]} timed out after {timeout_s} seconds",
                   _stdout=proc.stdout)
        return None, row
    transcript = convert(profile.trace, proc.stdout, str(workspace), scenario.cwd)
    row.update(returncode=proc.returncode, cost_usd=transcript.cost_usd,
               num_turns=transcript.num_turns, _stdout=proc.stdout)
    if proc.returncode != 0 or transcript.is_error or not transcript.answer:
        row["error"] = (f"exit {proc.returncode}" + (" (error result)" if transcript.is_error else "")
                        + (" (no answer)" if not transcript.answer else "")
                        + (f": {proc.stderr.strip()[:300]}" if proc.stderr.strip() else ""))
        return None, row
    return transcript, row


def scenario_env(profile: HarnessProfile, scenario: Scenario, config_dir: Path) -> process.Env:
    """The profile's child environment, plus, in a bash scenario, the one command its shell may run."""
    env = profile.env(config_dir)
    if not scenario.command:
        return env
    return process.Env(env.allow, {**env.set, ONLY_COMMAND_ENV: scenario.command})


def _matrix(records: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """file -> scenario -> class, for the text report."""
    matrix: dict[str, dict[str, str]] = {}
    for r in records:
        matrix.setdefault(r["file"], {})[r["scenario"]] = r["class"]
    return matrix


def _own_overlay_note(payload: dict[str, Any], overlays: dict[str, Any]) -> str:
    """A "not loaded" note needs a usable negative record and no error: an errored scenario says nothing about loading."""
    group = {f["path"]: (f["path"], *f.get("aliases", ())) for f in payload["files"]}
    own = set(overlays["own_files"])
    own_records = [r for r in payload["records"] if own & set(group.get(r["file"], (r["file"],)))]
    seen = {r["class"] for r in own_records}
    probed = {r["scenario"] for r in own_records}
    # a scenario that errored, or answered with prose (a refusal), says nothing about what it loaded
    usable = {s["id"] for s in payload["scenarios"] if not s.get("error") and (s.get("prose") or 0) <= PROSE_LIMIT}
    negative = {r["class"] for r in own_records if r["scenario"] in usable}
    files = ", ".join(overlays["own_files"])
    # a usable negative in one scenario does not clear a refusal in another: every probed scenario must be usable
    if "not_loaded" in negative and "error" not in seen and probed <= usable:
        return f"note: no {overlays['own']} overlay ({files}) was loaded in any scenario"
    return f"note: could not determine whether the {overlays['own']} overlay ({files}) was loaded"


def saved_gaps(payload: dict[str, Any], fmt: str) -> list[str]:
    """The fields a saved probe must carry to be rendered as ``fmt``: the schema leaves them optional."""
    if fmt == "receipt":
        return [key for key in ("salt", "generated_at") if key not in payload]
    return [] if "matrix" in payload["summary"] else ["summary.matrix"]


_SHORT = {"loaded": "L", "truncated": "T", "fetched": "F", "not_loaded": ".", "error": "E"}  # a cell -> its letter


def saved_matrix_problem(payload: dict[str, Any]) -> str | None:
    """What keeps a saved ``summary.matrix`` from rendering: a row that is not an object, or a cell that is not one of
    the classes the table shows (format_matrix would raise on either). None when every cell can be shown."""
    matrix = payload["summary"]["matrix"]
    if not isinstance(matrix, dict):
        return "summary.matrix is not an object"
    for path, row in matrix.items():
        if not isinstance(row, dict):
            return f"summary.matrix[{path!r}] is not an object"
        for scenario, cell in row.items():
            if not isinstance(cell, str) or cell not in _SHORT:
                return f"summary.matrix[{path!r}][{scenario!r}] is {cell!r}, not a reach class"
    return None


def _overlay_lines(payload: dict[str, Any]) -> list[str]:
    """The overlay lines of the table: a ``finding:`` per foreign overlay that loaded, and the own-overlay note."""
    lines: list[str] = []
    overlays = payload.get("overlays") or {}
    for path, seen in (overlays.get("cross_loaded") or {}).items():
        where = ", ".join(f"{sid} ({cls})" for sid, cls in seen.items())
        lines.append(f"finding: {path}, an overlay for another agent, was loaded by {payload['harness']['name']} in {where}")
    own_ambiguous = set(overlays.get("own_files") or ()) & set(overlays.get("ambiguous") or ())
    if overlays.get("own_files") and not overlays.get("own_loaded") and not own_ambiguous:
        lines.append(_own_overlay_note(payload, overlays))
    return lines


def format_matrix(payload: dict[str, Any]) -> str:
    """A plain-text table: one row per guidance file, one column per scenario."""
    short = _SHORT
    ids = [s["id"] for s in payload["scenarios"]]
    label = {f["path"]: " = ".join([f["path"], ", ".join(f["aliases"])]) if f.get("aliases") else f["path"]
             for f in payload["files"]}
    width = max([len(v) for v in label.values()] + [4])
    lines = [f"{payload['harness']['name']} {payload['harness']['version']}  model={payload['model']}"
             f"  cost=${payload['summary']['cost_usd']:.4f}"]
    lines += [f"  [{i}] {sid}" for i, sid in enumerate(ids)]
    lines.append(" " * (width + 2) + " ".join(f"{i:>2}" for i in range(len(ids))))
    for f in payload["files"]:
        row = payload["summary"]["matrix"].get(f["path"], {})
        lines.append(f"{label[f['path']]:<{width}}  " + " ".join(f"{short[row.get(s, 'error')]:>2}" for s in ids))
    lines.append("L loaded  T truncated  F fetched (after a tool read)  . not loaded  E error")
    lines += _overlay_lines(payload)
    if any(f.get("alias_kind") == "copy" for f in payload["files"]):
        lines.append("note: the paths after = are copies, not links (this OS refused a symlink); "
                     "a harness that skips symlinks would still load them")
    for u in payload.get("unresolved", []):
        lines.append(f"not probed: {u['path']} ({u['reason']})")
    for s in payload["scenarios"]:
        if s.get("shadow"):  # schema-optional: only a shadow scenario has one
            lines.append(f"shadow file of {s['id']}: {s['shadow']['path']} (written for this scenario only) "
                         f"{s['shadow']['class']}")
        if s["error"]:
            lines.append(f"error in {s['id']}: {s['error']}")
        if (s.get("prose") or 0) > PROSE_LIMIT:
            lines.append(f"warning: {s['id']} answered with {s['prose']} words besides tokens; "
                         "read its trace before trusting its not_loaded cells")
        if s.get("unknown"):  # schema-optional: a scenario saved without it reports no unknown tokens
            lines.append(f"unknown tokens in {s['id']}: {', '.join(s['unknown'])}")
    return "\n".join(lines)


def planned(files: list[str], profile: HarnessProfile, model: str, kinds: tuple[str, ...],
            only: tuple[str, ...] = (), bin_path: str | None = None, max_turns: int = 4,
            budget_usd: float = 0.10, shadow: Shadow | None = None) -> list[dict[str, Any]]:
    """What a run would execute, for --dry-run, with the shadow file a shadow scenario writes."""
    return [{"scenario": s.id, "cwd": s.cwd or ".", **({"shadow": s.shadow} if s.shadow else {}),
             "argv": profile.argv(prompt=s.prompt, model=model, tools=s.tools, max_turns=max_turns,
                                  budget_usd=budget_usd, bin_path=bin_path)}
            for s in scenarios(files, kinds, only, shadow)]


def save_reach(payload: dict[str, Any], out_dir: Path) -> Path:
    from dokimi.artifacts import save_artifact

    path = out_dir / "reach.json"
    save_artifact("guidance_reach", payload, path)
    return path


def load_reach(path: Path) -> dict[str, Any]:
    from dokimi.artifacts import load_artifact

    return load_artifact("guidance_reach", path)


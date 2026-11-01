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
import os
import re
import shutil
import tempfile
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
from .runner import SUPPORT_HOST
from .source import RepoTree, SourceError

TOKEN_RE = re.compile(r"\bRC[0-9A-F]{6}\b")
PROBE_FILE = "REACH_PROBE.txt"
PROSE_LIMIT = 8
CLASSES = ("loaded", "truncated", "fetched", "not_loaded", "error")
SEEN = ("loaded", "truncated", "fetched")  # the classes that mean the harness had (some of) the file
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


class ReachError(RuntimeError):
    """The probe cannot run safely (for example a guidance file above the workspace)."""


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
    read: str | None = None  # file the agent is told to read first

    @property
    def prompt(self) -> str:
        return (ASK_AFTER_READ.format(path=self.read) if self.read else "") + ASK


def token(salt: str, path: str, position: str) -> str:
    digest = hashlib.sha256(f"{salt}\0{path}\0{position}".encode()).hexdigest()
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


def _pinned_names(links: dict[str, tuple[str, ...]],
                  native: set[str] | frozenset[str]) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    """Each probed file with the names a load of it can be pinned on, and the aliases that cannot be pinned."""
    names: dict[str, tuple[str, ...]] = {}
    ambiguous: set[str] = set()
    for target, aliases in links.items():
        group = (target, *aliases)
        overlay_names = [n for n in group if overlay_of(n) is not None]
        # a native name anywhere in the group cannot tell which file a harness read, and neither can two overlay
        # names (own or foreign): a harness that reads one of them reads the same file under every other one
        if aliases and (any(PurePosixPath(n).name in native for n in group) or len(overlay_names) > 1):
            ambiguous.update((*aliases, *overlay_names))
            names[target] = ()
        else:
            names[target] = group
    return names, ambiguous


def _loads(records: list[dict[str, Any]], names: dict[str, tuple[str, ...]], links: dict[str, tuple[str, ...]],
           ambiguous: set[str], foreign: set[str], own_files: set[str]) -> tuple[dict[str, dict[str, str]], set[str],
                                                                               set[str]]:
    """The foreign overlays and own overlay files that loaded, by the names they can be pinned on, and the
    aliases whose file loaded without a way to tell which of them was read."""
    cross: dict[str, dict[str, str]] = {}
    own_loaded: set[str] = set()
    unpinned: set[str] = set()
    for r in records:
        if r["class"] not in SEEN:
            continue
        for n in names.get(r["file"], ()):
            if n in foreign:
                cross.setdefault(n, {})[r["scenario"]] = r["class"]
            if n in own_files:
                own_loaded.add(n)
        unpinned.update(n for n in (r["file"], *links.get(r["file"], ())) if n in ambiguous)
    return cross, own_loaded, unpinned


def overlay_report(records: list[dict[str, Any]], own: str | None,
                   links: dict[str, tuple[str, ...]], native: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """The overlay part of the payload: which of the ``own`` overlay files loaded, and which foreign overlays did
    (``cross_loaded``, a finding: the harness read guidance written for another agent). ``links`` maps each probed
    file to the links that name it: a file reached through an overlay-named link is an overlay under that name.

    A load is pinned on a link only when no other name of its file could have been read: the harness reads a
    file of a ``native`` name by itself, and an own and a foreign overlay can name one file. Those links are
    ``ambiguous`` when the file loaded, and are neither own nor cross loads."""
    every = {n for target, aliases in links.items() for n in (target, *aliases)}
    own_files = {n for n in every if own and overlay_of(n) == own}
    foreign = {n for n in every if overlay_of(n) not in (None, own)}
    names, ambiguous = _pinned_names(links, native)
    cross, own_loaded, unpinned = _loads(records, names, links, ambiguous, foreign, own_files)
    report: dict[str, Any] = {"own": own, "own_files": sorted(own_files), "own_loaded": sorted(own_loaded),
                              "foreign_files": sorted(foreign), "cross_loaded": dict(sorted(cross.items()))}
    if unpinned:
        report["ambiguous"] = sorted(unpinned)
    return report


def scenarios(files: list[str], kinds: tuple[str, ...] = ("root", "cwd", "read"),
              only: tuple[str, ...] = ()) -> list[Scenario]:
    dirs = sorted({str(PurePosixPath(f).parent) for f in files} - {"."})
    out: list[Scenario] = []
    if "root" in kinds:
        out.append(Scenario("cwd=.", "", "none"))
    if "cwd" in kinds:
        out.extend(Scenario(f"cwd={d}", d, "none") for d in dirs)
    if "read" in kinds:
        out.extend(Scenario(f"read={d}", "", "read", read=f"{d}/{PROBE_FILE}") for d in dirs)
    return [s for s in out if s.id in only] if only else out


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


def distinct_salt(tree: RepoTree, files: list[str], salt: str, attempts: int = 50) -> str:
    """``salt``, or ``salt-N`` for the first N that gives every file its own two tokens.

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
        tokens = [token(candidate, p, pos) for p in files for pos in ("head", "tail")]
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


def prose_words(answer: str) -> int:
    """Words in the answer besides tokens and NONE. A long tail usually means the
    model explained or refused instead of listing, so its not_loaded cells are suspect."""
    words = re.findall(r"[A-Za-z]{2,}", TOKEN_RE.sub(" ", answer))
    return len([w for w in words if w != "NONE"])


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


def run_reach(
    *,
    tree: RepoTree,
    manifest: RepoManifest,
    profile: HarnessProfile,
    model: str,
    out_dir: Path,
    bin_path: str | None = None,
    work_dir: Path | None = None,
    kinds: tuple[str, ...] = ("root", "cwd", "read"),
    only: tuple[str, ...] = (),
    salt: str | None = None,
    max_turns: int = 4,
    budget_usd: float = 0.10,
    timeout_s: int = 300,
    keep: bool = False,
) -> dict[str, Any]:
    """Run every scenario once and return the guidance_reach.v1 payload (unsaved)."""
    files = probe_files(tree, manifest, profile)
    if not files:
        raise ReachError("no guidance files to probe: the manifest's globs and the "
                         f"{profile.name} guidance names match nothing")
    groups, unresolved = resolve_links(tree, files)
    if not groups:
        raise ReachError("no guidance file to probe: each is a link that cannot be followed: "
                         + "; ".join(u["reason"] for u in unresolved))
    files = [p for p in files if p not in {u["path"] for u in unresolved}]
    salt = salt or hashlib.sha256(f"{tree.label}\0{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:12]
    # Resolved: a harness reports physical paths (macOS /var is /private/var).
    base = Path(tempfile.mkdtemp(prefix="reach-", dir=str(work_dir) if work_dir else None)).resolve()
    workspace = base / "ws"
    try:
        workspace.mkdir()
        check_ancestors(workspace, tuple(dict.fromkeys(ANCESTOR_GUARD + profile.guidance_names)))
        salt = distinct_salt(tree, list(groups), salt)
        seeded = build_workspace(tree, list(groups), workspace, salt, groups)
        version_env = profile.env(base / "cfg-version")
        version = profile.version(version_env, bin_path)
        trace_dir = out_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        for old in trace_dir.iterdir():  # traces/ is the record of this run only
            if re.match(r"^\d{2,}-.*\.(stdout|events\.jsonl)$", old.name) and old.is_file():
                old.unlink()
        known = {s.head for s in seeded} | {s.tail for s in seeded}
        scenario_rows, records = [], []
        for n, scenario in enumerate(scenarios(files, kinds, only)):
            transcript, row = _run_one(profile, scenario, model, workspace, base / f"cfg-{n}",
                                       bin_path, max_turns, budget_usd, timeout_s)
            slug = f"{n:02d}-" + re.sub(r"[^A-Za-z0-9._-]+", "_", scenario.id)
            (trace_dir / f"{slug}.stdout").write_text(row.pop("_stdout"), encoding="utf-8")
            if transcript is not None:
                (trace_dir / f"{slug}.events.jsonl").write_text(transcript.events_jsonl(), encoding="utf-8")
                reported = sorted(set(TOKEN_RE.findall(transcript.answer)))
                row.update(reported=reported, unknown=[t for t in reported if t not in known],
                           prose=prose_words(transcript.answer))
                version = transcript.harness_version or version
            scenario_rows.append(row)
            for item in seeded:
                records.append({"file": item.path, "scenario": scenario.id, "harness": profile.name,
                                "harness_version": version, "model": model, **classify(item, transcript)})
    finally:
        if not keep:
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
                                   {PurePosixPath(n).name for n in profile.guidance_names}),
        "summary": {
            "cost_usd": round(sum(r.get("cost_usd") or 0 for r in scenario_rows), 6),
            "by_class": by_class,
            "matrix": _matrix(records),
        },
    }


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
        proc = process.run(argv, cwd=workspace / scenario.cwd, env=profile.env(config_dir),
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
    seen = {r["class"] for r in payload["records"] if own & set(group.get(r["file"], (r["file"],)))}
    files = ", ".join(overlays["own_files"])
    if "not_loaded" in seen and "error" not in seen:
        return f"note: no {overlays['own']} overlay ({files}) was loaded in any scenario"
    return f"note: could not determine whether the {overlays['own']} overlay ({files}) was loaded"


def format_matrix(payload: dict[str, Any]) -> str:
    """A plain-text table: one row per guidance file, one column per scenario."""
    short = {"loaded": "L", "truncated": "T", "fetched": "F", "not_loaded": ".", "error": "E"}
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
    overlays = payload.get("overlays") or {}
    for path, seen in (overlays.get("cross_loaded") or {}).items():
        where = ", ".join(f"{sid} ({cls})" for sid, cls in seen.items())
        lines.append(f"finding: {path}, an overlay for another agent, was loaded by {payload['harness']['name']} in {where}")
    if overlays.get("own_files") and not overlays.get("own_loaded") and not overlays.get("ambiguous"):
        lines.append(_own_overlay_note(payload, overlays))
    if any(f.get("alias_kind") == "copy" for f in payload["files"]):
        lines.append("note: the paths after = are copies, not links (this OS refused a symlink); "
                     "a harness that skips symlinks would still load them")
    for u in payload.get("unresolved", []):
        lines.append(f"not probed: {u['path']} ({u['reason']})")
    for s in payload["scenarios"]:
        if s["error"]:
            lines.append(f"error in {s['id']}: {s['error']}")
        if (s.get("prose") or 0) > PROSE_LIMIT:
            lines.append(f"warning: {s['id']} answered with {s['prose']} words besides tokens; "
                         "read its trace before trusting its not_loaded cells")
        if s["unknown"]:
            lines.append(f"unknown tokens in {s['id']}: {', '.join(s['unknown'])}")
    return "\n".join(lines)


def planned(files: list[str], profile: HarnessProfile, model: str, kinds: tuple[str, ...],
            only: tuple[str, ...] = (), bin_path: str | None = None, max_turns: int = 4,
            budget_usd: float = 0.10) -> list[dict[str, Any]]:
    """What a run would execute, for --dry-run."""
    return [{"scenario": s.id, "cwd": s.cwd or ".",
             "argv": profile.argv(prompt=s.prompt, model=model, tools=s.tools, max_turns=max_turns,
                                  budget_usd=budget_usd, bin_path=bin_path)}
            for s in scenarios(files, kinds, only)]


def save_reach(payload: dict[str, Any], out_dir: Path) -> Path:
    from dokimi.artifacts import save_artifact

    path = out_dir / "reach.json"
    save_artifact("guidance_reach", payload, path)
    return path


def load_reach(path: Path) -> dict[str, Any]:
    from dokimi.artifacts import load_artifact

    return load_artifact("guidance_reach", path)


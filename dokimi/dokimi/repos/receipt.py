# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The effective-instructions receipt (``effective-instructions/v0``, agentsmd/agents.md#213) of one reach probe.

Built from a ``guidance_reach.v1`` payload and the start-up chain of ``repo_budget.v1``. Per run (one reach
scenario) and per guidance source it gives the expected state (budget's start-up rules), the observed state (the
probe's marker tokens) and the states ``accessed``, ``selected``, ``loaded`` and ``followed``. What the probe
cannot establish is ``unknown``; ``followed`` always is. docs/repo-evaluation-contract.md §7.1 holds the mapping
and its limits.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .budget import _imports, _target
from .reach import PROSE_LIMIT, ReachError
from .source import RepoTree

SCHEMA = "effective-instructions/v0"
SCHEMA_VERSION = "effective_instructions.v1"
UNKNOWN = "unknown"
# The thread vocabulary's observation_method: no runtime event, prompt capture or hook backs the probe.
OBSERVATION_METHOD = UNKNOWN
# dokimi's own method: the model reports the marker tokens it can see; the request itself is not captured.
DOKIMI_METHOD = "marker_self_report"
# Root-level files that turn AGENTS.md off in Claude Code (budget.py): the displacing set.
CLAUDE_ROOT = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
ROOT_NESTED = (".claude/CLAUDE.md", ".claude/AGENTS.md")  # project memory in .claude/ applies repo-wide
SUBTREE_NAMES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")
_STATE: dict[str | None, str] = {"loaded": "loaded", "fetched": "fetched", "not_loaded": "not_loaded"}
_LOADED: dict[str | None, bool] = {"loaded": True, "not_loaded": False}
DETAIL = ("the model is asked to report the marker tokens it can see; the request sent to the provider is not "
          "captured, so a reported token is evidence of loading, not a copy of the bytes it received")
NOT_PROBED = ("start-up imports and skill listings from repo_budget are not probed by reach; their observed state is "
              "unknown, and skill bodies (loaded on demand) are not listed")


@dataclass(frozen=True)
class _Run:
    row: dict[str, Any]  # the reach scenario row
    records: dict[tuple[str, str], dict[str, Any]]  # (file, scenario id) -> record
    when_of: dict[str, str]  # canonical path -> budget's when
    own: frozenset[str]  # guidance files budget lists under their own path (not a link to another file)
    digests: dict[str, str | None]
    workdir: str  # the directory the run works in
    rules: bool  # budget's rules apply (Claude Code)


def build_receipt(reach: dict[str, Any], budget: dict[str, Any], tree: RepoTree) -> dict[str, Any]:
    """The ``effective_instructions.v1`` artifact: one ``effective-instructions/v0`` receipt per reach scenario."""
    harness = reach["harness"]
    rules = harness["name"] == "claude-code"  # budget's start-up rules are Claude Code's
    when_of = _budget_when(reach, budget) if rules else {}
    own = _own_guidance(reach, budget, tree)
    start_up = [p for p, when in when_of.items() if when in ("start", "off") and p not in _known(reach)]
    digests = source_digests(tree, [p for p, _ in _probed(reach)] + start_up)
    records = {(r["file"], r["scenario"]): r for r in reach["records"]}
    runtime = {"name": harness["name"], "version": harness["version"]}
    receipts = []
    for row in reach["scenarios"]:
        run = _Run(row, records, when_of, own, digests, _workdir(row), rules)
        receipts.append(_receipt(reach, run, runtime, start_up))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": reach["generated_at"],
        "repo": {**reach["repo"], "tree": _shareable_tree(reach["repo"]["tree"])},
        "runtime": runtime,
        "model": reach["model"],
        "observation": {"method": OBSERVATION_METHOD, "dokimi_method": DOKIMI_METHOD, "detail": DETAIL},
        "unknowns": _global_unknowns(harness["name"], rules, pinned=bool(getattr(tree, "sha", None))),
        "receipts": receipts,
    }


_REV_SUFFIX = re.compile(r"@[0-9a-f]{7,40}$")  # the pinned revision a git source's label ends with
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")  # https://, ssh://, file://, ...
_SCP_LIKE = re.compile(r"^[^/\\\s]+@[^/\\\s]+:")  # git@host:path
_USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/]*)@")  # scheme://user[:secret]@, up to the last @ before the host


def _shareable_tree(label: str) -> str:
    """The tree label with a local directory cut to its last component, so no home path reaches a shared receipt.
    A remote label (any URL scheme but file://, or git@host:path) is kept whole; any other label names a local
    directory, absolute or relative, and is cut. The revision is the label's own hex suffix, so an ``@`` in a parent
    directory is part of the path."""
    match = _REV_SUFFIX.search(label)
    name, rev = (label[: match.start()], match.group()) if match else (label, "")
    if name.lower().startswith("file://"):
        local = True
    else:
        local = not (_URL_SCHEME.match(name) or _SCP_LIKE.match(name))
    if not local:
        return _USERINFO.sub(_drop_secret, name, count=1) + rev
    base = PurePosixPath(name.replace("\\", "/")).name
    return (base if base not in ("", ".", "..") else "tree") + rev


def _drop_secret(match: re.Match[str]) -> str:
    """Userinfo that can carry a credential is dropped: any ``user:secret``, and all of it over http(s), where a
    token is often the user name. A bare ssh user (``ssh://git@``) names no secret and is kept."""
    scheme, user = match.groups()
    if ":" in user or scheme.lower().startswith("http"):
        return scheme
    return match.group()


def check_saved_reach(reach: dict[str, Any], repo_id: str, harness_name: str, tree_sha: str | None) -> None:
    """Refuse a saved reach.json probed on another repository, harness or commit than the one it is read against."""
    if reach["repo"]["id"] != repo_id:
        raise ReachError(f"reach.json is for repository {reach['repo']['id']!r}, not {repo_id!r}")
    if reach["harness"]["name"] != harness_name:
        raise ReachError(f"reach.json was probed with harness {reach['harness']['name']!r}, not {harness_name!r}")
    probed = reach["repo"].get("sha")
    if probed and tree_sha and probed != tree_sha:
        raise ReachError(f"reach.json was probed at {probed[:12]}, the source is at {tree_sha[:12]}")


def check_receipt_provenance(reach: dict[str, Any], tree_sha: str | None) -> None:
    """Refuse a receipt rebuilt from a saved probe unless both sides are at a commit. The digests and expectations
    are read from the source now, so they describe the probed text only when that text is pinned: a working tree
    may have changed since the probe, and a probe of a working tree has no commit to check the source against."""
    if not tree_sha:
        raise ReachError("--format receipt needs a pinned source: a working tree can change after the probe, so give "
                         "the repository at a revision (<path>@<rev>)")
    if not reach["repo"].get("sha"):
        raise ReachError("reach.json was probed from a working tree (repo.sha is null), so its receipt cannot be "
                         "tied to a commit; probe a pinned source to get a receipt")


def source_digests(tree: RepoTree, paths: list[str]) -> dict[str, str | None]:
    """sha256 of the text dokimi reads for each path (LF line endings: the text the probe seeds); None if unreadable."""
    out: dict[str, str | None] = {}
    for path in dict.fromkeys(paths):
        try:
            out[path] = hashlib.sha256(tree.read_text(path).encode("utf-8")).hexdigest()
        except (OSError, ValueError, RuntimeError):  # SourceError and UnicodeDecodeError are ValueErrors
            out[path] = None
    return out


def _probed(reach: dict[str, Any]) -> list[tuple[str, str | None]]:
    """(path, alias_of) for every probed path: each guidance file, then the links to it."""
    out: list[tuple[str, str | None]] = []
    for item in reach["files"]:
        out.append((item["path"], None))
        out.extend((alias, item["path"]) for alias in item.get("aliases", []))
    return out


def _known(reach: dict[str, Any]) -> set[str]:
    return {path for path, _ in _probed(reach)}


def _budget_when(reach: dict[str, Any], budget: dict[str, Any]) -> dict[str, str]:
    """budget's ``when`` of each guidance file and import, keyed by the file the path resolves to."""
    target_of = {alias: item["path"] for item in reach["files"] for alias in item.get("aliases", [])}
    return {target_of.get(e["path"], e["path"]): e["when"]
            for e in budget["entries"] if e["kind"] in ("guidance", "import", "skill listing")}


def _own_guidance(reach: dict[str, Any], budget: dict[str, Any], tree: RepoTree) -> frozenset[str]:
    """Guidance files budget lists under their own path, and that no other guidance file @-imports: a link or an
    import is activated where its importer is, so the file has no subtree of its own."""
    aliases = {alias for item in reach["files"] for alias in item.get("aliases", [])}
    guidance = [e["path"] for e in budget["entries"] if e["kind"] == "guidance"]
    imported = {_target(tree, p) for g in guidance for p in _imports(tree, [g])[0]}
    return frozenset(p for p in guidance if p not in aliases and p not in imported)


def _dir(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def _workdir(row: dict[str, Any]) -> str:
    """The directory a run works in: the one holding the file it is told to read, else where it starts."""
    return _dir(row["read"]) if row.get("read") else row["cwd"]


def _receipt(reach: dict[str, Any], run: _Run, runtime: dict[str, str], start_up: list[str]) -> dict[str, Any]:
    sources = [_source(path, alias_of, run) for path, alias_of in _probed(reach)]
    sources += [_budget_only(path, run) for path in start_up]
    off = {p for p, when in run.when_of.items() if when == "off"}
    row = run.row
    return {
        "schema": SCHEMA,
        "run_id": f"{reach['salt']}:{row['id']}",
        "runtime": runtime,
        "cwd": row["cwd"] or ".",
        "target": row.get("read"),
        "sources": sources,
        "precedence_observed": _precedence(sources, off),
        "unknowns": _run_unknowns(row, sources, run),
    }


def _source(path: str, alias_of: str | None, run: _Run) -> dict[str, Any]:
    target = alias_of or path
    rec = run.records.get((target, run.row["id"]))
    cls = rec["class"] if rec else None
    return {
        "path": path,
        **({"alias_of": alias_of} if alias_of else {}),
        "sha256": run.digests.get(path),
        "scope": _scope(path, alias_of is not None),
        "expected": _expected(target, run.when_of.get(target), run),
        "observed": _STATE.get(cls, UNKNOWN),  # a truncated cell saw one token: an observation, no verdict
        "observation_method": OBSERVATION_METHOD,
        "dokimi_method": DOKIMI_METHOD if rec else None,
        "states": _states(cls, run.row["tools"] == "none", bool(rec and rec["read_by_tool"])),
    }


def _budget_only(path: str, run: _Run) -> dict[str, Any]:
    """A start-up import or guidance file budget found and reach did not probe: expected, never observed."""
    return {"path": path, "sha256": run.digests.get(path), "scope": _scope(path, False),
            "expected": _expected(path, run.when_of[path], run), "observed": UNKNOWN,
            "observation_method": OBSERVATION_METHOD, "dokimi_method": None,
            "states": _states(None, False, False)}


def _states(cls: str | None, tools_off: bool, read_by_tool: bool) -> dict[str, Any]:
    """accessed: a Read-tool read was seen (false with no tools at all); selected: the probe saw the source enter the
    request, by the harness or by the agent's own read; loaded: at the harness's own load step, not through a read."""
    accessed: bool | str = True if read_by_tool else (False if tools_off else UNKNOWN)
    selected: bool | str = True if cls in ("loaded", "fetched") else UNKNOWN
    return {"accessed": accessed, "selected": selected, "loaded": _LOADED.get(cls, UNKNOWN), "followed": UNKNOWN}


def _expected(target: str, when: str | None, run: _Run) -> bool | str:
    """Start-up files are expected in every run; off ones in none; a nested lazy file in a run whose directory is its
    subtree (or inside it), since the harness loads it when the work reaches that directory."""
    if not run.rules or when is None:
        return UNKNOWN
    if when in ("start", "off"):
        return when == "start"
    if when == "lazy":  # budget's flattened when cannot say where a link or an import is activated: only a subtree file
        d = _dir(target)
        if target not in run.own or not d or target.rsplit("/", 1)[-1] not in SUBTREE_NAMES:
            return UNKNOWN
        return run.workdir == d or run.workdir.startswith(d + "/")
    return UNKNOWN


def _scope(path: str, alias: bool) -> str:
    if alias:
        return "adapter"  # a link to another guidance file: its tokens are the target's
    if "/" not in path or path in ROOT_NESTED:
        return "repo"
    return "subtree" if path.rsplit("/", 1)[-1] in SUBTREE_NAMES else UNKNOWN


def _precedence(sources: list[dict[str, Any]], off: set[str]) -> list[dict[str, str]]:
    """A file budget says a root CLAUDE* file displaces, seen not loaded while that file was seen loaded in the run."""
    winners = sorted(s["path"] for s in sources if s["path"] in CLAUDE_ROOT and s["observed"] == "loaded")
    if not winners:
        return []
    return [{"winner": w, "displaced": s["path"]}
            for s in sources if (s.get("alias_of") or s["path"]) in off and s["observed"] == "not_loaded"
            for w in winners]


def _recorded_class(source: dict[str, Any], run: _Run) -> str | None:
    """The reach class of the record this source's observation came from (its alias's, for a link)."""
    rec = run.records.get((source.get("alias_of") or source["path"], run.row["id"]))
    return rec["class"] if rec else None


def _run_unknowns(row: dict[str, Any], sources: list[dict[str, Any]], run: _Run) -> list[str]:
    out = []
    if row.get("error"):
        out.append("no usable answer from this run: every observed state is unknown")
    else:
        # a truncated record saw one of the two marker tokens; an error record saw neither, so it says nothing
        out += [f"{s['path']}: one of its two marker tokens was reported; loaded is not established"
                for s in sources if _recorded_class(s, run) == "truncated"]
        out += [f"{s['path']}: the probe recorded an error for this file; no state is established"
                for s in sources if _recorded_class(s, run) == "error"]
    if (row.get("prose") or 0) > PROSE_LIMIT:
        out.append(f"answered with {row['prose']} words besides tokens; its not_loaded states may be under-reports")
    if row.get("unknown"):
        out.append("reported tokens that were never seeded: " + ", ".join(row["unknown"]))
    return out


def _global_unknowns(name: str, rules: bool, pinned: bool) -> list[str]:
    out = [
        "followed is not observed: no marker shows what the model did with a loaded source",
        "observation_method is unknown: no prompt is captured; dokimi_method names the marker self-report instead",
        "accessed is observed only where the harness reports a read event; "
        "a shell read it does not normalise is not seen",
        "sha256 is over the LF-normalised UTF-8 text dokimi reads, which the probe seeds; a CRLF file's bytes differ",
        "no load timestamps: the probe does not record when a source entered the request",
        "the order in which sources entered the request is not observed; precedence_observed lists displacements only",
        NOT_PROBED,
    ]
    if not rules:
        out.append(f"budget's start-up rules are Claude Code's: no source has an expected state for {name}")
    if not pinned:
        out.append("the source is a working tree: sha256 and expected are read after the harness exits, so an edit "
                   "made while the run was in flight is not reflected in the observations; pin the source to a "
                   "revision for a stable receipt")
    return out

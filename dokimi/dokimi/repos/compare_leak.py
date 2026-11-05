# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare``: the replay tasks a ``guidance:rev:`` arm may have seen (contract §9).

A ``guidance:rev:<rev>`` arm runs a revision of the guidance against replay tasks built from PRs merged
earlier. Guidance written or edited after a task's review can encode that review, so the arm catches the
thread because a rule was written from it (https://github.com/intel/dokimi/issues/290). Each compared arm
(baseline and treatments) whose cells' ``meta.json`` has ``arm_rev`` (or whose name starts with
``guidance:rev:``) is checked, over its tasks whose cell ``task.json`` has ``public_at`` or ``source_pr``
(a cell with no ``task.json`` is not read):

- **by date**: some guidance file that applies to the task has its ``arm_rev.guidance_latest`` commit
  ``committed`` (the committer date: an amend or a rebase keeps the author date) later than the task's
  ``public_at``, compared as times. A file applies to every task unless it is a directory-scoped instruction
  file (:data:`SCOPED_NAMES`) below the root: then it applies to a task whose ``patch_files`` has a path
  under its directory (``.claude/`` counts as its parent). Every other guidance path (``.claude/rules/*``
  whatever its name, ``.github/copilot-instructions.md``, a skill, ...) applies to every task, the strict side;
  so does every file to a task with no ``patch_files`` (one built before ``repo tasks replay`` wrote it), and its entry
  says ``scoped: false``. A task with no ``public_at`` is unknown by date. ``@`` imports are not followed:
  ``guidance_latest`` holds no import graph, so a nested file a root file imports counts as scoped.
- **by name**: an ``arm_rev.guidance_commits`` subject names the task's PR: ``#<source_pr>``, or ``<source_pr>``
  after ``PR``, ``PR#``, ``PR #`` or ``GH-`` (any case), not preceded
  by a word character, ``/``, ``.`` or ``-`` (what a repository name holds), ``<owner>/<repo>#<source_pr>``
  of the task's own ``source_url``, or that PR's URL, none followed by a digit. ``other/repo#<n>`` does not
  count, no text is compared, and a task
  with no ``source_pr`` has no PR to be named. With
  ``guidance_commits_truncated`` the check covered only the newest commits the list kept.

A ``patch:`` arm has no history: each of its replay tasks is unknown, and so is each of an ``arm_rev`` with
no ``guidance_latest`` (a batch from before it was recorded). Cells of one (arm, task) may differ (pooled
batches): a flag any cell sets counts, the named commits are their union. A ``meta.json`` or ``task.json``
of the wrong shape makes its task unknown, with the reason. The flags change no delta, interval or p.
"""

from __future__ import annotations

import json
import posixpath
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .budget import RULE_GLOB
from .guidance_history import GUIDANCE_COMMITS_CAP
from .receipt import SUBTREE_NAMES
from .workspace import _SKILL_PREFIXES, ARMS, FORMAT_ARM

#: Instruction files that apply to their own directory and below; any other guidance path applies everywhere
SCOPED_NAMES = (*SUBTREE_NAMES, "GEMINI.md")
LIST_CAP = 10  # ids shown on a line of text before "and N more"

_State = tuple[str, Any]  # ("flag", what tripped it) | ("clean", None) | ("unknown", reason)
_CLEAN: _State = ("clean", None)


def leak_check(batch_dirs: list[Path], arms: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One entry per arm of ``arms`` (selector → its compared cells) that has a guidance history to check (or
    is a ``patch:`` arm) and a replay-style task; ``[]`` when none has. ``batch_dirs`` holds the cells."""
    dirs = {d.resolve().name: d for d in batch_dirs}
    out = []
    for text, cells in arms.items():
        read = [(c["task"], _read(dirs[c["batch"]] / c["cell"] / "meta.json"),
                 _read(dirs[c["batch"]] / c["cell"] / "task.json")) for c in cells]
        kind = _kind(cells, [meta for _, meta, _ in read])
        entry = _arm_entry(text, kind, read) if kind is not None else None
        if entry is not None and entry["checked"]:  # an arm with no replay-style task has nothing to say
            out.append(entry)
    return out


def _read(path: Path) -> dict[str, Any] | str | None:
    """A cell file as a dict, ``None`` when it is absent, or a str saying what is wrong with it."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        return f"its {path.name} is not readable JSON"
    return value if isinstance(value, dict) else f"its {path.name} is not an object"


def _kind(cells: list[dict[str, Any]], metas: list[Any]) -> str | None:
    """``guidance:rev`` or ``patch`` for an arm with a guidance history to check (or none to have), else ``None``."""
    if any(isinstance(m, dict) and "arm_rev" in m for m in metas) or any(
            str(c["arm"]).startswith("guidance:rev:") for c in cells):
        return "guidance:rev"
    if any(isinstance(m, dict) and "arm_patch" in m for m in metas) or any(
            str(c["arm"]).startswith("patch:") for c in cells):
        return "patch"
    if all(str(c["arm"]).split("+")[0] in ARMS or str(c["arm"]).startswith((FORMAT_ARM, *_SKILL_PREFIXES))
           for c in cells):
        return None  # the name says it has no history (guidance:none, guidance:format=, skill:...), whatever meta.json is
    # An unreadable meta.json hides what an arm of another name is: check it, so its replay tasks are reported unknown.
    return "guidance:rev" if any(isinstance(m, str) for m in metas) else None


def _arm_entry(text: str, kind: str, read: list[tuple[str, Any, Any]]) -> dict[str, Any]:
    states: dict[str, list[tuple[_State, _State]]] = {}
    truncated = False
    for task, meta, spec in read:
        checked = _check_cell(kind, meta, spec)
        if checked is not None:
            states.setdefault(task, []).append(checked)
            rev = meta.get("arm_rev") if isinstance(meta, dict) else None
            truncated |= isinstance(rev, dict) and rev.get("guidance_commits_truncated") is True
    entry: dict[str, Any] = {"arm": text, "kind": kind, "revisions": _revisions(read), "checked": sorted(states),
                             "not_checked": sorted({task for task, _, _ in read} - set(states)),
                             "by_date": {}, "by_name": {}, "unknown": {}, "clean": [], "names_truncated": truncated}
    for task in sorted(states):
        date, name = _merge([d for d, _ in states[task]]), _merge([n for _, n in states[task]])
        if date[0] == "flag":
            entry["by_date"][task] = max(date[1], key=_hit_order)
        if name[0] == "flag":
            entry["by_name"][task] = list(dict.fromkeys(sha for shas in name[1] for sha in shas))
        reasons = [s[1] for s in (date, name) if s[0] == "unknown"]
        if reasons:
            entry["unknown"][task] = "; ".join(dict.fromkeys(reasons))
        elif date == name == _CLEAN:
            entry["clean"].append(task)
    return entry


def _hit_order(hit: dict[str, Any]) -> tuple[datetime, str]:
    """The latest commit that tripped a cell's date flag sorts last (:func:`_by_date` read its date)."""
    at = _when(hit["committed"])
    assert at is not None
    return at, hit["path"]


def _revisions(read: list[tuple[str, Any, Any]]) -> list[dict[str, Any]]:
    """The distinct ``arm_rev`` ``rev``/``sha`` of the arm's cells (two when pooled batches resolved it apart)."""
    seen = {(str(rev.get("rev")), str(rev.get("sha"))) for _, meta, _ in read
            if isinstance(meta, dict) and isinstance(rev := meta.get("arm_rev"), dict)}
    return [{"rev": r, "sha": s} for r, s in sorted(seen)]


def _merge(states: list[_State]) -> _State:
    """One task's state from its cells': flagged when any cell is (the hits of all), else unknown when any is."""
    hits = [value for status, value in states if status == "flag"]
    if hits:
        return ("flag", hits)
    return next((s for s in states if s[0] == "unknown"), _CLEAN)


def _check_cell(kind: str, meta: Any, spec: Any) -> tuple[_State, _State] | None:
    """The cell's (by date, by name) states; ``None`` for a task that is not replay-style."""
    if isinstance(spec, str):
        return ("unknown", spec), ("unknown", spec)
    if spec is None or ("public_at" not in spec and "source_pr" not in spec):
        return None
    rev = meta.get("arm_rev") if isinstance(meta, dict) else None
    if kind == "patch":
        why = "a patch: arm has no guidance history"
    elif not isinstance(rev, dict):
        why = meta if isinstance(meta, str) else "its meta.json has no arm_rev object"
    elif "guidance_latest" not in rev:
        why = "no guidance history recorded"
    else:
        return _by_date(rev, spec), _by_name(rev, spec)
    return ("unknown", why), ("unknown", why)


def _by_date(rev: dict[str, Any], spec: dict[str, Any]) -> _State:
    """Flagged with the latest applicable ``guidance_latest`` commit committed after ``public_at``."""
    if spec.get("public_at") is None:
        return ("unknown", "no public_at")
    public, files, latest = _when(spec["public_at"]), spec.get("patch_files"), rev["guidance_latest"]
    if public is None:
        return ("unknown", "public_at is not a date with a UTC offset")
    if files is not None and not (isinstance(files, list) and all(isinstance(p, str) for p in files)):
        return ("unknown", "patch_files is not a list of paths")
    if not isinstance(latest, dict):
        return ("unknown", "guidance_latest is not an object")
    hits, undated = [], []
    for path, commit in sorted(latest.items()):
        if not applies(path, files):
            continue
        when = _when(commit.get("committed")) if isinstance(commit, dict) else None
        if when is None:
            undated.append(path)
        elif when > public:
            hits.append((when, path, commit))
    if hits:
        _, path, commit = max(hits, key=lambda h: (h[0], h[1]))
        return ("flag", {"path": path, "sha": commit.get("sha"), "committed": commit["committed"],
                         "public_at": spec["public_at"], "scoped": files is not None})
    return ("unknown", f"guidance_latest has no committed date for {undated[0]}") if undated else _CLEAN


def applies(path: str, patch_files: list[str] | None) -> bool:
    """Whether a guidance file at ``path`` applies to a task patching ``patch_files`` (``None``: not known)."""
    if patch_files is None or posixpath.basename(path) not in SCOPED_NAMES or RULE_GLOB.match(path):
        return True  # a file under .claude/rules/ is a rule whatever its name, loaded at start
    scope = posixpath.dirname(path)
    if posixpath.basename(scope) == ".claude":  # .claude/CLAUDE.md is its parent directory's memory
        scope = posixpath.dirname(scope)
    return not scope or any(p.startswith(scope + "/") for p in patch_files)


def _by_name(rev: dict[str, Any], spec: dict[str, Any]) -> _State:
    """Flagged with the ``guidance_commits`` whose subject names the task's PR (:func:`names_pr`)."""
    pr = spec.get("source_pr")
    if pr is None:
        return _CLEAN  # no PR to be named
    if isinstance(pr, bool) or not isinstance(pr, (int, str)) or not re.fullmatch(r"[0-9]{1,18}", str(pr)):
        return ("unknown", "source_pr is not a PR number")
    commits = rev.get("guidance_commits")
    if not isinstance(commits, list):
        return ("unknown", "no guidance_commits recorded")
    pattern = names_pr(int(pr), spec.get("source_url"))
    shas, unread = [], False
    for c in commits:
        if not isinstance(c, dict) or not isinstance(c.get("subject"), str):
            unread = True
        elif pattern.search(c["subject"]):
            shas.append(str(c.get("sha")))
    if shas:
        return ("flag", shas)
    return ("unknown", "a guidance_commits entry has no subject") if unread else _CLEAN


def names_pr(pr: int, source_url: Any) -> re.Pattern[str]:
    """What names PR ``pr`` in a subject: ``#<pr>``, ``PR<pr>``, ``PR#<pr>``, ``PR <pr>``, ``GH-<pr>``, and with a
    ``.../<owner>/<repo>/pull/<n>`` ``source_url`` ``<owner>/<repo>#<pr>`` and the PR's URL with nothing glued
    before its host; never followed by a digit."""
    forms = [rf"(?<![\w/.-])(?:#|PR\s*#?|GH-){pr}(?!\d)"]
    found = re.fullmatch(r"https?://([^/]+)/([^/]+)/([^/]+)/pull/\d+/?", source_url) \
        if isinstance(source_url, str) else None
    if found:
        host, repo = (re.escape(p) for p in (found[1], f"{found[2]}/{found[3]}"))
        forms += [rf"(?<![\w./-]){repo}#{pr}(?!\d)", rf"(?<![\w./+-])(?:https?://)?{host}/{repo}/pull/{pr}(?!\d)"]
    return re.compile("|".join(forms), re.IGNORECASE)


def _when(value: Any) -> datetime | None:
    """An ISO 8601 date with an offset (``Z`` or ``+00:00``) as an aware datetime; ``None`` for anything else."""
    try:
        at = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return at if at is not None and at.tzinfo is not None else None


def format_leak(entries: list[dict[str, Any]]) -> list[str]:
    """The text lines of ``summary["leak"]``: per checked arm its counts, then the tasks behind them; ``clean:``
    holds its clean tasks and those it does not check, as the ``--task`` options that leave out the flagged ones."""
    lines = []
    flagged = {t for e in entries for t in (*e["by_date"], *e["by_name"], *e["unknown"])}
    for e in entries:
        revs = ", ".join(f"{r['rev']} {r['sha'][:12]}" for r in e["revisions"])
        lines += ["", f"leak check {e['arm']}{f' ({revs})' if revs else ''}: {len(e['checked'])} checked, "
                      f"{len(e['by_date'])} by date, {len(e['by_name'])} by name, {len(e['unknown'])} unknown, "
                      f"{len(e['clean'])} clean"]
        if e["by_name"]:
            lines.append("  by name: " + _capped([f"{t} ({', '.join(s[:12] for s in shas)})"
                                                  for t, shas in e["by_name"].items()]))
        if e["names_truncated"]:
            lines.append(f"  by name covers only the newest {GUIDANCE_COMMITS_CAP} guidance commits "
                         "(guidance_commits_truncated)")
        unscoped = [t for t, hit in e["by_date"].items() if not hit["scoped"]]
        if unscoped:
            lines.append("  by date, not scoped to the patch (no patch_files): " + _capped(unscoped))
        by_reason: dict[str, list[str]] = {}
        for task, reason in e["unknown"].items():
            by_reason.setdefault(reason, []).append(task)
        lines += [f"  unknown, {reason}: {_capped(tasks)}" for reason, tasks in by_reason.items()]
        # a task another arm flags or cannot vouch for is not clean there; one the check does not read stays compared
        keep = sorted({*e["clean"], *e["not_checked"]} - flagged)
        if keep and (e["by_date"] or e["by_name"]):
            lines.append("  clean: " + " ".join(f"--task {t}" for t in keep))
    return lines


def _capped(items: list[str]) -> str:
    return ", ".join(items[:LIST_CAP]) + (f" and {len(items) - LIST_CAP} more" if len(items) > LIST_CAP else "")

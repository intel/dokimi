# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Review graders: the agent reviews the ``HEAD`` commit and ends its answer with findings as JSON.

The answer's last parseable ```` ```json ```` fenced block must be an array of
findings, each an object ``{file, line, severity, rule, message}`` (``evidence``
is read too). Anything else is ``pass: false`` and ``parse_error``, with
every recall 0 (``null`` where there is nothing to recall): the agent failed. A finding's ``file`` is made
repository-relative: it is cut after ``/eval/run/workspace/`` (the agent's
workspace), then after the last ``/repo/`` (a standalone oneDAL run's
workspace; a relative ``a/repo/b`` becomes ``b`` too, on a seed's or a
thread's file as on a finding's), then leading ``./`` and ``/`` are dropped. ``severity`` ``blocker`` or
``major`` counts toward ``n_severe``.

``review_seeds``: ``manifest.json`` next to ``task.json`` (or ``task.json``
``manifest``)::

  seeds   [{id, class, file, lines, kw}]  known problems. A finding hits a seed if its file matches ("*" = any) and
                                          its rule/message/evidence matches the regex kw (case-insensitive), or
                                          (no kw hit on any seed) its line is within 1 of the seed's lines; the
                                          line fallback credits only the nearest such seed.
  decoys  [{id, file, lines, kw}]         things that look wrong but are fine. A finding that hits no seed and
                                          matches a decoy's kw or one of its lines is a false positive.

A manifest with no seeds is a clean-diff control: ``pass`` is "no blocker or
major finding". Otherwise ``pass`` is null and ``recall`` is the score. The
oracle answer is ``oracle.json`` (or ``task.json`` ``oracle_answer``), a list
of findings, in a fence.

``review_replay``: a merged PR replayed at the revision humans reviewed.
``gold.json`` (or ``task.json`` ``gold``)::

  [{id, file, line, kind, comment, addressed}]          one human thread anchored on a line of the reviewed
                                                        revision's new file; kind is free (correctness, api,
                                                        tests, convention, docs, nit); addressed says whether
                                                        the code changed in response before merge.

A thread is hit by a finding on the same file with ``|finding.line -
gold.line| <= window`` (``task.json`` ``window``, default 5). Matching is one
to one and nearest first, so one finding credits at most one thread and one
thread at most one finding. Location is the only criterion (no judge,
deterministic), so ``pass`` is null: humans miss problems and raise
non-defects, and neither recall nor precision is a verdict. ``matches`` lists
each credited pair in ``threads_hit`` order, so a grade can be checked without
the task: the thread (``thread``, ``thread_file``, ``thread_line``,
``comment``), the finding (``finding``, its index among the answer's objects;
``file`` repository-relative and ``line`` as matched; ``severity``,
``message``) and their ``distance``; strings are cut to 200 characters.
``precision_proxy`` is the share of findings within the window of any thread,
a lower bound on precision. ``recall`` equals ``recall_all`` and
``decoys_flagged`` is ``[]``, so a seeds-style report applies unchanged.
``pass`` is null, so these tasks are scored (``score``), not gated.
The oracle answer is one finding per thread, at its anchor.

Ported from oneDAL ``dev/agent_evals/graders/review.py`` and ``replay.py``
with the same results for the same inputs. Standard library only: this file
runs on the image's ``python3``, which may be old.
"""

from __future__ import annotations

import json
import re

# Bump when a result for the same inputs changes (a metric or ``pass``); grade.json records it. A field added
# to ``details`` that leaves the result as it was (``matches``) does not: a reader tells it by its presence.
VERSION = "1"
SEVERE = ("blocker", "major")


MAX_DEPTH = 100  # json.loads' own limit is the interpreter's (3.14 parses what 3.13 refuses)


def _too_deep(block):
    """True when ``block`` nests arrays/objects deeper than MAX_DEPTH (brackets inside strings ignored)."""
    depth, in_str, esc = 0, False, False
    for ch in block:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                return True
        elif ch in "]}":
            depth -= 1
    return False


def last_json(text):
    """The last parseable ```` ```json ```` fenced block of ``text``, or None."""
    for block in reversed(re.findall(r"```json\s*(.*?)```", text or "", re.S)):
        if _too_deep(block):
            continue
        try:
            return json.loads(block)
        except (ValueError, RecursionError):  # a fence nested too deep is unreadable, like any other
            continue
    return None


def norm_path(p):
    """Repository-relative path of a finding: cut after the workspace, then after the last ``/repo/``."""
    p = str(p or "").strip().replace("\\", "/")
    if "/eval/run/workspace/" in p:  # the agent's workspace under dokimi
        p = p.rsplit("/eval/run/workspace/", 1)[1]
    if "/repo/" in p:  # oneDAL's rule, applied to every path alike so a seed's file and a finding's agree
        p = p.rsplit("/repo/", 1)[1]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def finding_fields(f):
    """``(path, text, line)`` of one finding; an unreadable line is -99, near nothing."""
    path = norm_path(f.get("file"))
    text = " ".join(str(f.get(k, "")) for k in ("rule", "message", "evidence"))
    try:
        line = int(f.get("line") or -99)
    except (TypeError, ValueError, OverflowError):
        line = -99
    return path, text, line


def same_file(entry, path):
    return entry["file"] == "*" or bool(path) and path == norm_path(entry["file"])


def findings_of(answer):
    """The answer's findings (objects only), or None when it has no JSON array."""
    findings = last_json(answer)
    if not isinstance(findings, list):
        return None
    return [f for f in findings if isinstance(f, dict)]


def n_severe(findings):
    return sum(str(f.get("severity", "")).lower() in SEVERE for f in findings)


def _read_json(task_dir, name):
    with open(str(task_dir / name), encoding="utf-8") as fh:
        return json.load(fh)


def _seed_hits(seeds, path, text, line):
    """Seed ids one finding credits: every keyword match, else the nearest seed within one line."""
    matched = [s["id"] for s in seeds if same_file(s, path) and re.search(s["kw"], text, re.I)]
    if matched:
        return matched
    near = [(min(abs(line - x) for x in s["lines"]), s["id"]) for s in seeds
            if same_file(s, path) and s["file"] != "*" and s["lines"]]
    near = [n for n in near if n[0] <= 1]
    return [min(near, key=lambda n: (n[0], _id_key(n[1])))[1]] if near else []


def _id_key(x):
    """Sort key for an id: numbers in numeric order, then strings; a manifest may mix int and str ids."""
    return (isinstance(x, str), x)


def _ids(ids):
    """Ids in a stable order."""
    return sorted(ids, key=_id_key)


def _per_group(entries, key, hit):
    """``{group: "hit/total"}`` over ``entries`` grouped by ``key``, in first-seen order."""
    counts: dict = {}
    for e in entries:
        c = counts.setdefault(e[key], [0, 0])
        c[1] += 1
        c[0] += e["id"] in hit
    return {k: f"{a}/{b}" for k, (a, b) in counts.items()}


def grade_seeds(task, task_dir, answer, run_dir=None):
    """``review_seeds``: the result dict (``pass`` and flat metrics, lists and objects for details)."""
    man = _read_json(task_dir, task.get("manifest", "manifest.json"))
    seeds, decoys = man.get("seeds", []), man.get("decoys", [])
    findings = findings_of(answer)
    if findings is None:  # no findings to score: the run failed, and recall is 0, not unmeasured
        return {"pass": False, "parse_error": True, "recall": 0.0 if seeds else None}
    hit, fp, other = set(), set(), 0
    for f in findings:
        path, text, line = finding_fields(f)
        matched = _seed_hits(seeds, path, text, line)
        if matched:
            hit.update(matched)
            continue
        dec = [d["id"] for d in decoys
               if same_file(d, path) and (re.search(d["kw"], text, re.I) or line in d["lines"])]
        if dec:
            fp.update(dec)
        else:
            other += 1
    severe = n_severe(findings)
    out = {"pass": None, "n_findings": len(findings), "n_severe": severe, "seeds_hit": _ids(hit),
           "decoys_flagged": _ids(fp), "n_decoys": len(decoys), "unmatched": other,
           "recall": round(len(hit) / len(seeds), 3) if seeds else None,
           "recall_by_class": _per_group(seeds, "class", hit)}
    if not seeds:  # clean-diff control: pass = nothing blocking reported
        out["pass"] = severe == 0
    return out


def oracle_seeds(task, task_dir, run_dir=None):
    """The reference answer: ``oracle.json`` (a list of findings) in a fence."""
    with open(str(task_dir / task.get("oracle_answer", "oracle.json")), encoding="utf-8") as fh:
        # ``` can only sit in a JSON string: escape it as oracle_replay does, or it closes the fence
        return "```json\n" + fh.read().replace("```", "\\u0060\\u0060\\u0060") + "\n```"


def _ratio(a, b):
    return round(a / b, 3) if b else None


def _replay_pairs(gold, findings, window):
    """``(pairs, near)``: every (distance, finding index, thread id, thread) within ``window``, and how
    many findings are near any thread."""
    pairs, near = [], 0
    for i, f in enumerate(findings):
        path, _, line = finding_fields(f)
        close = [(abs(line - g["line"]), i, g["id"], g) for g in gold
                 if same_file(g, path) and abs(line - g["line"]) <= window]
        pairs += close
        near += bool(close)
    return pairs, near


CUT = 200  # characters of a comment or message a match keeps


def _cut(value):
    return "" if value is None else str(value)[:CUT]


def _match(g, i, f, distance):
    """One credited (thread, finding) pair of ``details.matches``: enough to check it without the task."""
    path, _, line = finding_fields(f)
    return {"thread": g["id"], "thread_file": g["file"], "thread_line": g["line"], "comment": _cut(g.get("comment")),
            "finding": i, "file": path, "line": line, "severity": _cut(f.get("severity")),
            "message": _cut(f.get("message")), "distance": distance}


def grade_replay(task, task_dir, answer, run_dir=None):
    """``review_replay``: score-only, ``recall`` over the human threads."""
    gold = _read_json(task_dir, task.get("gold", "gold.json"))
    findings = findings_of(answer)
    if findings is None:  # no findings to score: the run failed, and every recall is 0, not unmeasured
        zero = _ratio(0, len(gold))
        return {"pass": False, "parse_error": True, "recall": zero, "recall_all": zero,
                "recall_addressed": _ratio(0, sum(bool(g.get("addressed")) for g in gold))}
    pairs, near = _replay_pairs(gold, findings, task.get("window", 5))
    hit, used, credited = set(), set(), {}
    # nearest first: one finding per thread, one thread per finding; ids in _id_key order, as oneDAL's min()
    for dist, i, gid, g in sorted(pairs, key=lambda p: (p[0], p[1], _id_key(p[2]))):
        if i not in used and gid not in hit:
            used.add(i)
            hit.add(gid)
            credited[gid] = _match(g, i, findings[i], dist)
    addressed = [g for g in gold if g.get("addressed")]
    recall_all = _ratio(len(hit), len(gold))
    return {"pass": None, "n_findings": len(findings), "n_severe": n_severe(findings),
            "threads_hit": _ids(hit), "matches": [credited[gid] for gid in _ids(hit)], "n_threads": len(gold),
            "recall_all": recall_all, "recall": recall_all,
            "recall_addressed": _ratio(sum(g["id"] in hit for g in addressed), len(addressed)),
            "recall_by_kind": _per_group(gold, "kind", hit),
            "precision_proxy": _ratio(near, len(findings)), "decoys_flagged": []}


def oracle_replay(task, task_dir, run_dir=None):
    """The reference answer: one finding per gold thread, at its anchor."""
    gold = _read_json(task_dir, task.get("gold", "gold.json"))
    ans = [{"file": g["file"], "line": g["line"], "severity": "minor", "rule": g.get("kind", ""),
            "message": ((g.get("comment") or "").splitlines() or [""])[0][:200]} for g in gold]
    # a comment's ``` (a GitHub suggestion) would close the fence: JSON-escape it, same decoded text
    return "```json\n" + json.dumps(ans, indent=1).replace("```", "\\u0060\\u0060\\u0060") + "\n```"


GRADERS = {"review_seeds": grade_seeds, "review_replay": grade_replay}
ORACLES = {"review_seeds": oracle_seeds, "review_replay": oracle_replay}

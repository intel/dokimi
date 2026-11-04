# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Built-in grader ``dokimi:graph_enum`` (contract §4.5): an enumeration answer against a gold list.

The task asks for every file, function or class that stands in one relation to a symbol (references it,
derives from it); ``eval tasks generate --from graph`` reads the gold from a code index (§4.7). The
answer's last parseable ```` ```json ```` fence must be an object holding the gold's ``answer_key`` as a
list. Standard library only: it runs on the image's ``python3``.
"""

from __future__ import annotations

import importlib
import json
import os

if __package__:
    from . import review
else:  # a script under -I: main.py put this directory on sys.path
    review = importlib.import_module("review")

# Bump when a result for the same inputs changes (a metric or ``pass``); grade.json records it.
VERSION = "1"
KEYS = ("files", "functions", "classes")
LISTED = 50  # most missed, false-positive and accepted items listed in details


def _str_list(gold, key, required=False):
    value = gold.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value) or (required and not value):
        raise ValueError(f"gold {key} must be a {'non-empty ' if required else ''}list of strings")
    return value


def _read_gold(task, task_dir):
    with open(os.path.join(str(task_dir), task.get("gold", "gold.json")), encoding="utf-8") as fh:
        gold = json.load(fh)
    if not isinstance(gold, dict) or gold.get("answer_key") not in KEYS:
        found = gold.get("answer_key") if isinstance(gold, dict) else gold
        raise ValueError(f"gold answer_key must be one of {', '.join(KEYS)}, not {found!r}")
    for key, required in (("gold", True), ("exclude", False), ("accepted", False)):
        _str_list(gold, key, required)
    return gold


def norm_path(p):
    """A repository-relative path: cut after the agent's workspace, drop ``./`` and a leading ``/``."""
    p = str(p or "").strip().replace("\\", "/")
    if "/eval/run/workspace/" in p:
        p = p.rsplit("/eval/run/workspace/", 1)[1]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def norm_item(item):
    """``path`` or ``path::name``: the path as :func:`norm_path`, the name with ``::`` read as ``.`` and a
    trailing ``()`` dropped (``Impl::run()`` is ``Impl.run``)."""
    path, sep, name = str(item).strip().partition("::")
    name = name.strip().replace("::", ".")
    if name.endswith("()"):
        name = name[:-2]
    return norm_path(path) + sep + name


def _path(item):
    return item.partition("::")[0]


def _last(item):
    path, _, name = item.partition("::")
    return path, name.rsplit(".", 1)[-1]


def _resolve(items, known):
    """Map an answer item to the known item it names: exact, else the one item of ``known`` in that file whose
    last name part is the same (``run`` or ``Other.run`` for ``Impl.run``, when the file has no other ``run``)."""
    by_last: dict = {}
    for k in known:
        if "::" in k:
            by_last.setdefault(_last(k), []).append(k)
    out = set()
    for x in items:
        hits = [] if x in known or "::" not in x else by_last.get(_last(x), [])
        out.add(hits[0] if len(hits) == 1 else x)
    return out


def answer_items(answer, key):
    """The items of the answer's last ```` ```json ```` fence under ``key``, or None when it has none."""
    doc = review.last_json(answer)
    if not isinstance(doc, dict) or not isinstance(doc.get(key), list):
        return None
    return {norm_item(x) for x in doc[key] if isinstance(x, str) and x.strip()}


def grade(task, task_dir, answer, run_dir=None):
    """Score-only: ``f1`` of the answer against the gold; an item in a defining file (``exclude``) is dropped,
    an ``accepted`` one is neither a hit nor a false positive."""
    gold = _read_gold(task, task_dir)
    want = {norm_item(x) for x in gold["gold"]}
    accepted = {norm_item(x) for x in gold.get("accepted", [])} - want
    exclude = {norm_path(p) for p in gold.get("exclude", [])}
    got = answer_items(answer, gold["answer_key"])
    if got is None:  # nothing to score: the run failed, every metric 0
        return {"pass": False, "parse_error": True, "f1": 0.0, "recall": 0.0, "precision": 0.0,
                "n_gold": len(want), "n_answer": 0, "n_false_positive": 0}
    got = _resolve({x for x in got if _path(x) not in exclude}, want | accepted)
    hit, wrong = want & got, got - want - accepted
    recall = len(hit) / len(want)
    precision = len(hit) / (len(hit) + len(wrong)) if hit or wrong else 0.0
    f1 = 2 * precision * recall / (precision + recall) if hit else 0.0
    return {"pass": None, "f1": f1, "recall": recall, "precision": precision, "n_gold": len(want),
            "n_answer": len(got), "n_false_positive": len(wrong),
            "missed": sorted(want - got)[:LISTED], "false_positive": sorted(wrong)[:LISTED],
            "accepted_extra": sorted(got & accepted)[:LISTED]}


def oracle(task, task_dir, run_dir=None):
    """The reference answer: the gold list in a ```` ```json ```` fence."""
    gold = _read_gold(task, task_dir)
    return "```json\n" + json.dumps({gold["answer_key"]: gold["gold"]}, indent=1) + "\n```"


GRADERS = {"graph_enum": grade}
ORACLES = {"graph_enum": oracle}

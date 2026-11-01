# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Eval coverage of a subject's problem space, from files only (issue #331, AC-J07-007).

The problem space is a frozen need graph (``need_graph.v1``): the graph
:func:`dokimi.novelty.needs.build_need_graph` builds from a subject's name and description,
written once and kept next to the suite. Each eval names the nodes it grades in ``needs``:

* a row of a skill-creator ``evals.json``: ``{"id": ..., "needs": ["n1", "n4"]}``;
* a task's ``task.toml`` / ``task.md``: ``metadata.needs``.

:func:`coverage_report` is a pure function of the graph and those labels, so the same files
always give the same report. No model is called here; drafting labels is a separate, paid step.

Relevance of one eval: ``relevant_core`` (names a core or rare_critical node),
``relevant_peripheral`` (only branch or supporting nodes), ``out_of_scope`` (only nodes the
graph marks out of scope), ``orphan`` (``needs: []``: it grades nothing the subject is for)
and ``unlabelled`` (no ``needs`` at all: not yet mapped, so it covers nothing).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dokimi.artifacts import load_artifact
from dokimi.harnesses.tasks import is_task_dir, read_task_config
from dokimi.novelty.needs import PATH_WEIGHTS

CRITICAL = ("core", "rare_critical")
CLASSES = ("core", "rare_critical", "branch", "supporting", "out_of_scope")


@dataclass(frozen=True)
class LabelledEval:
    """One eval and the need-node ids it says it grades (``None``: it has no ``needs``)."""

    id: str
    source: str
    needs: tuple[str, ...] | None


def load_graph(path: Path) -> dict:
    """Read and validate a ``need_graph.v1`` file (an unstamped ``save_graph`` file is valid)."""
    return load_artifact("need_graph", Path(path))


def graph_digest(graph: dict) -> str:
    """sha256 of the nodes in canonical JSON: the identity a report and a plan can record."""
    nodes = sorted(graph["nodes"], key=lambda n: n["id"])
    blob = json.dumps(nodes, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _needs(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):  # one id, not a sequence of letters
        return (value,)
    return tuple(str(v) for v in value) if isinstance(value, list) else None


def _evals_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [p for p in (root / "evals.json", root / "evals" / "evals.json") if p.is_file()]


def read_labels(root: Path) -> list[LabelledEval]:
    """Every eval under ``root`` with its ``needs``: skill-creator ``evals.json`` rows, then tasks.

    ``root`` is an ``evals.json`` file, a skill directory (its ``evals/evals.json``) or a tasks
    root whose child directories are tasks. Raises ``ValueError`` on an unreadable ``evals.json``.
    """
    out: list[LabelledEval] = []
    for path in _evals_files(root):
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("evals", [])
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError(f"{path}: not a skill-creator evals.json ({exc})") from exc
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if isinstance(row, dict):
                eid = str(row.get("id", index))
                out.append(LabelledEval(eid, path.name, _needs(row.get("needs"))))
    if root.is_dir():
        for task_dir in sorted(p for p in root.iterdir() if is_task_dir(p)):
            try:
                meta = read_task_config(task_dir).get("metadata", {})
            except (ValueError, OSError):
                continue
            needs = _needs(meta.get("needs")) if isinstance(meta, dict) else None
            out.append(LabelledEval(task_dir.name, "task", needs))
    return out


def _relevance(needs: tuple[str, ...] | None, classes: dict[str, str]) -> str:
    if needs is None:
        return "unlabelled"
    named = [classes[n] for n in needs if n in classes]
    if not named:
        return "orphan"
    if any(c in CRITICAL for c in named):
        return "relevant_core"
    if any(c != "out_of_scope" for c in named):
        return "relevant_peripheral"
    return "out_of_scope"


def _share(nodes: list[dict], covered: set[str]) -> float | None:
    return round(sum(n["id"] in covered for n in nodes) / len(nodes), 4) if nodes else None


def coverage_report(graph: dict, evals: list[LabelledEval], evals_root: str) -> dict:
    """The ``eval_coverage.v1`` payload (unstamped) for ``graph`` and ``evals``."""
    classes = {n["id"]: n["path_class"] for n in graph["nodes"]}
    problems = [f"{e.id}: unknown need id {n!r}"
                for e in evals for n in (e.needs or ()) if n not in classes]
    covered = {n for e in evals for n in (e.needs or ()) if n in classes}
    scope = [n for n in graph["nodes"] if n["path_class"] != "out_of_scope"]
    weight = sum(PATH_WEIGHTS[n["path_class"]] for n in scope)
    critical = [n for n in scope if n["path_class"] in CRITICAL]
    return {
        "evals_root": evals_root,
        "graph": {
            "artifact_name": graph["artifact_name"],
            "digest": graph_digest(graph),
            "nodes": len(graph["nodes"]),
            "in_scope": len(scope),
            "hypotheses": sum(n.get("confirmed_by", "hypothesis") == "hypothesis"
                              for n in graph["nodes"]),
        },
        "coverage": {
            "weighted": round(sum(PATH_WEIGHTS[n["path_class"]] for n in scope
                                  if n["id"] in covered) / weight, 4) if weight else None,
            "core_rare_critical": _share(critical, covered),
            "rare_critical": _share([n for n in scope if n["path_class"] == "rare_critical"], covered),
            "by_class": {c: {"covered": sum(n["id"] in covered for n in graph["nodes"]
                                            if n["path_class"] == c),
                             "total": sum(n["path_class"] == c for n in graph["nodes"])}
                         for c in CLASSES},
        },
        "gaps": [{"id": n["id"], "path_class": n["path_class"], "text": n["text"],
                  "confirmed_by": n.get("confirmed_by", "hypothesis")}
                 for n in critical if n["id"] not in covered],
        "evals": [{"id": e.id, "source": e.source, "needs": list(e.needs or ()),
                   "relevance": _relevance(e.needs, classes)} for e in evals],
        "problems": problems,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(report: dict) -> str:
    """The report as Markdown, for a suite's README or the PR that changes the suite."""
    g, c = report["graph"], report["coverage"]
    lines = [
        f"## Eval coverage: {g['artifact_name']} (graph {g['digest'][:12]})",
        "",
        f"Weighted coverage {_pct(c['weighted'])}; core + rare_critical "
        f"{_pct(c['core_rare_critical'])}; rare_critical {_pct(c['rare_critical'])}. "
        f"{g['hypotheses']} of {g['nodes']} nodes are unconfirmed hypotheses.",
        "",
        "| Eval | Source | Needs | Relevance |",
        "|---|---|---|---|",
    ]
    lines += [f"| {e['id']} | {e['source']} | {', '.join(e['needs']) or '-'} | {e['relevance']} |"
              for e in report["evals"]]
    lines += ["", "Gaps (core and rare_critical nodes no eval names):"]
    lines += [f"- {n['id']} [{n['path_class']}, {n['confirmed_by']}] {n['text']}"
              for n in report["gaps"]] or ["- none"]
    if report["problems"]:
        lines += ["", "Problems:"] + [f"- {p}" for p in report["problems"]]
    return "\n".join(lines) + "\n"

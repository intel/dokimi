# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Build the need graph from *outside* the artifact, and match atoms into it.

The rule this module exists to enforce: **an artifact must not define both the problem
scope and its own relevance to it.** :func:`build_need_graph` takes a name and a
description and nothing else. It never receives the body, so an artifact cannot make
itself relevant by asserting what matters.

That constraint is what produces a category similarity cannot express. In the study 100
of 1076 nodes were deliberately labelled ``out_of_scope``, and 1.3% of atoms best-matched
one of them — text answering a question the artifact's own task does not pose. The graph
is also not a dressed-up embedding ranking: correlation between graph-derived relevance
and top retrieval similarity was 0.368, and the two rankings disagreed on 57% of the top
quartile.

Path classes carry the relevance weights, and ``rare_critical`` is weighted at the top
on purpose: those needs are the ones whose value is conditional on a failure, which is
exactly what a value-ranking shortener discards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .atoms import Atom
from .client import LLMClient
from .retention import lexical_overlap

NODE_TYPES = ("intent", "evidence", "action", "constraint", "decision")
PATH_CLASSES = ("core", "branch", "rare_critical", "supporting", "out_of_scope")

# Relevance weight per path class. `out_of_scope` is 0.0 by definition: an atom matching
# only there is not relevant, however well-written it is.
PATH_WEIGHTS = {
    "core": 1.0,
    "rare_critical": 1.0,
    "branch": 0.7,
    "supporting": 0.4,
    "out_of_scope": 0.0,
}

_GRAPH_SYSTEM = (
    "You model what a practitioner needs in order to finish a task. You work only from "
    "the task's name and one-line description; you do not have, and must not assume, any "
    "document that answers it."
)

_GRAPH_PROMPT = """\
TASK NAME: {name}
TASK DESCRIPTION: {description}

Model what someone must know, check, decide or avoid to finish this task. Work only from
the name and description above.

Include some nodes that a document on this topic might plausibly cover but that this
task does *not* require — label those "out_of_scope". They are the negative controls.

node types: {types}
path classes: {classes}
  - core: needed on the normal path
  - branch: needed on a common variant
  - rare_critical: rarely needed, expensive to get wrong
  - supporting: nice to have
  - out_of_scope: not required by this task

Return ONLY JSON:
{{"nodes": [{{"id": "n1", "text": "...", "type": "intent", "path_class": "core"}}],
  "edges": [{{"from": "n1", "to": "n2", "kind": "requires"}}]}}
"""


@dataclass
class NeedNode:
    """One thing the task requires (or explicitly does not)."""

    id: str
    text: str
    type: str = "intent"
    path_class: str = "core"
    confirmed_by: str = "hypothesis"  # "user" once a person confirmed it (need_graph.v1)

    @property
    def weight(self) -> float:
        """Relevance weight of this node's path class."""
        return PATH_WEIGHTS.get(self.path_class, 0.4)

    def as_dict(self) -> dict:
        """Serializable form."""
        return {"id": self.id, "text": self.text, "type": self.type,
                "path_class": self.path_class, "confirmed_by": self.confirmed_by}


_NODE_FIELDS = frozenset(NeedNode.__dataclass_fields__)


@dataclass
class NeedGraph:
    """The independently-built need model for one artifact."""

    artifact_name: str
    description: str
    nodes: list[NeedNode] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def task_statement(self) -> str:
        """A task description for the value judge, built without the artifact body."""
        core = [n.text for n in self.nodes if n.path_class in ("core", "rare_critical")]
        head = f"{self.artifact_name}: {self.description}"
        return head if not core else head + "\nRequired along the way:\n- " + "\n- ".join(core[:25])

    def summary(self) -> dict:
        """Node/edge statistics for the artifact."""
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "by_type": {t: sum(1 for n in self.nodes if n.type == t) for t in NODE_TYPES},
            "by_path_class": {
                c: sum(1 for n in self.nodes if n.path_class == c) for c in PATH_CLASSES
            },
        }

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "artifact_name": self.artifact_name,
            "description": self.description,
            "nodes": [n.as_dict() for n in self.nodes],
            "edges": list(self.edges),
            **({"provenance": dict(self.provenance)} if self.provenance else {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NeedGraph":
        """Rebuild a graph from :meth:`as_dict` output."""
        return cls(
            artifact_name=data.get("artifact_name", ""),
            description=data.get("description", ""),
            nodes=[NeedNode(**{k: v for k, v in row.items() if k in _NODE_FIELDS})
                   for row in data.get("nodes", [])],
            edges=list(data.get("edges", [])),
            provenance=dict(data.get("provenance") or {}),
        )


def build_need_graph(name: str, description: str, *, client: LLMClient) -> NeedGraph:
    """Build a need graph from an artifact's name and description **only**.

    The signature is the guarantee: there is no parameter through which the body could
    reach the graph author.
    """
    graph = NeedGraph(artifact_name=name, description=description)
    try:
        data = client.json_object(
            _GRAPH_PROMPT.format(
                name=name, description=description,
                types=", ".join(NODE_TYPES), classes=", ".join(PATH_CLASSES),
            ),
            system=_GRAPH_SYSTEM,
            usable=_has_a_node,
        )
    except Exception:
        return graph
    for index, row in enumerate(data.get("nodes", []) or [], start=1):
        if not isinstance(row, dict) or not str(row.get("text", "")).strip():
            continue
        node_type = str(row.get("type", "intent"))
        path_class = str(row.get("path_class", "core"))
        graph.nodes.append(NeedNode(
            id=str(row.get("id") or f"n{index}"),
            text=str(row["text"]).strip(),
            type=node_type if node_type in NODE_TYPES else "intent",
            path_class=path_class if path_class in PATH_CLASSES else "supporting",
        ))
    known = {node.id for node in graph.nodes}
    graph.edges = [
        {"from": str(e.get("from")), "to": str(e.get("to")), "kind": str(e.get("kind", "requires"))}
        for e in data.get("edges", []) or []
        if isinstance(e, dict) and str(e.get("from")) in known and str(e.get("to")) in known
    ]
    return graph


def _has_a_node(reply: dict) -> bool:
    """Cache only a graph with a node in it: an object with none builds an empty graph."""
    nodes = reply.get("nodes")
    return isinstance(nodes, list) and any(
        isinstance(row, dict) and str(row.get("text", "")).strip() for row in nodes
    )


def match_atoms(atoms: list[Atom], graph: NeedGraph) -> dict[str, dict]:
    """Match each atom to its best-fitting need node.

    Lexical matching is used deliberately: the graph's *labels* carry the relevance
    signal, and the failure mode that lexical matching cannot see — reversal — is not
    reachable here, because a node is a need, not a claim to contradict. Atoms whose best
    match is ``out_of_scope`` are reported as such rather than dropped.
    """
    out: dict[str, dict] = {}
    for atom in atoms:
        best: tuple[float, NeedNode | None] = (0.0, None)
        for node in graph.nodes:
            score = max(
                lexical_overlap(node.text, atom.statement),
                lexical_overlap(atom.statement, node.text),
            )
            if score > best[0]:
                best = (score, node)
        score, match = best
        out[atom.id] = {
            "node_id": match.id if match else None,
            "path_class": match.path_class if match else None,
            "score": round(score, 4),
            "relevance": round(score * match.weight, 4) if match else 0.0,
            "out_of_scope": bool(match and match.path_class == "out_of_scope"),
        }
    return out


def relevance_summary(matches: dict[str, dict]) -> dict:
    """Aggregate the atom→need matching."""
    total = len(matches)
    oos = sum(1 for row in matches.values() if row["out_of_scope"])
    unmatched = sum(1 for row in matches.values() if row["node_id"] is None)
    return {
        "n_atoms_matched": total - unmatched,
        "n_atoms_unmatched": unmatched,
        "out_of_scope_atoms": oos,
        "frac_out_of_scope": round(oos / total, 4) if total else 0.0,
        "mean_relevance": round(
            sum(row["relevance"] for row in matches.values()) / total, 4
        ) if total else 0.0,
    }


def save_graph(graph: NeedGraph, path) -> None:
    """Write a need graph so later runs reuse it instead of paying for it again."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph.as_dict(), indent=2), encoding="utf-8")


def load_graph(path) -> NeedGraph:
    """Read a need graph written by :func:`save_graph`."""
    return NeedGraph.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

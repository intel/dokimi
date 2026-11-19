# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The per-atom verdicts of one ``novelty`` run, saved beside it as ``<stem>.atom_verdicts.json``.

``novelty.json`` keeps totals (``atoms.faithfulness``, ``categories``, ``paired``); a reader who
wants to check the grader on one atom needs that atom's own statement, judged axes, measured
outcome and category. This module builds the ``novelty_atoms`` record from an
:class:`~dokimi.novelty.analyze.AnalyzeResult` and writes it; ``novelty.json`` is not changed.
"""

from __future__ import annotations

from pathlib import Path


def build_atom_verdicts(result) -> dict:
    """The ``novelty_atoms`` payload: one row per atom the run probed (``result.atoms``).

    The judged fields are ``None`` for an atom with no verdict (``--no-classify``, a tier without
    classification, or an atom the value judge did not answer for), ``outcome`` is ``None`` under
    a scoring with no usable pair for the atom, and ``category`` is ``None`` without a verdict.
    """
    rows = []
    for atom in result.atoms:
        verdict = result.verdicts.get(atom.id)
        rows.append({
            **{key: value for key, value in atom.as_dict().items() if key != "meta"},
            "needed": verdict.needed if verdict else None,
            "scaffolding": verdict.scaffolding if verdict else None,
            "conflict": verdict.conflict if verdict else None,
            "conflict_why": verdict.conflict_why if verdict else None,
            "outcome": {
                "lenient": result.outcomes.get(atom.id),
                "strict": result.strict_outcomes.get(atom.id),
            },
            "category": result.categories.get(atom.id),
        })
    return {
        "artifact_ref": result.artifact["artifact"]["ref"],
        "model": result.artifact["model"],
        "with_arm": result.measure_result.with_arm,
        "conflicts": [dict(pair) for pair in result.conflicts],
        "atoms": rows,
    }


def save_atom_verdicts(result, out: Path) -> Path:
    """Write the verdicts beside ``out`` (the ``novelty.json`` path), replacing any earlier file."""
    from dokimi.artifacts import save_artifact

    dest = out.with_name(f"{out.stem}.atom_verdicts.json")
    save_artifact("novelty_atoms", build_atom_verdicts(result), dest)
    return dest

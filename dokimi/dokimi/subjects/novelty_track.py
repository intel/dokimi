# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Summarize ``novelty.v1`` artifacts into the scorecard's ``novelty{}`` block.

One row per ``(artifact, model)`` cell, and **never an average over cells**. Novelty is a property
of a pair: a strong model has both a smaller `added` and less headroom, so pooling cells mixes two
effects, and ranking artifacts by `added` inside one model correlated with the measured task delta
at -0.446 over 13 artifacts. The block therefore carries cells, the gate verdict for each, and the
caveats — not a subject-level novelty score. There is deliberately nothing here to sort by.

Novelty analysis is not run as part of a subject run: the cost is
``atoms x probes x arms x (answer + grade)`` and the study's sweep cost 11561 gradings. Artifacts
are produced by ``novelty analyze`` and attached to a subject run by path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dokimi.artifacts import load_artifact
from dokimi.gate.novelty_gate import evaluate_novelty

# What the scorecard keeps per cell. Everything here is either a measured rate with its n, or a
# provenance field; the wide tables (atoms, spans, candidates) stay in the novelty artifact.
CAVEATS = (
    "novelty is per (artifact, model) and not comparable across cells",
    "`added` is a profile, not a ranking: ranking artifacts by it inside one model correlated "
    "with the measured task delta at -0.446",
    "every novelty check is soft and none blocks a build",
)


def novelty_cell(artifact: dict, *, source: str | None = None) -> dict[str, Any]:
    """Compact one novelty artifact into one scorecard cell."""
    paired = artifact.get("paired", {}) or {}
    spans = artifact.get("spans", {}) or {}
    atoms = artifact.get("atoms", {}) or {}
    verdict = evaluate_novelty(artifact)
    cell: dict[str, Any] = {
        "artifact": dict(artifact.get("artifact") or {}),
        "model": artifact.get("model"),
        "provider": artifact.get("provider"),
        "judge_model": artifact.get("judge_model"),
        "atoms": {
            "n": atoms.get("n"),
            "n_probed": atoms.get("n_probed"),
            "sampling": atoms.get("sampling"),
        },
        "paired": {
            scoring: {
                key: (paired.get(scoring) or {}).get(key)
                for key in ("added", "redundant", "degraded", "still_missing",
                            "n_paired", "ci95_added")
            }
            for scoring in ("lenient", "strict")
            if paired.get(scoring)
        },
        "k0": dict(artifact.get("k0") or {}),
        "spans": {
            "n": spans.get("n"),
            "tokens": spans.get("tokens"),
            "frac_carrying_nothing": spans.get("frac_carrying_nothing"),
            "capped_atom_set": spans.get("capped_atom_set"),
            # The fraction is over the read text only, so the unread part rides along.
            "n_unread": spans.get("n_unread"),
            "tokens_unread": spans.get("tokens_unread"),
        },
        "gate": verdict.as_dict(),
        # The artifact's own warnings ride along: a cell that skipped a stage must not read as a
        # cell that cleared it once the scorecard is the thing being read.
        "warnings": list(artifact.get("warnings") or []),
    }
    if artifact.get("run_config"):
        cell["run_config"] = dict(artifact["run_config"])
    if source:
        cell["source"] = source
    return cell


def build_novelty_block(
    artifacts: list[dict], *, sources: list[str] | None = None
) -> dict[str, Any]:
    """Build the scorecard's ``novelty{}`` block from already-produced novelty artifacts."""
    sources = sources or []
    cells = [
        novelty_cell(artifact, source=sources[i] if i < len(sources) else None)
        for i, artifact in enumerate(artifacts)
    ]
    return {
        "status": "measured" if cells else "not_run",
        "n_cells": len(cells),
        "cells": cells,
        "caveats": list(CAVEATS),
    }


def load_novelty_artifacts(paths: list[str] | list[Path]) -> tuple[list[dict], list[str]]:
    """Load and validate novelty artifacts, returning them with their source paths.

    Validation is not optional here: a subject scorecard that embeds an unvalidated novelty payload
    would launder a malformed artifact into a versioned one.
    """
    loaded: list[dict] = []
    sources: list[str] = []
    for path in paths:
        loaded.append(load_artifact("novelty", Path(path)))
        sources.append(str(path))
    return loaded, sources


def novelty_warnings(block: dict) -> list[str]:
    """Subject-level warnings a novelty block implies, for the scorecard's warning list."""
    out: list[str] = []
    for cell in block.get("cells", []):
        ref = (cell.get("artifact") or {}).get("ref", "?")
        model = cell.get("model", "?")
        out.extend(f"novelty [{ref} on {model}]: {warning}" for warning in cell.get("warnings", []))
        for check in cell.get("gate", {}).get("checks", []):
            if not check.get("passed") and check.get("severity") == "warn":
                out.append(f"novelty [{ref} on {model}] {check['name']}: {check['message']}")
    return out

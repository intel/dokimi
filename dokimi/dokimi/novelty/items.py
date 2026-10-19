# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Item analysis over the probes: which of them measure anything.

The novelty pipeline treats every probe as equally informative. Two LLM-free checks say
whether that holds, and they answer different questions:

* **Kind agreement**, inside one run. An atom asked twice — its own question and a
  reworded one — should get the same verdict in the same arm. When it does not, that
  atom's result is wording-dependent: a `redundant` that survives only the artifact's own
  phrasing is not prior knowledge, and a `added` that appears only under one wording may
  be a bad wording rather than new knowledge. Reversal probes are excluded, because their
  answer key is "refuse", so disagreeing with the other kinds is their job.
* **Discrimination**, across runs. Corrected (rest-score) point-biserial: an atom that every model
  gets right (or wrong) separates nothing, and one the *weaker* models get right more
  often than the strong ones is measuring something other than the knowledge it names.
  This needs several models' probe runs over the *same* atom set — one run cannot produce
  it, and the module says so rather than computing a number from one respondent.

Neither check is a correctness judgement on the atom. A non-discriminating atom can be
perfectly true and simply universal; the output is a list of atoms whose *measurement* is
uninformative, which is where an author's next probe budget should not go.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dokimi.eval.stats import point_biserial

from .measure import ProbeRun

#: Below this, an atom's pass/fail tracks the models' overall standing weakly enough that
#: the atom is reported as non-discriminating. 0.2 is the conventional item-analysis floor
#: and is not measured on this data -- it is a flag, not a threshold with evidence behind it.
MIN_DISCRIMINATION = 0.2

#: Respondents below which the 0.2 floor is not applied at all. Enumerating every 3x6
#: pass matrix under a uniform null puts 30% of atom-cells at r >= 0.2 and 32% at r < 0,
#: and the attainable values are the lumpy set {0, 0.189, 0.5, 0.756, 0.866, 0.945, 1.0} --
#: so at three respondents a 0.2 cut flags a third of atoms by chance and cannot be hit
#: exactly. Below this many cells the rows are reported without being counted against it.
MIN_CELLS_FOR_FLOOR = 12

#: Fewer respondents than this and the correlation is undefined, not merely noisy.
MIN_RESPONDENTS = 3


def kind_agreement(
    runs: list[ProbeRun], *, arm: str, scoring: str = "lenient"
) -> dict:
    """Do an atom's probe kinds agree with each other inside one arm?

    The reported figure is the share of multi-kind *atoms* whose kinds were unanimous, not
    a per-atom share of agreeing kinds: an atom asked three ways where two agree counts as
    a disagreement, because one of its three answers is wrong whichever way it is read.

    Args:
        runs: All probe runs from one measurement.
        arm: Which arm to check. The artifact arm is the interesting one; the baseline arm
            answers the complementary question (is the prior knowledge wording-dependent?).
        scoring: ``lenient`` or ``strict``.

    Returns:
        ``{"arm", "scoring", "n_atoms_multi_kind", "n_agree", "agreement", "disagreeing"}``.
        ``agreement`` is ``None`` when no atom was asked by more than one non-reversal kind,
        which is the default-run case with ``--no-paraphrase``.
    """
    by_atom: dict[str, dict[str, bool]] = {}
    for run in runs:
        if run.arm != arm or run.probe_kind == "reversal" or run.errored:
            continue
        kinds = by_atom.setdefault(run.atom_id, {})
        # Worst-first on duplicates, as `discrimination` folds them: last-write-wins made
        # the verdict depend on the order the runs happened to arrive in.
        correct = run.correct(scoring)
        kinds[run.probe_kind] = kinds.get(run.probe_kind, True) and correct

    multi = {atom: kinds for atom, kinds in by_atom.items() if len(kinds) > 1}
    disagreeing = sorted(
        (
            {"atom_id": atom, "by_kind": dict(sorted(kinds.items()))}
            for atom, kinds in multi.items() if len(set(kinds.values())) > 1
        ),
        key=lambda row: str(row["atom_id"]),
    )
    n_agree = len(multi) - len(disagreeing)
    return {
        "arm": arm,
        "scoring": scoring,
        "n_atoms_multi_kind": len(multi),
        "n_agree": n_agree,
        "agreement": round(n_agree / len(multi), 4) if multi else None,
        "disagreeing": disagreeing,
    }


def discrimination(cells: list[dict], *, scoring: str = "lenient", arm: str = "full") -> dict:
    """Point-biserial per atom across models, with the models as the respondents.

    Args:
        cells: One entry per run: ``{"model": str, "runs": list[ProbeRun]}``. Cells of one
            model are folded into one respondent, worst-first per atom. Every cell
            must cover the same atom set; atoms missing from a cell are dropped from that
            atom's statistic and counted, because silently treating a missing probe as a
            failure would manufacture discrimination.
        scoring: ``lenient`` or ``strict``.
        arm: The artifact arm to score. Only this arm counts: the baseline and the
            ``placebo``/``shuffled`` controls are expected to fail atoms the artifact
            carries, so folding them in would mark every `added` atom as failed.

    Returns:
        A summary with ``per_atom`` rows (``atom_id``, ``r``, ``n``, ``n_pass``,
        ``reason``), the counts of non-discriminating and negatively discriminating atoms,
        and ``measured`` ``False`` with a reason when fewer than three models were given.
    """
    models = [cell["model"] for cell in cells]
    distinct = sorted(set(models))
    if len(cells) < MIN_RESPONDENTS or len(distinct) < MIN_RESPONDENTS:
        return {
            "measured": False, "scoring": scoring, "arm": arm, "models": models,
            "reason": (
                f"{len(cells)} cell(s) over {len(distinct)} distinct model(s): "
                f"discrimination needs at least {MIN_RESPONDENTS} *models* as respondents. "
                "Repeated runs of one model are repeated measurements of the same "
                "respondent, not more respondents"
            ),
            "per_atom": [], "n_atoms": 0, "n_non_discriminating": 0, "n_undefined": 0,
            "n_negative": 0,
        }

    # Fold repeated cells of one model into one respondent before anything is counted.
    # Counting distinct models over all cells and correlating over the usable ones let
    # A, B, C, A with C unusable pass as three respondents when only two models were left.
    runs_by_model: dict[str, list] = {}
    for cell in cells:
        runs_by_model.setdefault(cell["model"], []).extend(cell["runs"])

    correct_by_model: list[dict[str, bool]] = []
    for model_runs in runs_by_model.values():
        per_atom: dict[str, bool] = {}
        for run in model_runs:
            if run.arm != arm or run.probe_kind == "reversal" or run.errored:
                continue
            # Worst-first inside an atom: a kind the model failed means it failed the atom.
            per_atom[run.atom_id] = per_atom.get(run.atom_id, True) and run.correct(scoring)
        correct_by_model.append(per_atom)

    usable = [per_atom for per_atom in correct_by_model if per_atom]
    if len(usable) < MIN_RESPONDENTS:
        return {
            "measured": False, "scoring": scoring, "arm": arm, "models": models,
            "reason": (
                f"{len(usable)} of {len(distinct)} models carry usable runs in arm `{arm}`: "
                f"point-biserial discrimination needs at least {MIN_RESPONDENTS} "
                "respondents, so this is not computable from these runs"
            ),
            "per_atom": [], "n_atoms": 0, "n_non_discriminating": 0, "n_undefined": 0,
            "n_negative": 0,
        }

    # Only atoms every usable cell scored. An atom present in one cell cannot be
    # correlated, and -- because the rest score is computed over the atoms a cell has --
    # letting cells disagree about the atom set moved the shared atoms' totals too, which
    # flipped their sign.
    common = set(usable[0]).intersection(*usable[1:])
    dropped = sorted(set().union(*usable) - common)

    rows: list[dict] = []
    for atom_id in sorted(common):
        flags, used_totals = [], []
        for per_atom in usable:
            rest = [per_atom[other] for other in common if other != atom_id]
            if not rest:
                continue      # nothing left to correlate against; never scored as 0.0
            flags.append(per_atom[atom_id])
            used_totals.append(sum(1 for ok in rest if ok) / len(rest))
        if not flags:
            rows.append({"atom_id": atom_id, "r": None, "n": 0, "n_pass": 0,
                         "reason": "no other atom was scored by every cell, so there is "
                                   "no rest score to correlate against"})
            continue
        rows.append({"atom_id": atom_id, **point_biserial(flags, used_totals)})

    scored = [row for row in rows if row["r"] is not None]
    undefined = [row for row in rows if row["r"] is None]
    negative = [row for row in scored if row["r"] < 0]
    floor_applies = len(usable) >= MIN_CELLS_FOR_FLOOR
    below_floor = [row for row in scored if row["r"] < MIN_DISCRIMINATION]
    return {
        "measured": True,
        "scoring": scoring,
        "arm": arm,
        "models": models,
        "n_respondents": len(usable),
        "min_discrimination": MIN_DISCRIMINATION if floor_applies else None,
        "floor_applies": floor_applies,
        "n_atoms": len(rows),
        "n_atoms_dropped": len(dropped),
        "atoms_dropped": dropped[:20],
        "n_undefined": len(undefined),
        "n_non_discriminating": len(below_floor) if floor_applies else 0,
        "n_negative": len(negative),
        "per_atom": sorted(scored, key=lambda row: row["r"]) + undefined,
        "statistic": "corrected_item_total_point_biserial",
        "note": (
            "Discrimination describes the measurement, not the atom: an atom every model "
            "knows separates nothing and is still true. Each r is against the models' "
            "scores on the other atoms, over the atoms every cell scored. "
            + (
                f"With {len(usable)} respondents the {MIN_DISCRIMINATION} floor is applied "
                "and a negative r is the one to read as a defect."
                if floor_applies else
                f"With {len(usable)} respondents the floor is NOT applied: under a uniform "
                "null, 30% of atoms clear 0.2 and 32% come out negative by chance at three "
                "cells, and the attainable values are a lumpy set that 0.2 does not even "
                f"land on. Read the r column as a ranking and gather {MIN_CELLS_FOR_FLOOR} "
                "cells before treating any single value as a finding."
            )
        ),
    }


class ProbeFileError(ValueError):
    """A ``novelty probe`` output that item analysis cannot use, with the reason."""


def load_probe_cells(paths: list[str]) -> tuple[list[dict], list[str]]:
    """Read ``novelty probe`` outputs into item-analysis cells, one per file.

    Correlating atoms across models only means something over one answer key and one
    probed text. Atom ids are content hashes, so ids collide across artifacts only for
    identical statements -- but then the "same atom" was asked with different context. A
    file that does not record ``artifact`` or ``ref_digest`` (older output) is not held
    against the rest.

    Returns:
        ``(cells, warnings)``; each cell is ``{"model", "runs", "source", "artifact",
        "ref_digest"}`` as :func:`discrimination` reads it.

    Raises:
        ProbeFileError: A file that is not probe output, or files that measured different
            texts. A file that cannot be read or is not JSON raises as reading it does.
    """
    cells = []
    for raw in paths:
        data = json.loads(Path(raw).read_text(encoding="utf-8"))
        try:
            runs = [
                ProbeRun(
                    atom_id=row["atom_id"], probe_kind=row["probe_kind"], arm=row["arm"],
                    question=row.get("question", ""), response=row.get("response", ""),
                    verdict=row["verdict"], confidence=row.get("confidence"),
                )
                for row in data.get("runs", [])
            ]
        except (KeyError, TypeError) as exc:
            raise ProbeFileError(
                f"{raw} has a probe run missing {exc}; it is not `novelty probe` output"
            ) from None
        if not runs:
            raise ProbeFileError(f"{raw} carries no probe runs; item analysis needs `novelty "
                                 "probe` output, not a novelty.json")
        cells.append({"model": data.get("model", raw), "runs": runs, "source": raw,
                      "artifact": data.get("artifact"), "ref_digest": data.get("ref_digest")})

    for name in ("artifact", "ref_digest"):
        values = {cell[name] for cell in cells if cell[name] is not None}
        if len(values) > 1:
            raise ProbeFileError(
                f"these probe runs measured different texts ({name}: "
                f"{', '.join(sorted(values))}); item analysis correlates one artifact's "
                "atoms across models, so pass runs of one artifact at one revision"
            )

    warnings = []
    if len({frozenset(run.atom_id for run in cell["runs"]) for cell in cells}) > 1:
        warnings.append("these runs do not cover the same atom set; an atom missing from a "
                        "cell is dropped from its own statistic rather than counted as a "
                        "failure")
    return cells, warnings


def item_analysis(cells: list[dict], *, arm: str = "full") -> dict[str, Any]:
    """The ``novelty items`` record over cells from :func:`load_probe_cells`.

    ``kind_agreement`` is keyed by source file, not by model: two runs of the same model
    (two seeds, say) must not overwrite each other.
    """
    return {
        "cells": [{"model": cell["model"], "source": cell["source"],
                   "n_runs": len(cell["runs"])} for cell in cells],
        "discrimination": {
            scoring: discrimination(cells, scoring=scoring, arm=arm)
            for scoring in ("lenient", "strict")
        },
        "kind_agreement": {
            cell["source"]: dict(kind_agreement(cell["runs"], arm=arm), model=cell["model"])
            for cell in cells
        },
    }

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What ``skill novelty analyze`` keeps when ``--max-usd`` (or a call with no price) stops it.

:func:`stopped_record` builds a partial ``novelty.v1`` record from the stages that finished: the
extracted atoms and the spans always, the probe set once it was built, and the probe answers and
judgments made so far. It holds none of the summaries over the runs (``paired``, ``arms``, the
views): the cut leaves the arms uneven, so they would read as results next to ``status: partial``.
The record opens with ``status``, so ``runs`` does not list it as a finished run, and every reader
of a finished ``novelty.json`` refuses it (:func:`dokimi.artifacts.load_artifact`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .atoms import ExtractionReport
from .client import SpendCapReached, roll_up_cost
from .measure import MeasureResult
from .probes import ProbeSet

#: Where the cut fell, as the partial record names it, and how the message says it.
STAGES = {
    "extraction": "extraction",
    "verify": "the faithfulness check",
    "need_graph": "the need graph",
    "probes": "the probe wordings",
    "measurement": "the probes",
    "classify": "the content classification",
    "attribution": "the span attribution and shortening",
}


@dataclass
class Progress:
    """What an analysis had produced when it stopped; filled in by ``analyze`` as it goes."""

    stage: str = "extraction"
    spans: list = field(default_factory=list)
    extraction: ExtractionReport | None = None
    extracted: list = field(default_factory=list)
    atoms: list = field(default_factory=list)
    sampling: str = "none"  # until the run has chosen the atoms it probes
    run_config: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    faithfulness: dict | None = None
    probe_set: ProbeSet | None = None
    measure_result: MeasureResult | None = None


def stop_block(progress: Progress, stop: SpendCapReached) -> dict:
    """The ``partial`` block: ``reason``, the ``stage`` reached, ``calls_done`` / ``calls_planned``
    where the stage counts calls (the probes) or the spans read (extraction), and ``message``."""
    extraction = progress.extraction
    if progress.stage == "extraction" and extraction is not None and extraction.partial:
        return dict(extraction.partial)  # spans_read / spans_total, as `atoms` records them
    stage = STAGES[progress.stage]
    block: dict = {"reason": stop.reason, "stage": progress.stage}
    result = progress.measure_result
    where = stage
    if result is not None:  # the measurement started: its answers are kept, whatever stage stopped
        done = len(result.runs)
        block.update(calls_done=done, calls_planned=done + result.not_run)
        left = f"; {result.ungraded} of them not graded" if result.ungraded else ""
        if progress.stage == "measurement":
            where = f"the probes with {done} of {done + result.not_run} answers{left}"
    block["message"] = (f"--max-usd stopped {where}" if stop.reason == "spend_cap"
                        else f"analyze stopped at {where}: {stop}")
    if stop.reason == "spend_cap":  # what was spent and what to do (a no-price message is the message)
        block["cause"] = str(stop)
    return block


def stopped_record(progress: Progress, stop: SpendCapReached, *, artifact_kind: str,
                   artifact_ref: str, ref_digest: str | None, model: str, provider: str,
                   judge_model: str, clients: list, provenance: dict, bundle: dict) -> dict:
    """The partial record: it validates as ``novelty.v1`` and says ``status: partial``."""
    result = progress.measure_result
    extraction = progress.extraction
    return {
        "status": "partial",
        "partial": stop_block(progress, stop),
        "schema_version": "novelty.v1",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "artifact": {"kind": artifact_kind, "ref": artifact_ref, "ref_digest": ref_digest,
                     "tokens": sum(span.tokens for span in progress.spans)},
        "model": model,
        "provider": provider,
        "judge_model": judge_model,
        "atoms": {
            "n": len(progress.extracted),
            "n_probed": len({row.atom_id for row in result.runs}) if result else 0,
            "sampling": progress.sampling,
            "faithfulness": progress.faithfulness or {},
        },
        "bundle": bundle,
        "run_config": progress.run_config,
        "provenance": provenance,
        "extraction": extraction.as_dict() if extraction is not None else {},
        "cost": roll_up_cost(clients),
        "warnings": sorted(set(progress.warnings)),
        "extracted_atoms": [atom.as_dict() for atom in progress.extracted],
        "source_spans": [span.as_dict() for span in progress.spans],
        **({"probes": progress.probe_set.as_dict()} if progress.probe_set is not None else {}),
        **({"runs": [row.as_dict() for row in result.runs]} if result is not None else {}),
    }

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Segmentation, protection detection, and span statistics — all offline."""

from __future__ import annotations

from dokimi.novelty.atoms import Atom
from dokimi.novelty.spans import (
    index_atoms,
    protection_tags,
    segment,
    span_summary,
)

DOC = """\
# Setup

Install the runtime first. Do not enable the legacy driver.

## Flags

- `--fast` speeds up startup
- `--strict` requires version 2.4 or newer

```bash
make build
```

| flag | default |
| --- | --- |
| --fast | off |
"""


def test_segment_offsets_reconstruct_the_source():
    spans = segment(DOC)
    for span in spans:
        assert DOC[span.start:span.end] == span.text


def test_segment_keeps_a_fenced_block_whole():
    code = [s for s in segment(DOC) if s.kind == "code"]
    assert len(code) == 1
    assert "make build" in code[0].text


def test_heading_path_tracks_nesting():
    spans = segment(DOC)
    flags = [s for s in spans if s.kind == "list_item"]
    assert flags
    assert flags[0].heading_path == ("Setup", "Flags")


def test_protection_detects_negation_and_version_gates():
    assert "negation" in protection_tags("Do not enable the legacy driver.")
    assert "version_gate" in protection_tags("Requires version 2.4 or newer.")
    assert "threshold" in protection_tags("Fails above 80% utilization.")
    assert protection_tags("It speeds up startup.") == ()


def test_span_summary_counts_tokens_carrying_nothing():
    spans = segment(DOC)
    atom = Atom(
        statement="The legacy driver must not be enabled.",
        question="Should the legacy driver be enabled?",
        answer="No.",
        span_ids=(spans[1].id,),
    )
    index_atoms(spans, [atom])
    summary = span_summary(spans, [atom])
    assert summary["n"] == len(spans)
    assert summary["sole_carrier"] == 1
    # Every span but the one carrying the atom is unattributed, so the upper bound on
    # waste is high — that is the point of calling it a bound.
    assert 0.0 < summary["frac_carrying_nothing"] < 1.0
    assert summary["by_kind"]["code"] == 1
    assert summary["n_atoms_indexed"] == 1
    assert summary["capped_atom_set"] is False


def test_span_summary_flags_a_capped_atom_set_because_the_cap_inverts_the_metric():
    """A cap does not make the dead-token share noisy, it makes it wrong.

    In the study a 250-atom cap turned a real 0.064 into an apparent 0.743, so the flag
    has to be in the payload rather than in a doc.
    """
    spans = segment(DOC)
    atoms = [
        Atom(statement=f"claim {i}", question="q", answer="a", span_ids=(spans[1].id,))
        for i in range(3)
    ]
    # The run says whether its cap left atoms out; three atoms alone do not say it.
    assert span_summary(spans, atoms, capped=True)["capped_atom_set"] is True
    assert span_summary(spans, atoms)["capped_atom_set"] is False
    assert span_summary(spans)["n_atoms_indexed"] is None

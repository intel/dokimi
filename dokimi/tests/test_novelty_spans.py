# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Segmentation, protection detection, and span statistics — all offline."""

from __future__ import annotations

from dokimi.novelty.atoms import Atom
from dokimi.novelty.spans import (
    dangling_fence,
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


_NESTED = "# Valve setup\n\nThe valve opens clockwise.\n\n{o}markdown\n```\nOpen the valve. Close the valve.\n```\n{o}\n\nCheck the gauge.\n"


def test_longer_fence_keeps_an_inner_shorter_fence_in_one_code_span():
    """#974: a four-backtick fence is not closed by a three-backtick line inside it."""
    kinds = ["heading", "sentence", "code", "sentence"]
    assert [s.kind for s in segment(_NESTED.format(o="~~~~"))] == kinds
    spans = segment(_NESTED.format(o="````"))
    assert [s.kind for s in spans] == kinds
    assert spans[2].text.count("```") == 2 + 2


def test_a_longer_closing_fence_still_closes():
    """#974: a closing fence at least as long as the opener closes the block."""
    spans = segment("```\ncode\n`````\n\nAfter the block.\n")
    assert [s.kind for s in spans] == ["code", "sentence"]


def test_a_shorter_closing_fence_does_not_close():
    """#974: a closing line shorter than the opening run is content."""
    spans = segment("````\ncode\n```\nstill code\n")
    assert [s.kind for s in spans] == ["code"]


def test_dangling_fence_reads_the_whole_run():
    """#974: `dangling_fence` uses the same whole-run rule as `segment`."""
    assert dangling_fence("````\n```\ncode\n```\n") == "````"
    assert dangling_fence("````\n```\ncode\n````\n") is None
    assert dangling_fence("") is None


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


def test_span_summary_indexes_every_atom_so_its_atom_set_is_never_capped():
    """A cap does not make the dead-token share noisy, it makes it wrong (0.064 read 0.743 in
    the study), so every run indexes all its extracted atoms and writes `capped_atom_set: false`;
    the key stays because the gate still reads `true` on artifacts written before #371."""
    spans = segment(DOC)
    atoms = [
        Atom(statement=f"claim {i}", question="q", answer="a", span_ids=(spans[1].id,))
        for i in range(3)
    ]
    summary = span_summary(spans, atoms)
    assert (summary["capped_atom_set"], summary["n_atoms_indexed"]) == (False, 3)
    assert span_summary(spans)["n_atoms_indexed"] is None

### Fixed

- **`harmful_atoms` no longer clears a conflict check that did not read the pair space** —
  `novelty.v1` artifacts now record `conflict_coverage` (`pairs_compared`, `pairs_total`),
  and the gate reports `harmful_atoms` as `measured: false` instead of a pass at 0 when fewer
  than `min_conflict_coverage` (default 1.0) of the atom pairs were compared, when the atom
  set was capped, or when `conflict_coverage` is present but malformed. A conflict that was
  found is still reported. `analyze` now sends the whole capped atom set to the conflict
  check in one call (it used batches of 40, which compared about a third of the pairs at the
  default cap of 120), so full coverage is the normal case. Artifacts without the field keep
  the old reading.

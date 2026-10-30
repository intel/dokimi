### Fixed

- **A capped novelty run says how far the cap reached** — extraction stops at
  the atom cap in document order, so a capped run measures the opening of the
  artifact. The cap warning of `novelty analyze` now names the span it stopped
  at, the spans up to it and their share of the tokens, instead of calling the
  atoms a sample; `extraction.reached` records the same in `novelty.json` and
  `atoms.json` (`null` when the cap did not bind); the `novelty.md` header
  states it; and on a capped run the headline's `coverage:` line says the
  readable words went into the input and the capped atoms come from the first
  N% of its tokens, instead of saying they were measured. The tier forfeit
  (`added` is a rate over the opening of the artifact, up to the atom cap) and
  the `harmful_atoms` gate reason (0 conflicts among the atoms of the
  artifact's opening) no longer call the capped atoms a sample either.

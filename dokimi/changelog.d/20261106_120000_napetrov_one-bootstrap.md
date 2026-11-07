### Changed

- **Novelty and tool-preference confidence intervals come from the shared bootstrap.** `novelty` `ci95_added` and the
  `tool_preference` `ci95` now resample with `dokimi.eval.stats.bootstrap_ci` (integer seed, the same stream as the
  McNemar and grounding intervals), where they used their own RNG streams. The resample counts (400 and 2000) and the
  output shapes are unchanged, but the bounds move: the RNG stream changes, and novelty's upper percentile index moves
  from 390 to 389 of 400 (the shared symmetric `round(alpha * (B - 1))` rule). On 40 random inputs per track the
  bounds moved in the third decimal, by about 0.005 at most. Artifacts written before this change keep their old bounds; a re-run gives the new ones.

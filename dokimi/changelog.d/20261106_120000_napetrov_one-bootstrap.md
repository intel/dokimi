### Changed

- **Novelty and tool-preference confidence intervals come from the shared bootstrap.** `novelty` `ci95_added` and the
  `tool_preference` `ci95` now resample with `dokimi.eval.stats.bootstrap_ci` (integer seed, the same stream as the
  McNemar and grounding intervals), where they used their own RNG streams. The resample counts (400 and 2000) and the
  output shapes are unchanged, but the bounds move by Monte Carlo noise: typically in the third decimal, by about 0.005
  at most in the cases measured. Artifacts written before this change keep their old bounds; a re-run gives the new ones.

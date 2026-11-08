### Changed

- **Tool-preference confidence intervals come from the shared bootstrap.** `tool_preference` `ci95` now resamples with
  `dokimi.eval.stats.bootstrap_ci` (integer seed, the same stream as the McNemar and grounding intervals), where it used
  its own RNG stream. The resample count (2000) and the output shape are unchanged, but the bounds move: the RNG stream
  changes. On 40 random inputs the bounds moved in the third decimal, by about 0.005 at most. Artifacts written before
  this change keep their old bounds; a re-run gives the new ones.

### Changed

- **Tool-preference confidence intervals come from the shared bootstrap.** `tool_preference` `ci95` now resamples with
  `dokimi.eval.stats.bootstrap_ci` (integer seed, the same stream as the McNemar and grounding intervals), where it used
  its own RNG stream. The resample count (2000) and the output shape are unchanged, but the bounds move: the RNG stream
  changes, by Monte Carlo noise (in the third decimal, about 0.005 at most in the cases checked). Artifacts written
  before this change keep their old bounds; a re-run gives the new ones.

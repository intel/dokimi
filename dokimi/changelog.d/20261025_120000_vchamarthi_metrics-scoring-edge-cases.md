### Changed

- **Metrics and stats** no longer score wrongly or crash on edge cases in readability, example extraction, coverage, citation parsing, localization, NaN/Infinity aggregates and `mean_ci` confidence levels. Scores for the same inputs can change: re-baseline stored scores that depend on readability, coverage, citations, localization or records. `mean_ci` now raises `ValueError` for a `confidence` outside (0, 1).

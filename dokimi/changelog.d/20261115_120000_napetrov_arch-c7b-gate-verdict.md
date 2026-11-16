### Changed

- **A missing input is listed as not measured, for the score gate and the critical bands.** `check_gate` lists `score` in `not_measured` when the summary lacks it, and no longer when the gate is only disabled. `check_critical_bands` lists a band whose metric the summary lacks (`score`, `coverage`, `freshness_lite`, `readability`). Such a band still reads the metric as 0.0 for its pass or violation, but its verdict is no longer a measured pass: an enabled verdict where no check was measured does not `pass`, and its `status` is `NOT MEASURED` unless a check fails (a missing input whose 0.0 default violates the gate reads `FAIL`); a missing metric is listed in `not_measured` even when another check was measured, and the verdict still passes then. The CLI reads `blocking` and `failures` for the bands and `passed` for the score gate, which always has `score` in its summary, so its output and exit codes do not change.

### Fixed

- **A NaN regression delta is graded CRITICAL, not OK.** `detect_regressions` graded a NaN `score` or metric delta as `OK`, so it passed silently. It now grades `CRITICAL` (a fail-severity check, like any CRITICAL regression), as NaN already violates a critical band. `dokimi compare` does not reach this path, because it rejects non-finite summaries; only direct callers of `dokimi.gate.regression` did.

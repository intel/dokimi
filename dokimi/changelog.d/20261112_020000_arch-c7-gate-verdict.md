### Changed

- **Gate results share one `Verdict` type** — the gates in `dokimi/gate/` return `Verdict` (built from `Check`, both in `dokimi/gate/verdict.py`), and `run`, `compare` and the report read it. The JSON and markdown output and the exit codes are unchanged. `check_gate`, `evaluate_novelty`, `evaluate_trust`, `check_critical_bands` and `detect_regressions` now return `Verdict`, and `format_trust_block` and `format_novelty_block` take it.
- **`Verdict.passed` is False for an enabled verdict that measured no check** — it was True for an empty one. A caller that branched on `.passed` reads such a verdict as not passing; its `status` is `NOT MEASURED`.
- **`Verdict.status` is one of five strings** — `PASS`, `WARN`, `FAIL`, `NOT MEASURED` or `DISABLED`. The `TrustVerdict.status` values were icon strings (`"✅ PASS"`); they are the bare strings now.

### Removed

- **The per-gate result classes** — `GateResult`, `NoveltyCheck`, `NoveltyVerdict`, `TrustCheck`, `TrustVerdict`, `BandViolation`, `CriticalBandsResult`, `RegressionResult` and `RegressionSummary` are gone from `dokimi.gate`. Library code that imports them must use `dokimi.gate.verdict.Check` and `Verdict`. Field mapping: the score gate's `actual_score` / `min_score` → `require("score").value` / `.threshold`; `CriticalBandsResult.violations` / `has_violations` → `checks` / `blocking`; `RegressionSummary.score_regression` / `metric_regressions` → `check("score")` and the other checks; `RegressionResult.severity` → `Check.level`.

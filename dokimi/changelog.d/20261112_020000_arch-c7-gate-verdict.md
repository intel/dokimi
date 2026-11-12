### Changed

- **Gate results share one `Verdict` type** — the gates in `dokimi/gate/` return `Verdict` (built from `Check`, both in `dokimi/gate/verdict.py`), and `run`, `compare` and the report read it. The JSON and markdown output and the exit codes are unchanged. `check_gate`, `evaluate_novelty`, `check_critical_bands` and `detect_regressions` now return `Verdict`, and `format_trust_block` and `format_novelty_block` take it.
- **`Verdict.status` is one of five strings** — `PASS`, `WARN`, `FAIL`, `NOT MEASURED` or `DISABLED`. The `TrustVerdict.status` values were icon strings (`"✅ PASS"`); they are the bare strings now.

### Removed

- **The per-gate result classes** — `GateResult`, `NoveltyCheck`, `NoveltyVerdict`, `TrustCheck`, `TrustVerdict`, `BandViolation`, `CriticalBandsResult`, `RegressionResult` and `RegressionSummary` are gone from `dokimi.gate`. Library code that imports them must use `dokimi.gate.verdict.Check` and `Verdict`.

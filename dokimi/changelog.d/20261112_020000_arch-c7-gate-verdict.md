### Changed

- **Gate results share one `Verdict` type** — the gates in `dokimi/gate/` return `Verdict` (built from `Check`, both in `dokimi/gate/verdict.py`), and `run`, `compare` and the report read it. The JSON and markdown output and the exit codes are unchanged. `check_gate`, `evaluate_novelty`, `evaluate_trust`, `check_critical_bands` and `detect_regressions` now return `Verdict`, and `format_trust_block` and `format_novelty_block` take it.
- **An enabled `Verdict` that measured no check does not pass** — `passed` is False and `status` is `NOT MEASURED`. A disabled one still passes.
- **A CRITICAL regression is a fail-severity check** — its `Verdict.status` is `FAIL`, and `has_critical` / `has_warnings` answer for every gate's verdict: any failing fail-severity or warn-severity check. For regression verdicts they give the same answers as the regression grades, so `compare`'s output and exit codes do not move.
- **`Verdict.status` is one of five strings** — `PASS`, `WARN`, `FAIL`, `NOT MEASURED` or `DISABLED`. The `TrustVerdict.status` values were icon strings (`"✅ PASS"`); they are the bare strings now.

### Removed

- **The per-gate result classes** — `GateResult`, `NoveltyCheck`, `NoveltyVerdict`, `TrustCheck`, `TrustVerdict`, `BandViolation`, `CriticalBandsResult`, `RegressionResult` and `RegressionSummary` are gone from `dokimi.gate`. Library code that imports them must use `dokimi.gate.verdict.Check` and `Verdict`. Field mapping: the score gate's `actual_score` / `min_score` → `require("score").value` / `.threshold`; `CriticalBandsResult.violations` / `has_violations` → `failures` / `blocking` (`checks` holds every band, violated or not); `RegressionSummary.score_regression` / `metric_regressions` → `check("score")` and the other checks; `RegressionResult.severity` → `Check.level`; `TrustVerdict.multirun_std` → `check("multirun_variance").value` (rounded to 2 places, and no check when fewer than two multi-run averages are given).
- **`SEVERITIES` is gone** — `dokimi.gate.novelty_gate.SEVERITIES` (`("info", "warn", "fail")`) has no replacement; `Check.severity` takes those three strings.

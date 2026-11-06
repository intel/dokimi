### Changed

- **Every `repo compare` interval now inverts the sign-flip test** — a row's 95% interval was a
  percentile bootstrap (gate, score) or a t interval (the other measures) next to a sign-flip p, and near
  0.05 the two disagreed. It is now, for every measure, every shift the row's sign-flip test does not
  reject at the uncorrected 5% level (exact up to 16 changed tasks, Monte Carlo above with the draws of p,
  made once from the seed, so the ends are the same on every run), and it excludes 0 exactly when p is
  below 0.05. Under a shift tied tasks count too, so both ends are bounded from 6 tasks on and open at 5
  or fewer: `delta.lo` / `delta.hi` are then `null` in `--json` (before, only a single task had `null`
  ends) and print as `-inf` / `+inf`, in `repo compare` and in the `--ledger` text. There, a non-zero end
  that 3 decimals would print as `0.000` prints at 2 significant digits (`+0.0002`), so an interval that
  excludes 0 no longer reads `[+0.000, +0.000]`; in `repo compare --json`, `delta.mean` stays inside the
  ends (six deltas of `4e-5`: mean `0.0001`, not `0.0`). Each `--json` row, and each rule result of the ledger,
  names its method, `delta.method` `sign_flip_inversion` (`null` for a rule result with no pairs); the ledger's ends
  stay at 4 places, as before. `interval_p_disagree` now fires only where
  Holm's correction lifts a gate or score row's p across 0.05, and its note says the interval is
  uncorrected and `p_holm` decides the row. Printed p, `p_holm`, `mde` and `p_holm_floor` do not change.

### Changed

- **Internal refactor, no behavior change** — functions over the new
  complexity cap were split into helpers. CI now fails a PR that makes a
  file longer or a function more complex than its entry in
  `scripts/growth_baseline.json`; `python scripts/check_growth.py --update`
  lowers the baseline when code shrinks.
  After rebasing onto main, run it to lower shrunk entries; `--seed`
  re-records every over-cap entry at its current value.

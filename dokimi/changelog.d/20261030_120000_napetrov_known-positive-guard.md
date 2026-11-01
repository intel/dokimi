### Added

- **`catalog trigger-probe` known-positive guard** — before the labelled queries, one
  explicit run (`Use the <skill> skill.`, for `--target` or the alphabetically first skill name of the catalog)
  must load that skill. If it does not, the probe writes `"status": "invalid: skill not
  visible"` with `known_positive` and no answers, prints the same status on stdout, and
  exits 1, so a skill the harness never shows cannot read as a 0% trigger rate. A guard run
  that ended in an error writes `"status": "error: known-positive run failed"` instead. An
  empty `--catalog` is refused (exit 2) before the guard runs. Pass
  `--no-known-positive` to skip the check; the output then says `"status": "unchecked"`.

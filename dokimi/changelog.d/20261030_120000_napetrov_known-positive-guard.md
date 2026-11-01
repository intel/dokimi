### Added

- **`catalog trigger-probe` known-positive guard** — before the labelled queries, one
  explicit run (`Use the <skill> skill.`, for `--target` or the alphabetically first skill name of the catalog)
  must load that skill. If it does not, the probe writes `"status": "invalid: skill not
  loaded"` (Claude Code listed it) or `"invalid: skill not visible"` (it did not), with
  `known_positive`, the probe's `max_turns_hit`/`not_listed`/`skill_selection_summary` keys
  empty, no answers, no stale `<out>.partial`, prints the same status on stdout, and
  exits 1. A skill call whose result is an error, or another plugin's `plugin:skill`, does
  not count as a load. So a skill the harness never shows cannot read as a 0% trigger rate. A guard run
  that ended in an error writes `"status": "error: known-positive run failed"` instead. An
  empty `--catalog` is refused (exit 2) before the guard runs, unless `--no-known-positive` is passed. Pass
  `--no-known-positive` to skip the check; the output then says `"status": "unchecked"` (or `invalid: skill not listed` when a
  catalog skill is never listed).

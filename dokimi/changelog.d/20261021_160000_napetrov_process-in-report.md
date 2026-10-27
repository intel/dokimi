### Added

- **`repo report` and `repo compare` show `result.json` `process` counts** —
  `repo report` prints, under each (task, arm, model) row, the mean of each
  `process` count over the cells that have one (`tool_calls`, `commands`,
  `failed_commands`, `denied_commands`, `guidance_read` as a number of paths,
  and `counter:<name>` for each `process_metrics` counter); the JSON row has
  them under `process`. `repo compare` adds each count a compared cell
  reports as a descriptive measure (`process:<key>`, `counter:<name>`): a
  paired delta with a t interval and a raw p, outside Holm's family. Cells
  whose `process` is absent or `null` are left out, so batches written before
  `process` existed compare as before; a count only one arm reports for a
  (treatment, model) is not compared, and a warning names it.

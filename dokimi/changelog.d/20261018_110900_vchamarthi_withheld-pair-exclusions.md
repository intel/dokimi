### Fixed

- **A withheld task-run comparison keeps its exclusions** — when only one
  `(task, repeat)` cell is shared, McNemar still cannot run (`p` stays null),
  but the comparison record now carries `n_excluded`. Before, that case
  returned no record, so cells each harness attempted alone disappeared and
  the report looked like no comparison.

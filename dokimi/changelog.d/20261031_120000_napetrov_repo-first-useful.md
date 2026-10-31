### Added

- **Time to first useful result, per repo-eval run** — a `task.json` can declare
  `"useful": {"command": "<regex>"}` for a task graded by its target tests, or
  `"useful": {"edit": "<glob>"}` (or a list of globs) for one graded by an edit to a target
  file. The first event that hits it (a command whose text matches and whose `rc` is 0, or an
  edit whose path matches) is recorded in `result.json` `first_useful` with its `issued_s` (the
  event's issue time) and `steps` (events up to and including it). A run with no hit says
  why (`never`, `unobserved`, `untimed`); a task with no declaration is `not_declared`.
  `repo report` shows a row's observed count, medians and reasons under the row, and
  `repo compare` pairs the per-task medians as `first_useful_s` and `first_useful_steps`.
  A malformed `useful` is refused by `repo validate`, `repo tasks check` and `repo run`.
  Batches run before this change have no `first_useful` and no such rows.

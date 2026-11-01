### Added

- **Time to first useful result, per repo-eval run** — a `task.json` can declare
  `"useful": {"command": "<regex>"}` for a task graded by its target tests, or
  `"useful": {"edit": "<glob>"}` (or a list of globs) for one graded by an edit to a target
  file. The first non-denied event with `rc` 0 that hits it (a command whose text matches, or an
  edit whose path matches) is recorded in `result.json` `first_useful` with its `issued_s` (the
  event's issue time) and `steps` (events up to and including it). A run with no hit says
  why (`never`, `unobserved`, `untimed`, or `timeout`: a `command` pattern, searched with the `regex` module, ran past
  2 s on one event, so the run has no verdict); a task with no declaration is `not_declared`.
  `repo report` shows a row's observed count, medians and reasons under the row, and
  `repo compare` pairs the per-task medians as `first_useful_s` and `first_useful_steps`, and
  the share of declared cells that observed a hit as `first_useful_rate`; its header counts
  observed of declared cells per arm.
  A malformed `useful` is refused by `repo validate`, `repo tasks check` and `repo run`.
  Batches run before this change have no `first_useful` and no such rows.
- A Codex `file_change` record whose `changes` has a malformed entry (not an object, or no path), or is a
  non-list value other than `null`, is one dropped line, as the other malformed records are. The whole record is dropped: its valid
  entries are not read either, so no edit from it counts.

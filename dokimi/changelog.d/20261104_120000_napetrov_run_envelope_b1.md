### Added

- **Run envelope (`run_id`) on run artifacts** ([#277](https://github.com/intel/dokimi/issues/277)) —
  `save_artifact` stamps every run kind with a `run_id` right after `schema_version`: one id per
  dokimi process (`YYYYMMDDTHHMMSSZ-xxxxxxxx`), or the one a caller passes. The four closed
  schemas (`harness_profile`, `repo_manifest`, `repo_readiness`, `tool_preference_groups`) and the
  other closed run schemas name `run_id` as an optional property. `runs list --json` and
  `runs show` report it; artifacts written before this change still list and show, with
  `run_id` `null`. Grouping by `run_id` is not in this change. A child dokimi process
  (`repo conformance`) gets its own id. A question set is an input and has no `run_id`, so its
  `question_set_hash` is unchanged by a re-save. Re-saving a `repo_ledger` file adds a new
  `run_id`, so the file changes on every re-save. `eval headroom` leaves the top-level `run_id`
  out of its duplicate-run check, so two arms files that differ only in it count as one run.

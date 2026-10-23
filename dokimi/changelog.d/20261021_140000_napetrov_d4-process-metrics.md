### Added

- **Process metrics in `result.json`** — every `repo run` cell's `result.json` has a
  `process` object counted from `events.jsonl`: `tool_calls`, `commands`,
  `failed_commands` (`rc` neither 0 nor null), `denied_commands`, and `guidance_read` (the
  read paths the manifest's guidance globs match). A manifest's new `process_metrics:` maps
  a name to a regex, searched in each command's text, or its `output` (`match: output|any`),
  and `process.counters` holds each count. `repo validate` refuses a regex that does not
  compile.

### Changed

- **`entry_points.grade` is optional** when every task's grader is a built-in `dokimi:` one;
  `repo validate`, `repo run` and `repo tasks check` name the tasks that need it otherwise,
  the last two before any agent runs.

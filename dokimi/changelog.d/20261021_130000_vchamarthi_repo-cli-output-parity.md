### Added

- **`repo fetch --json`** prints `{"sha", "git_dir"}` as JSON, so a script reads the
  pinned commit without splitting the tab-separated line.
- **`repo validate --out FILE`** and **`repo budget --out FILE`** write the full
  report as JSON, as `repo scan` and `repo inspect` already do. The summary (or
  `--json`) still prints.
- **`repo validate --fail-on-warning`** exits 1 when the report has any warning,
  as `repo budget --fail-on-warning` does, so a CI job can gate on a guidance
  glob that matches nothing or a task base SHA that is not 40 characters.

The default output and exit codes of all three commands are unchanged.

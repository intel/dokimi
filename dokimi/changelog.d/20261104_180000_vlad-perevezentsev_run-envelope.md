### Added

- **Run envelope on run artifacts**: every artifact of a registered run kind now has `run_id`
  and `dokimi_version` right after its `schema_version`. The files one process writes share the
  `run_id` (`20261104T153012Z-1a2b3c4d`). Set `DOKIMI_RUN_ID` to give several
  commands one id, for example the steps of a CI job. A malformed value (anything but 1-128
  letters, digits, `.`, `_` or `-`, starting with a letter or a digit) exits 2 before the command
  runs. `runs list --json` and `runs show` print the `run_id`. Input kinds (`plan`,
  `dokimi_config`, `repo_ledger`, `need_graph`, `cli_routes`) get no envelope. Files saved before
  the envelope have neither key and stay valid, and the 26 run-kind schemas declare both as
  optional, `run_id` with the same pattern. A reader that compares a whole saved document with a
  committed copy now sees two more keys.

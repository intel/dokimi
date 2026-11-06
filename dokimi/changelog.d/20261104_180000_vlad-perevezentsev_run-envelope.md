### Added

- **Run envelope on run artifacts**: every artifact of a registered run kind now has `run_id`
  and `dokimi_version` right after its `schema_version`. The files one process writes share the
  `run_id` (`20261104T153012Z-1a2b3c4d`), so a catalog run's report, plan and result carry one id.
  Set `DOKIMI_RUN_ID` to give several commands one id, for example the steps of a CI job. A
  malformed value (anything but 1-128 letters, digits, `.`, `_` or `-`, starting with a letter or
  a digit) exits 2 before a command runs (`--help` and `--version` still work). `runs list
  --json`, `runs show` and the rows of `dataset export` carry the `run_id`. Input kinds
  (`questions`, `dokimi_config`, `repo_ledger`, `need_graph`, `cli_routes`) get no envelope, so a
  question set saved again keeps its bytes and its `question_set_hash`. `plan_digest` and the
  `eval headroom` duplicate check leave the envelope out. The `--json` print of `repo scan`,
  `repo inspect`, `repo budget` and `eval inspect` now leads with `schema_version` and the
  envelope, as the file `--out` writes does. Files saved before the envelope have neither key and
  stay valid, and the 26 run-kind schemas declare both as optional, `run_id` with the same
  pattern. A reader that compares a whole saved run artifact with a committed copy now sees two
  more keys.

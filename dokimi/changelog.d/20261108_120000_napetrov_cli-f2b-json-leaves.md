### Changed

- **`runs list --json`, `runs show --json`, `trace inspect --json`, `trace consistency --json` and
  `trace prefilter --json` print a `cli_output.v1` document** (#94 F2). The payload moved from the top
  level to `data`: read `data` where you read the object or list before. `status` is `partial` when a
  trace or session could not be read (exit 1), when a trace or session has skipped lines or records, or
  when `runs list|show` could not read entries under the root (exit 0), and `failed` when nothing was read; a `trace` leaf that refuses its
  input prints `failed` with `error` null and exits 1. `runs list|show` exit 2 with `error.code` `input`
  when `DOKIMI_RUNS_DIR` names a root that does not exist (a missing default root is not an error for
  `runs list`, which exits 0; `runs show` exits 2). A crash exits 5 with an `infra` error
  document. The other `--json` leaves still print their own payload until their follow-up PRs.

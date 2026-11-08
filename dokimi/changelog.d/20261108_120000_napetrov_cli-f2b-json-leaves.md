### Changed

- **`runs list --json`, `runs show --json`, `trace inspect --json`, `trace consistency --json` and
  `trace prefilter --json` print a `cli_output.v1` document** (#94 F2). The payload moved from the top
  level to `data`: read `data` where you read the object or list before. `status` is `partial` when a trace
  could not be read and `failed` when nothing was read or the input is refused (exit 1 in both); a crash exits 5 with an `infra` error document. The other
  `--json` leaves still print their own payload until their follow-up PRs.

### Added

- **`backends --json` prints a `cli_output.v1` document** (#94 F2): `status`, `verdict` (null),
  `summary`, `cost`, `next`, and the rows under `data.rows`. A crash or an input error in `backends --json`
  prints the same envelope with `error.code` `infra` (exit 5) or `input` (exit 2).
  `dokimi data schema show cli_output.v1` prints the schema. The other `--json` leaves still print their
  own payload until their follow-up PRs.

### Changed

- **`backends --json` output is an object, not a list.** The rows moved from the top level to
  `data.rows`. Read `data.rows` instead of the array.

### Added

- **`docs llms-txt`** (prototype) — runs the static llms.txt check from the command line on a
  file or a directory holding one: title and summary, described links, when-to-use and
  when-not-to-use sections, and a `--token-budget` size check. Prints a row per finding or
  `cli_output.v1` with `--json`; exits 1 when a `fail` finding exists. No model calls.

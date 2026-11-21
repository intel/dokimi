### Fixed

- **A `--json` usage error now names the leaf's `--help` command in `next`** — a bad value, a missing argument or an unknown flag (before or after the leaf name) on a `--json` leaf printed its `cli_output.v1` failed document with `next` empty. `next` now holds one free step (`spends` false, `est_usd` 0) whose `argv` is `dokimi <leaf> --help`, which prints the accepted syntax; the caller still chooses the corrected arguments. Other documents keep an empty `next`. (#941)

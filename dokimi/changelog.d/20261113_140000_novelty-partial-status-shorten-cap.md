### Fixed

- **`skill shorten` at the `--max-usd` cap says what a rerun can reuse** — the exit message, which still says nothing was written (no candidate table and no `--out` file), now adds how many retention verdicts were adjudicated before the stop: with `--cache-dir` they are stored and a rerun with a higher `--max-usd` and the same `--cache-dir` replays them at $0; without it, `--cache-dir <dir>` would have kept them.

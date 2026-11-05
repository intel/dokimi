### Fixed

- **`novelty routing` keeps its paid calls when `--max-usd` stops it** — a
  single-artifact or `--all-vs-all` run that hit the cap (or a call with no
  known price) used to exit with `nothing was written`, losing every call
  already paid for. It now writes `--out` with the calls made scored, a
  `partial` block (`reason: spend_cap`, `calls_done`, `calls_planned`) and
  `status: "partial"`, prints the summary (marked `partial: N of M calls`) and
  `cost` lines, and still exits non-zero. If `--out` already holds a finished
  result, the partial one goes to `<out stem>.partial.json` instead. `novelty analyze`,
  `atoms`, `probe` and `shorten` still write nothing when the cap stops them.

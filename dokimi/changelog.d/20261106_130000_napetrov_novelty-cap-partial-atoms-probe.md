### Added

- **`analyze` names the run's expected cost before it spends** — the expected USD at list prices
  (the figure `--estimate-only` prints as `expected_usd`) goes to stderr before the first paid call.

### Fixed

- **`--max-usd` keeps the work of `atoms` and `probe` it stops** — `atoms` writes
  `<stem>.partial.json` with the spans read so far (the rest unread), and `probe` writes
  `probes.partial.json` with the answers so far, and both exit naming the file. A partial file is
  refused by the atom readers and by `novelty items`, and never replaces a finished file.
  `analyze` and `shorten` still lose their work at the cap (follow-up).

### Added

- **`novelty analyze` states the measured share on stdout** — the headline now prints
  `coverage: N% of the artifact's readable words were measured`, with `(entry point only)`
  for a run without `--include-resources`. The novelty.v1 bundle record stamps the same figure
  as `bundle.measured_share`, so a script can read it without recomputing it from
  `measurable_outside_share`.

### Changed

- **A run that read half or less of the readable words warns on stdout** — the `WARNING: only
  N% ...` line goes to stdout, because a headline piped elsewhere never shows stderr. The
  threshold of the stderr warning is unchanged, and an entry-point run warns on both channels
  as before. The stdout line is wider than the stderr one in two cases: a `--include-resources`
  run whose word budgets left half or more of the readable words unread now warns on stdout
  (its `warnings` list is unchanged), and `coverage: unknown` (a bundled file did not decode)
  keeps the `(entry point only)` scope and warns when the entry point is half or less of the
  readable words. `bundle.measured_share` is `null` when the share is unknowable.

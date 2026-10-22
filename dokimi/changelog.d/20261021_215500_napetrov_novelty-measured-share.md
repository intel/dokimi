### Added

- **`novelty analyze` states the measured share on stdout** — the headline now prints
  `coverage: N% of the artifact's readable words were measured`, with `(entry point only)`
  for a run without `--include-resources`. The novelty.v1 bundle record stamps the same figure
  as `bundle.measured_share`, so a script can read it without recomputing it from
  `measurable_outside_share`.

### Changed

- **A run that read half or less of the readable words warns on stdout** — the `WARNING: only
  N% ...` line goes to stdout as well as the stderr warning, because a headline piped
  elsewhere never shows stderr. The threshold is unchanged, so the same runs warn as before; a
  rate quoted from an entry-point run of a progressive-disclosure skill now carries that
  caveat where it is copied.

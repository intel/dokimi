### Changed

- **A capped novelty run probes atoms spread over the whole input** — under
  `--max-atoms` or a tier's cap (25 at `triage`, 60 at `standard`, 120 at
  `study`), extraction no longer stops at the cap: it reads every span, and of
  the M atoms it extracts the run probes n at indices
  `floor((i + 0.5) * M / n)`, every (M/n)-th in document order (on a
  `--include-resources` run, across the files in file order), so a capped rate
  estimates the whole input's per-atom rate instead of describing its opening.
  The selection takes no seed. `novelty analyze` and `novelty atoms` (whose
  `atoms.json` `probe` and `shorten` read) probe the same atoms. New runs record
  `atoms.sampling` as `cap-spread:<n>` and `extraction.sampled` as
  `{"atoms", "of", "rule"}`, with `extraction.reached` `null`; the cap
  warning, the `novelty.md` header and the headline's `coverage:` line say how
  many of the input's atoms were probed and with what stride. Artifacts written
  with `cap-per-artifact:<n>` and a `reached` record still validate and render
  as before. Probing still stops at the cap, but on an artifact with more atoms
  than the cap extraction now makes more calls, and `--estimate-only` counts
  them; the tier profiles were measured with the old order (decision D017). The
  shortening forfeit now says candidates are scored against a spread sample of
  the cap's atoms, so cutting a span with no sampled atom looks free.

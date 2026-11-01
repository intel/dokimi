### Changed

- **A capped novelty run probes atoms spread over the whole input** — under
  `--max-atoms` or a tier's cap (25 at `triage`, 60 at `standard`, 120 at
  `study`), extraction no longer stops at the cap: it reads every span, and of
  the M atoms it extracts the run probes n at indices
  `floor((i + 0.5) * M / n)`, every (M/n)-th in document order by the earliest
  span each atom cites (on a `--include-resources` run, across the files in
  file order, so a file with few atoms may get no probe), so a capped rate
  estimates the whole input's per-atom rate instead of describing its opening.
  The selection takes no seed: the same extracted atom list gives the same
  sample. `novelty analyze` and `novelty atoms` (whose `atoms.json` `probe` and
  `shorten` read) probe the same atoms. A cap below 1 is refused before any
  call. New runs record `atoms.sampling` as `cap-spread:<n>` and
  `extraction.reached` `null`; `extraction.sampled` is `{"atoms", "of", "rule"}`
  when the cap leaves atoms out, and `null` when it keeps every atom (M ≤ n).
  Only a cap that leaves atoms out sets `spans.capped_atom_set` and brings the
  cap's forfeits: an input whose atoms exactly meet the cap no longer counts as
  capped. The cap warning, the `novelty.md` header and the headline's
  `coverage:` line say how many of the input's atoms were probed and with what
  stride, or, when some spans went unread, that the sample is spread over the
  atoms of the spans that were read and how many were not. Artifacts written
  with `cap-per-artifact:<n>` and a `reached` record still validate and render
  as before. Probing still stops at the cap, but a run whose cap was reached
  before its last extraction batch now asks the remaining batches, and
  `--estimate-only` counts them; the tier profiles were measured with the old
  order (decision D017). The
  shortening forfeit now says candidates are scored against a spread sample of
  the cap's atoms, so cutting a span with no sampled atom looks free.

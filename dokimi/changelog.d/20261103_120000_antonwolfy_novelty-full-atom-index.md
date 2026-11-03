### Fixed

- **A capped novelty run indexes its spans and writes `atoms.json` from every
  extracted atom** — under `--max-atoms` or a tier's cap, `novelty analyze`
  and `novelty atoms` used to keep only the cap's spread sample after
  extraction, so a span whose atom fell outside it read as carrying nothing:
  200 one-atom spans at `--tier standard` left 140 of them dim, with
  `frac_carrying_nothing` 0.70, dead weight a shortening could cut, and no sole
  carrier among them. Spans (`atom_ids`, `frac_carrying_nothing`,
  `n_atoms_indexed`), span labels, dead weight, sole carriers and shortening's
  structure now come from all M atoms, and `spans.capped_atom_set` is `false`
  on new runs; faithfulness, the probes, the conflict check, carrier
  confirmation and the shortening oracle still run on the n sampled atoms, so
  the cost stays at the cap. A shortening candidate that keeps no span of an
  unsampled atom counts it in `dropped_unscored_atoms` and is not `safe`, so
  `shorten --out` and the offered candidates never lose such an atom unscored
  (without `--allow-unsafe`). `atoms.json` holds every atom and lists the
  sample in `sampled_atom_ids`: `novelty probe` and `novelty shorten` measure
  that sample by default and say so, and `--all-atoms` measures every atom in
  the file, reusing the extraction, with a stderr warning counting the added
  atoms whose faithfulness is `unchecked`. A file without the record (written
  before it) reads as every atom sampled; one that names an atom it does not
  hold exits 2. When such an older file comes from a cap that bound
  (`extraction.sampled` set, or any `extraction.reached`) or may have bound
  (`cap` set, no `extraction.reached`, and at least `cap` atoms),
  `novelty probe`, `novelty shorten` and `novelty view` warn on stderr that
  it holds only the probed atoms (or, for a cap met at the last span or a
  file without `reached`, may hold only those), so a shortening can cut
  spans whose atoms fell outside them, and to re-run `novelty atoms`. The `harmful_atoms` gate check still does not clear a 0 on a
  sampled run, now read from `extraction.sampled`, and the `dead_tokens`
  check judges a capped run's share. The cap no longer forfeits
  `frac_carrying_nothing`, and the shortening forfeit says `knowledge` and
  `value` are retention of the sample rather than that a cut looks free.
  `probes.json` records `sampling` (`atoms` probed, `of` the file's atoms,
  `all_atoms`), so a rate over the sample is not read as the file's.

### Changed

- **Capped novelty runs: span and shortening figures are not comparable with
  older capped runs** — on a run whose cap left atoms out,
  `frac_carrying_nothing`, dead weight, sole carriers, the novelty gate's
  `dead_tokens` check and the candidate `novelty shorten --out` writes are
  now computed over every extracted atom instead of the cap's sample, so the
  same input gives different numbers than before. Re-run older capped
  `novelty analyze` and `novelty atoms` runs before comparing them with new
  ones.

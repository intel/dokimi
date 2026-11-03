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
  the cost stays at the cap. `atoms.json` holds every atom and lists the
  sample in `sampled_atom_ids`: `novelty probe` and `novelty shorten` measure
  that sample by default and say so, and `--all-atoms` measures every atom in
  the file, reusing the extraction. A file without the record (written
  before it) reads as every atom sampled; one that names an atom it does not
  hold exits 2. The `harmful_atoms` gate check still does not clear a 0 on a
  sampled run, now read from `extraction.sampled`, and the `dead_tokens`
  check judges a capped run's share. The cap no longer forfeits
  `frac_carrying_nothing`, and the shortening forfeit says `knowledge` and
  `value` are retention of the sample rather than that a cut looks free.

### Added

- **`novelty routing --all-vs-all`** routes every skill that has queries against the whole
  field, in turn, and reports a confusion matrix plus an attractor table: the skills picked
  for other skills' queries, with how many of those queries they took and from whom. Takes
  `--queries-by-skill <json>` and `--out`, and no `--artifact`. The lexical arm always runs;
  `--model` adds the model arm.

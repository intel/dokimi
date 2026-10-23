### Added

- **`novelty routing --all-vs-all`** routes every skill that has queries against the whole
  field, in turn, and reports a confusion matrix plus an attractor table: the skills picked
  for other skills' queries, with how many of those queries they took and from whom. Takes
  `--queries-by-skill <json>` and `--out`, and no `--artifact`. The lexical arm always runs;
  `--model` adds the model arm.
- `--all-vs-all` rates are over the scored queries (`n_scored`, `n_unscored`; `null` own rate
  when none was scored); a lexical tie among other cards is `(tie)`; a failed model call
  exits non-zero; a repeated skill key in the queries file is refused; routing results record
  the field rule under `field`.

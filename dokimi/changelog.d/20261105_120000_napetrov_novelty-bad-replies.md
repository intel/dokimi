### Fixed

- **Judge and conflict replies with no usable row are split and not cached** — a value-judge
  reply whose rows all have a non-boolean `needed` (`"false"`, or missing), or no `n` in
  range, used to be cached and counted as skipped. It is now split and re-asked, and the
  cache does not keep it. A row whose `needed` or `scaffolding` is not a boolean, in a reply
  that has a usable row, is counted apart as malformed (`unjudged_malformed`), not as
  skipped. A conflict reply none of whose rows names a pair of its batch is split, not read as
  "checked, no conflicts". Conflict rows that name no pair (a missing `a` or `b`, `a == b`,
  out of range) are dropped and counted in the warnings.
- **A reply with an unreadable statement number no longer aborts the run** — an `Infinity`
  or a number too long to parse, in a conflict or judge row, used to raise and stop the
  stage; now that row is dropped (or skipped) and the valid rows of the reply are kept. A
  bool or a fraction as a statement number is not read as one.
- **Retry warnings give `--max-tokens` only for cut-off replies** — a content-classification
  retry for rows with no usable verdict is worded `re-asked N unusable batches as halves`
  without the `--max-tokens` hint. Extraction uses the same rule.

### Changed

- **One rule for a record without a question** — every judge path (`eval score`,
  `eval panel-score`, `arms` judging, `eval pairwise`, `eval checklist`,
  `eval cross-judge`, and the degradation pairs of `eval judge-validity` /
  `eval judge-wording`) now checks all records before the first judge call and
  refuses the run when one has a missing or blank question text, naming the
  record. Before, `eval score` recorded a per-question error only when the key
  was missing, the degradation pairs skipped the record silently, and the other
  paths sent an empty question to the judge. The CLI prints `Error: …` and
  exits 1.

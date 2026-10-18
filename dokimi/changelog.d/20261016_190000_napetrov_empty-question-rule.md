### Changed

- **One rule for a record without a question** — every judge path (`eval score`,
  `eval panel-score`, the RAGAS meta-evaluation of `eval ragas` and
  `answers generate --ragas`, `arms` judging, `eval pairwise`, `eval checklist`,
  `eval cross-judge`, and the degradation pairs of `eval judge-validity` /
  `eval judge-wording`) now checks the records it would send to a judge before
  the first judge call, and before the `--judge-temperature` sampling probe, and
  refuses the run when one has a missing or blank question text, naming the
  record. A record the path would skip anyway is not refused: an answer-error
  record with no answer, a record with only empty answers, one without expected
  points (`eval checklist`), one without a baseline answer or with only arms left
  out by `--arms` (`eval pairwise`), one whose answers are all under the
  degradation length floor, and one without a question id (the degradation
  pairs). `arms run --judge` checks every question before any answer is paid
  for. Before, `eval score` recorded a per-question error only when the key was
  missing, the degradation pairs skipped the record silently, RAGAS reported a
  warning, and the other paths sent an empty question to the judge. The CLI
  prints `Error: …` and exits 2, the code these commands use for other input
  errors (1 stays the verdict of `eval judge-validity` and the `--strict` gates).

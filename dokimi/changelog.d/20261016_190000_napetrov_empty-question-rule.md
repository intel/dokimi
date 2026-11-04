### Changed

- **One rule for a record without a question** — every judge path (`eval score pointwise`,
  `eval score panel`, the RAGAS meta-evaluation of `eval score ragas` and
  `answers generate --ragas`, `arms` judging, `eval score pairwise`, `eval score checklist`,
  `eval judge agreement`, and the degradation pairs of `eval judge validate` /
  `eval judge wording`) now checks the records it would send to a judge before
  the first judge call, and before the `--judge-temperature` sampling probe, and
  refuses the run when one has a missing or blank question text, naming the
  record. A record the path would skip anyway is not refused: an answer-error
  record with no answer, a record with only empty answers, one without expected
  points (`eval score checklist`), one without a baseline answer or with only arms left
  out by `--arms` (`eval score pairwise`), and one whose answers are all under the
  degradation length floor (a record without a question id is a `RecordError`,
  see the records entry). `arms run --judge` checks every question before any answer is paid
  for. Before, `eval score pointwise` recorded a per-question error only when the key was
  missing, the degradation pairs skipped the record silently, RAGAS reported a
  warning, and the other paths sent an empty question to the judge. The CLI
  prints `Error: …` and exits 2, the code these commands use for other input
  errors (1 stays the verdict of `eval judge validate` and the `--strict` gates).

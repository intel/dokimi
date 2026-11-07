### Fixed

- **Panel verdicts need a quorum** — a panel reports a mean, spread and agreement
  only when a majority of its panelists return a valid vote. One valid vote out
  of three is now `insufficient_evidence` (no mean, delta or agreement; the
  `score_answer` result carries an `insufficient evidence` error), and a single
  vote has no spread, so `std` and `agreement_score` are `null` rather than 0 and 1.0.
- **Panel judges see the question's ground truth** — `score_answer` and
  `evaluate_answers` pass the reference passage to every role prompt, as the
  single judge's grounded prompt does, instead of dropping it. Flipping it
  changes the prompt.
- **Panel identity covers the role→model map** — `model` lists each role with
  its model, and `prompt_sha256` also covers each judge's provider, temperature
  and seed, and the aggregation policy. Swapping models between roles, or changing
  the policy, now changes the identity. Panel runs saved under the old identity
  no longer pool with new ones as replicates.

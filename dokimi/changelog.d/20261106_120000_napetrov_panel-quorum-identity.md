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
- **A panel cell that fails quorum carries an `error`** — the saved cell has the
  `score_answer` error text (`insufficient evidence: …`, or `all panel judges
  failed`), so readers that count `Score.error` see it. The dashboard's doc score
  now leaves such a question out, as it does for any judge error, where before it
  counted a quorum failure with retrieval missed as a context 0. The eval summary
  prints how many questions it leaves out (`Questions left out …`). Panel artifacts
  saved before this change have no such `error`, so the dashboard reads them as before.

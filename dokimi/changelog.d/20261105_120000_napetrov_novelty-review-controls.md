### Added

- **Novelty review controls.** These are the checks a reviewer asked of the novelty 2x2.
  - `paired` now reports `added_over_placebo` when the `placebo` arm ran: right with the artifact, wrong without it, and wrong with the placebo.
  - A new `no_artifact_repeat` arm asks the baseline again as a new cache draw. With it, `paired` reports `noise_floor`, `added_net_of_noise` and `degraded_within_noise`.
  - `novelty analyze` and `novelty probe` take `--context-framing reference|neutral`, which controls for placebo abstention caused by the prompt. A probe record names its framing (`context_framing`).
  - `added_over_placebo.rate` is `null`, not `0.0`, when no probe was answered in all three arms.
  - `added_net_of_noise` and `degraded_within_noise` are computed over the probes all three arms answered, and are omitted when there are none.

### Changed

- **`ci95_added` resamples atoms rather than probes** (`ci_cluster: "atom"`). Paraphrases of one atom are correlated, so the old interval was too narrow. It needs at least 5 atoms: a run of 4 atoms with 3 paraphrases each got an interval before and gets `null` now. The new intervals are wider, so `ci95_added` from older cells is not comparable with new ones; re-run those cells to compare. A subject scorecard cell now carries `ci_cluster` next to `ci95_added`, null for an older artifact.
- The `shuffle_text` docstring no longer cites the study's shuffled-arm win, because that arm injected more files than `full` did.
- `prompts_sha256` now also covers the two context preambles, so its value differs from earlier runs.

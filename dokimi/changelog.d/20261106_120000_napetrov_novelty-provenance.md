### Added

- **Novelty `provenance` block** — every `novelty analyze` and `novelty probe` JSON records
  `dokimi_version`, `git_commit` (`null` outside a checkout), `artifact_digest`,
  `prompts_sha256`, the answerer and judge models, `context_framing`, and the vendors read
  off the model ids. A judge of the answerer's vendor is warned about on stderr (and in
  `warnings` for `analyze`); it is not refused.
- **`--judge2 MODEL --judge2-frac F`** on `novelty analyze` and `novelty probe` — a second
  judge regrades a seeded share (default 0.15) of the graded runs; `judge2` reports lenient
  agreement, Cohen's kappa on lenient and exact verdict agreement. A second judge that is the
  answering model exits 2 unless `--allow-self-judge`; so does a fraction outside (0, 1].
- **Reversal resistance for both arms** — `reversal_resisted_by_arm` and `reversal_paired`
  (resisted only with the artifact, only without it) next to `reversal_resisted`.

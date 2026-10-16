### Added

- **Judge's treatment material stored in arms artifacts** — when `dokimi eval arm-runner`
  judges an arm with `--judge`, the artifact now keeps the exact text the judge read for
  each arm and question, indexed by SHA256 digest. Later readers of the artifact (e.g.,
  `dokimi eval cross-judge --judges` re-scoring the same answers) see the stored material
  instead of a 200-char snippet fallback, so the re-scored judge reads what the original
  judge saw and scores match the stored baseline. Artifact sizes grow by one copy per
  distinct text per arm (perf skill +0.3% on typical runs, docs runs grow by the
  answerer's 15,000-char context cut per question).

### Changed

- **`eval cross-judge --judges` no longer refuses arms with stored material** — previously,
  it rejected any arm whose text was not in memory, falling back to snippets. Now it
  verifies the stored digest matches the text, reads it, and passes it to the judge. The
  `--pairwise` and `--checklist` judges still refuse, because they see only answers.


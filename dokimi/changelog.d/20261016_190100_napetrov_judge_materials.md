### Added

- **Judge's treatment material stored in arms artifacts** — when `dokimi eval arm-runner`
  judges an arm with `--judge`, the artifact now keeps the exact text the judge read for
  each arm and question, indexed by SHA256 digest. Later readers of the artifact (e.g.,
  `dokimi eval cross-judge --judges` re-scoring the same answers) see the stored material
  instead of a 200-char snippet fallback, so the re-scored judge reads what the original
  judge saw. Artifact sizes grow by one copy per
  distinct text per arm (perf skill +0.3% on typical runs, docs runs grow by the
  answerer's 15,000-char context cut per question).

### Changed

- **`eval cross-judge --judges` accepts stored scores when the file keeps the material** —
  it used to exit 2 for any arm judged on treatment material. It now re-scores with the
  stored text and exits 2 only when a stored score cites different material than the arm
  record serves, or a question's material cannot be read; the error names the questions.
  `--no-stored` still compares the measures anyway, and `--pairwise` and `--checklist`
  still refuse stored scores because those judges see only answers.

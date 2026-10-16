### Added

- **`eval judge-validity --context stored`** — judges each base with the arms
  artifact's verified `judge_materials`, the same material the arms were scored
  with. Bases without verified material are dropped before `--bases` /
  `--max-bases` sampling; the summary reports `n_without_context` and per-arm
  kept/dropped counts in `by_arm`, and the run exits 2 if an arm loses all its
  bases or no pair is usable.

### Changed

- **`cross-judge --validity` matches a report by judge and context** — a report
  is applied to a measure only when both the judge label and the context
  (`stored` or `none`) agree, so a no-context validity report no longer gates a
  judge that read the stored material. `cross-judge --judges` re-scores with the
  stored material.

### Added

- **`eval judge-validity --context stored`** — judges each base with the arms
  artifact's verified `judge_materials`, the same material the arms were scored
  with. Bases without verified material are dropped before `--bases` /
  `--max-bases` sampling; the summary reports `n_without_context` and per-arm
  kept/dropped counts in `by_arm` (with `scored` bases in `--sequential` mode). The run exits 2 with a typed scorer or
  `--mode pairwise`, on a file with no non-empty verified material, if an arm
  loses all its bases or gets none sampled, or if no pair is usable.

### Changed

- **`eval judge-validity` exits 2 when no pair is usable, in every mode** — a
  file with no answer longer than the minimum (or where every degradation is a
  no-op) now prints an `Error:` line and exits 2 instead of a `ValueError`
  traceback (exit 1), with or without `--context`.

- **`cross-judge --validity` matches a PASS by judge and context** — a passing
  report validates a measure only when both the judge label and the context
  (`stored` or `none`) agree, so a no-context PASS no longer validates a judge
  that read the stored material. A FAIL still silences every measure of that
  judge in any context, and a PASS never lifts it, whatever the report
  order. Reports without a `context` key (written before this
  change) count as `none`, and a stored measure whose scores fell back to
  snippets is `none` too; one mixing stored and snippet scores is `mixed`,
  which no PASS validates. `cross-judge --judges` re-scores with the
  stored material.

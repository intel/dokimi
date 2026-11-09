### Added

- **Program checks on novelty probes** — an atom may carry `meta.grading` in the
  `grading.checks` schema of `eval score reference`; `skill novelty probe` copies it onto the
  direct and paraphrase probes and runs its non-judge checks on every answer. A check's
  `role` decides what it may do: a failed `veto` makes the answer `wrong_other` whatever the
  judge said, a `sufficient` check decides the verdict, and `evidence` (the default) is only
  recorded. Each checked run keeps `judge_verdict`, `verdict_source` and `checks`, and the
  record gains a `checks` block: how many answers a veto failed and how many of those the
  judge had called correct.
- **`identifiers` reference check** — passes when every identifier the answer names (long
  options by default, inside the `within` lines) is in `known`; `--no-x` counts when `--x` is
  known. Checks may also set `tier` (`verifiable`, `grounded`, `judge`).
- **`skill novelty ground`** — writes an `identifiers` veto built from each `--tool`'s own
  `--help` (with the help's sha256) into the atoms that name that tool, and checks each such
  atom's answer key against the same help: `meta.key_status` is `verified`, `not_found`
  (with the `missing` options; a review flag) or `unverifiable`. See docs/grading-tiers.md.

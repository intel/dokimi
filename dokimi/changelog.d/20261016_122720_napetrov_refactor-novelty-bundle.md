### Added

- **The novelty `bundle` record names what it measured** — `atoms.json` and `probes.json`
  bundle blocks gain `artifact_name`, `artifact_ref` and `measured_digest` (the digest of
  exactly the files measured; every novelty `ref_digest` is this value), and the
  `probes.json` block also lists `files`. Older records still validate.

### Changed

- **A saved answer key survives a new symlink or image** — `novelty probe` no longer
  reports a key as stale when a symlink or a non-text file was added to the skill after
  extraction; an edited measured file or a new file the run would measure still does.
- **Hand-edited or partial `bundle` records read differently** — an `atoms.json` whose
  `bundle` block lacks `policy_excluded_words` or `unreadable_files` now gets them from its
  `excluded` rows, and a count that is not a number (`"n/a"`, `[1]`, `Infinity`) reads as
  0, so the `artifact_coverage` gate value of such a record can differ from before (no
  writer produces them). Records the tool writes gate and report exactly as before.
- **The pre-run note of a bundle run** says "words over the budgets"; it no longer says
  "or filtered by role", since role-filtered words were never in that count.

### Fixed

- **`novelty probe` errors name the file you passed** — a stale-key message quotes the
  `--atoms` path instead of a hard-coded `atoms.json`, and a missing or malformed entry
  point exits with `cannot load ...` instead of a traceback.

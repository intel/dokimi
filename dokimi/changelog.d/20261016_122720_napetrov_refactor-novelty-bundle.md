### Changed

- **Answer keys no longer invalidated by new symlinks or images** — a saved answer key remains valid even if a symlink or image file is added to the skill after extraction, as long as the text content is unchanged.
- **Clarified bundle run note on coverage** — the pre-run message before a bundle run now says "words over the budgets" instead of incorrectly mentioning role filtering, which was never part of the excluded-word count.
- **Unified coverage computation** — the gate, analyzer, and report now compute coverage figures from a single source (`Coverage.from_record()`), eliminating inconsistencies between the three and simplifying maintenance. No coverage values change.

### Fixed

- **Junk counts in a stored coverage record no longer crash** — a non-numeric,
  boolean or non-finite `words` or `unreadable_files` value in an `atoms.json`
  record (`"n/a"`, `[1]`, `Infinity`) now reads as 0 in the gate, `analyze` and
  the report instead of raising.

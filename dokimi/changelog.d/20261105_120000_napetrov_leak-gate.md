### Added

- **`eval leak` checks a candidate skill for held-out answers and locks the dev/val/test split** — it
  reads a skill (a directory or its SKILL.md) and a questions set, flags an item when at least
  `--min-fraction` (default 0.15) of the 8-word runs of its expected answers and checks also occur in
  the skill's text, and exits 1 when an item is flagged. `--allow TERM` and `--allow-file FILE` list terms
  that may appear in both (API names, paths, identifiers). `--split FILE` checks a dev/val/test assignment
  against the lock written at first use (`--lock`, default the split's name with `.lock.json`): a changed
  split exits 1 and names the items that moved, and the lock is never rewritten. `--proposer FILE` (a JSON
  list of question ids) exits 1 when it gives the proposer a test item. Writes a `leak.v1` artifact to
  `--out`. No model is called.

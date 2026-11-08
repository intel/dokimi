### Added

- **`eval leak` checks a candidate skill for held-out answers and locks the dev/val/test split** — it
  reads a skill (a directory or its SKILL.md) and a questions set, flags an item when at least
  `--min-fraction` (default 0.10) of the 8-word runs of its expected answers and checks also occur in
  the skill's text, or when any run of `--verbatim-words` words (default 12) of them occurs in the skill
  whatever the share, and exits 1 when an item is flagged. A skill file that is not UTF-8 text or is a
  symbolic link is not read and is listed as a warning. `--allow TERM` and `--allow-file FILE` list terms
  that may appear in both (API names, paths, identifiers). `--split FILE` checks a dev/val/test assignment
  against the lock written at first use (`--lock`, default the split's name with `.lock.json`). The lock holds
  each item's split and a digest of its grading block, so a changed answer or check counts as a change: a changed
  split exits 1 and names the items that moved, and the lock is never rewritten. `--relock` overwrites the lock
  after a deliberate change, and only when the run passes; a blocked run leaves the old lock and reports the
  digest it would have replaced. The lock is an accidental-edit check, not tamper-proof. `--proposer FILE` (a JSON
  list of question ids) exits 1 when it gives the proposer a test item. Writes a `leak.v1` artifact to
  `--out`. No model is called.

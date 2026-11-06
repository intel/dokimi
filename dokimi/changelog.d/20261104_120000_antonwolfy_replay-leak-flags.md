### Added

- **`repo compare` flags the replay tasks a `guidance:rev:` arm may have seen** — for each compared
  arm whose cells record `arm_rev`, a replay task (its `task.json` has `public_at` or `source_pr`) is
  flagged by date when a guidance file that applies to it has its `guidance_latest` commit committed
  after the task's `public_at`, and by name when a `guidance_commits` subject names its PR (`#<n>`,
  `PR#<n>`, `PR <n>`, `GH-<n>`, `<owner>/<repo>#<n>` of its own repository, or the PR's URL; `#31030` and `other/repo#3103` do not
  name `#3103`).
  A nested `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` or `GEMINI.md` applies to the tasks that patch
  a path under its directory; any other guidance file, and every file for a task with no
  `patch_files`, applies to every task. A task is unknown, with the reason, when it has no
  `public_at` (by date), the arm is a `patch:` arm, the batch recorded no guidance history, a cell
  file has the wrong shape, none of its cells has a `task.json` or one without it ran an `arm_rev` no
  checked cell has (pooled batches), or `guidance_commits_truncated` cut commits that may be later than its
  `public_at` (by name). The `--json` summary's `leak` lists each checked arm's `checked`,
  `not_checked`, `by_date`, `by_name`, `unknown`, `clean` and `names_truncated`; the text prints the
  counts, the tasks named with their commits and, as `--task` options, the clean tasks with the ones
  the check does not read (a hand-written task stays in the comparison). No delta, interval, p or
  exit code changes.
- **`repo tasks replay` writes `patch_files`** — each `task.json` lists the paths `change.patch`
  changes, a rename as both its paths, for `repo compare` to scope a nested guidance file by.

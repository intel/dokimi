### Added

- **`eval tasks generate --from history` builds authoring tasks from bug-fix commits** — no pull
  request file needed. It reads the newest `--commits` (default 300) first-parent non-merge commits of the
  pinned revision, keeps those whose subject `--subject` matches (default: fix, bug, regress, crash, wrong,
  ...), and writes one `authoring_<sha12>/` `dokimi:hidden_tests` task per commit, from its parent, with the
  writer and filters of `--from prs`. A squash subject's `(#<n>)` becomes `source_pr`;
  `public_at` is the author date. Reverts, `fixup!`/`squash!` commits and merges give no task (the merges are
  counted), trailers that name people stay out of the prompt, and one pull request is one task in
  `--out-dir`: `--from prs` and `--from history` each keep the other's task for it. `--dry-run` lists the tasks
  without writing them. A flag of another source is refused, and `--commits`/`--subject` with another source
  (contract §4.8).

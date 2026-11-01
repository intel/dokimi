### Added

- **Replay tasks and `guidance:rev:` arms record what a leak check needs** —
  `repo tasks replay` writes `public_at` into each `task.json`: the earliest
  `createdAt` of a gold thread's first comment. A `guidance:rev:<rev>` cell's
  `meta.json` `arm_rev` adds `guidance_latest` (the last commit to touch each
  guidance file at `<rev>`, or a path a guidance link resolves through),
  `guidance_commits` (the commits that touch a guidance path or a path a link
  resolves through, a merge when its own result changes one, newest first by committer
  date, at most 200) and `guidance_commits_truncated`, each commit with its
  author and committer date, so a reader can see whether a guidance commit
  came after a task's review was public.

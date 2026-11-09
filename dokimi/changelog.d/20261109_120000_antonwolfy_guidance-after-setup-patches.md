### Fixed

- **The guidance arms match the workspace a task's setup patches leave** — they matched
  `guidance.globs` against git's index, which a setup patch (applied with `git apply`) does
  not change. A guidance file the patch adds was missed: `guidance:none`,
  `guidance:as-skill` and `guidance:rev:` kept it, `guidance:bridge` wrote no bridge next to
  it and `guidance:format=xml` left it unrendered. A tracked guidance file the patch deletes
  stopped those three removing arms, `guidance:bridge` and `guidance:format=` with a bare
  `FileNotFoundError` instead of preparing the cell. The globs are now matched against the files the workspace holds, untracked ones
  included, which is what `guidance:overlay` already read.

### Fixed

- **`repo scan` no longer fails a path the repository's `.gitignore` ignores** — guidance
  that names generated or ignored files had each such path reported `fail` as a rename,
  because its parent directory exists and the file is never tracked. A missing path, directory
  (with or without its trailing slash) or glob that a tracked `.gitignore` ignores is now
  `unresolved`, and the reason names the `.gitignore` line. The rules follow gitignore(5), negation and nested files included;
  `.git/info/exclude` and `core.excludesFile` are not part of the snapshot and are not read.

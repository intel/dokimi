### Fixed

- **`skill_sha` and `suite_hash` leave out git-ignored files in a repository whose root name ends in a space or a carriage return** — the work tree's top was read from `git rev-parse --show-toplevel` with all trailing whitespace stripped, so git was then asked about a directory that does not exist, and the digest fell back to hashing every byte on disk, ignored files included. Only git's final newline is removed now.

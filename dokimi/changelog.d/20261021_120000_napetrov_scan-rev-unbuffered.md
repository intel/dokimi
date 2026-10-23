### Fixed

- **`PATH@REV` takes a revision with a slash** — `repo scan .@origin/main` (and `refs/heads/x`) read the whole spec as a path and failed with "not a directory"; a branch with `/` is now a revision for every `repo` command that takes a source. A suffix that is no revision (`a..b`, `x//y`, trailing `/`) still stays in the path, and an existing local directory with an `@` in its path (`ws/job@2/repo`) is read whole.
- **`repo tasks check` and `repo run` progress is flushed per task** — under a pipe (CI) the per-task lines were block-buffered and all appeared at exit unless `PYTHONUNBUFFERED` was set.

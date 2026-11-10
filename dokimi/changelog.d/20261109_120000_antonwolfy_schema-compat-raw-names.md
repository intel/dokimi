### Fixed

- **`python -m dokimi.schema_compat` says so when git prints no repository root** — git
  before 2.25, run outside a work tree, prints nothing for `rev-parse --show-toplevel` and
  exits 0, and following moved schemas then took the working directory for the root: run
  from the repository root the check answered, from a directory between the root and the
  schemas it compared nothing and passed, and from anywhere else it stopped with a
  `ValueError` or a git error about `diff`. The check now stops, from any directory, with a
  git error that names the command: `git rev-parse --show-toplevel: printed no work tree to
  follow moved schemas in`.

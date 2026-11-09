### Fixed

- **`python -m dokimi.schema_compat` says so when git prints no repository root** — git
  before 2.25, run outside a work tree, prints nothing for `rev-parse --show-toplevel` and
  exits 0, and following moved schemas then stopped with a `ValueError`. The check now
  stops with a git error that names the command: `git rev-parse --show-toplevel: printed
  no work tree to follow moved schemas in`.

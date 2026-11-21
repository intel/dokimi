### Added

- **`report opportunities`** (prototype) — ranks novelty, repo_static and llms.txt findings
  into one queue of fixes on an impact x effort grid, each with a stable `source_key` and an
  execution prompt for a coding agent. No model calls. `--format json` writes `opportunities.v1`.

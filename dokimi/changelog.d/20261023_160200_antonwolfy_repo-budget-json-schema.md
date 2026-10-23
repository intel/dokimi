### Added

- **repo budget: `repo_budget.v1` schema** — `repo budget --json` and `--out` now
  carry a `schema_version` and validate against `repo_budget.v1`, as `repo scan`,
  `repo inspect` and `repo reach` do, so a CI gate on the JSON can detect a
  changed shape. `--out` writes through the artifact writer: it creates missing
  parent directories and refuses a payload that does not match the schema.

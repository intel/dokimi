### Added

- **Key-free static catalog checks** — `catalog eval ROOT --tier static --out-dir DIR` plans deterministic catalog checks,
  maps actual tasks by their owning skill, preserves checks-only imports and missing
  runtime coverage, and writes schema-validated catalog, plan and result receipts.
  `--changed-from` selects affected skills with conservative handling of unknown changes.
  No model, skill script, Docker task or verifier is executed by this command.

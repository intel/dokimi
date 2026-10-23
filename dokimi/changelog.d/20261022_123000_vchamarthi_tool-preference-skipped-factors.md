### Added

- **`tools preference` names the groups a factor is skipped for** — a group whose twin has
  no `vendor_name`, `detail` or `exclusion` silently dropped that factor's items, so the
  effect was estimated from fewer groups than the file holds. The command now prints one
  line per affected factor, with the group ids, before any call is made (visible in
  `--dry-run`). Item counts and artifacts are unchanged.

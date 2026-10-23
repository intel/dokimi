### Added

- **`tools preference` names the groups a factor is skipped for** — a group whose twin has
  no `vendor_name`, `detail` or `exclusion` silently dropped that twin's items for the
  factor, so the effect rested on fewer groups, or on one twin of a group. The command now
  prints one line per affected factor, with the group ids (and the twin, when only one
  lacks the field), before any call is made (visible in `--dry-run`). Item counts and
  artifacts are unchanged.

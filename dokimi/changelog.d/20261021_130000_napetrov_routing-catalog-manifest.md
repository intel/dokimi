### Added

- **`novelty routing --competitors-from-catalog <dir>`** is the same flag as `--catalog`, so
  the name a harness-facing script expects works. The catalog's `skills.yaml`, when present,
  now decides which skills are competitors, as it does for `catalog scan`; without one, every
  `SKILL.md` under the directory counts as before. A declared skill that cannot be loaded is
  skipped with a warning instead of making the field unreadable.

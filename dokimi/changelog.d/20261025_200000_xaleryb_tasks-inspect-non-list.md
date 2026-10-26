### Fixed

- **`dokimi tasks inspect` no longer stops on one malformed file** — a promptfoo config with
  `tests: 5`, a dokimi questions file with `"questions": null`, a promptfoo tests file with a
  non-string row key, or an explore task with `lines: [.inf]` raised and ended the scan, so no
  other file under the path was reported. Each file is now reported on its own and the scan
  goes on.

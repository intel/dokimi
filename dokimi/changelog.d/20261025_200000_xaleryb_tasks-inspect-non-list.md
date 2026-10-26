### Fixed

- **`dokimi tasks inspect` no longer crashes on a non-list `tests` or `questions`** — a
  promptfoo config with `tests: 5` or a dokimi questions file with `"questions": null` raised
  `TypeError` and ended the scan, so no other file under the path was reported. The value is
  now read as no items and the scan goes on.

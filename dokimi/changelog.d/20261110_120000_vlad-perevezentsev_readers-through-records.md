### Fixed

- **`report model-compare` refuses a run whose `answers` or `evaluations` is
  not a list** — a run file with `"evaluations": null` (or `{}`, or the same
  for `answers`) was read as having no records, and the report was written
  with 0 common questions and exit 0. It now exits 2 naming the file
  (`'evaluations' must be a list, got NoneType`), as `eval judge` already
  did. A file without the field still reads as having no records.

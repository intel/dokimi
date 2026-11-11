### Fixed

- **`report model-compare` refuses a run whose `answers` or `evaluations` is
  not a list** — a run file with `"evaluations": null` (or `{}`, or the same
  for `answers`) was read as having no records, and the report was written
  with 0 common questions and exit 0. It now exits 2 naming the file
  (`'evaluations' must be a list, got NoneType`), as `eval judge` already
  did. A file without the field still reads as having no records.

- **No false "cite judge material that is missing" warning for a failed
  arm** — reading an arms file's `judge_materials` (`eval judge validate
  --context stored`, `eval judge agreement --judges`) counted every arm
  stored as `null`, and every record without `arms`, as one that cites
  missing material. Only an arm whose `judge_context_sha256` has no matching
  text is counted now. In an artifact that has `judge_materials`, a malformed
  answers list or record is now a `RecordError`, as in every other reader,
  instead of being skipped. A record that carries both "id" and
  "question_id" is now keyed by "question_id", as the judges look it up; it
  was keyed by "id".

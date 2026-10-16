### Added

- **`questions.v2` schema with required `question` field** — every writer now generates records with `question` instead of `text`, validated against `questions.v2.json`. Records with `question` are no longer invalid under v1 in memory; v1 remains the read-only schema for curated golden sets.
- **Tolerant question reading** — `load_questions` reads v1, v2, and legacy shapes (bare lists, `question_text`/`question_id` keys, missing ids, list-form `metadata`), and works on the set as v2 in memory, with one-line notices for legacy shapes. A set invalid after reading fails before any model call, naming each failing record and the fix.

### Changed

- **No writing over input files** — `--questions-from` and `--custom-questions` are never rewritten by a read operation. The pipeline writes its own v2 copy to `<output-dir>/questions/<product>.json`. A `--custom-questions` file is written only through the Python API when `--force-regen` merges generated questions with the custom set, and only then (no CLI command can reach this path). Input `source_type` is preserved when a same-path merge write reloads the file.
- **Question generation errors now exit the pipeline early** — `answers generate`, `report generate` and `arms run` now validate the merged question set before any LLM call, refusing records with empty questions or mistyped fields, with each failing record and the fix named in stderr.

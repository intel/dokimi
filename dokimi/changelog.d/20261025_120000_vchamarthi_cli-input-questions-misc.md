### Fixed

- **`questions`, `personas`, `dashboard`, `library`, `subjects` and `ingest docling`** exit with a message on a missing, unreadable or invalid input file or an unusable output path, instead of a traceback. `library` now exits 1 on a missing `--registry` or `--intents` file, where it used to log a warning and print an empty result.

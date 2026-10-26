### Fixed

- **`questions`, `personas`, `library` and `subjects`** exit with a message on a missing, unreadable or unparseable input file, and those commands, `dashboard` and `ingest docling` on an unusable output path, instead of a traceback. `library` now exits 1 on a missing `--registry` or `--intents` file, where it used to log a warning and print an empty result.

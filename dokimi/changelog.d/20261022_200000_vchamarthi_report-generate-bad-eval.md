### Fixed

- **`report generate` exits 1 with a message on an unreadable eval file** — a
  missing path, a file that is not JSON or not UTF-8, JSON that is not an
  object, or an `evaluations` value that is not a list now prints
  `Error: ...` naming `--eval` instead of a Python traceback.

### Fixed

- **`report generate` exits 1 with a message on an unreadable eval file** — a
  missing path, a file that is not JSON, or JSON that is not an object now
  prints `Error: ...` naming `--eval` instead of a Python traceback.

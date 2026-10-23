### Fixed

- **`baseline save`, `list` and `compare`** exit with a message naming the file, not a
  traceback, when the eval file, the saved baseline or `manifest.json` is not valid JSON,
  is not a JSON object, or cannot be read. `save` and `list` exit 1; `compare` exits 2,
  since 1 is the `--strict` verdict; that includes a missing `--eval` file, which used to
  exit 1.

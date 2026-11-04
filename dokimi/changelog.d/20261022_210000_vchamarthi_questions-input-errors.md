### Fixed

- **`eval questions analyze`** and **`eval questions review`** exit 1 with
  `Error: cannot read questions from <file>: ...` when the questions file is not
  valid JSON, not UTF-8 or a directory, instead of a Python traceback.
  `eval questions analyze` did the same for a missing file.

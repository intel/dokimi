### Fixed

- **`dataset export`** prints `Error: cannot export <input> to <out-dir>: <reason>` and
  exits 2 when the input file is missing, is not JSON, fails schema validation, lacks
  the record array for its `--kind` (with `--no-validate`), or the output directory
  cannot be written, instead of ending in a Python traceback.

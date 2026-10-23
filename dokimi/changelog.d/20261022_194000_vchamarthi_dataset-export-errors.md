### Fixed

- **`dataset export`** prints `Error: cannot export <input> to <out-dir>: <reason>` and
  exits 2 when the input file is missing, is not JSON, fails schema validation, lacks
  the record array for its `--kind` (with `--no-validate`), or the output directory
  cannot be written, instead of ending in a Python traceback. For `answers`, `eval`,
  `arms` and `task_runs` the input may also be any JSON value that is not an object.
  `dokimi.datasets_export.flatten` and
  `dokimi.artifacts.load_artifact` raise `ValueError` for these inputs, where
  `flatten` raised `KeyError` or `TypeError` before.

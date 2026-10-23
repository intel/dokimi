### Fixed

- **`baseline save`, `list` and `compare`** exit with a message naming the file, not a
  traceback, when the eval file, the saved baseline or `manifest.json` is not valid JSON,
  is not a JSON object, or cannot be read, and when `manifest.json` holds a `baselines`
  value that is not a list or holds an entry without a string `name` and `path` or with a
  `summary` that is not an object (`save` used to leave an orphan baseline file behind). A
  manifest with no `baselines` key is treated as empty. `save` and `list` exit 1; `compare`
  exits 2, since 1 is the `--strict` verdict; that includes a missing `--eval` file, which
  used to exit 1.
- **`baseline compare`** treats a `run_metadata` that is not an object, or a
  `panel_config.models` that is not a list of strings, as a model the artifact does not
  record (named on stderr as not compared) instead of raising a traceback.

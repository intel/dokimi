### Fixed

- **`novelty probe` and `novelty shorten`** exit with a message on an `--atoms` file whose spans, atoms, `extraction` or `bundle` have a missing or wrong-type field or whose JSON is nested too deeply to parse, and `novelty view` does the same for deep nesting, instead of a traceback.

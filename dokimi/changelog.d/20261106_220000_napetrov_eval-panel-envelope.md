### Fixed

- **`eval score panel` output carries the run envelope**: its incremental output is saved through `save_artifact` as an `eval.v1` artifact, so it gets `schema_version`, `run_id` and `dokimi_version` and shows in `runs list`. A save the schema rejects stops the run on the first save, not after the last judge call.

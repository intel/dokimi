### Fixed

- **`run`, `compare`, `arms`, `tasks`, `explore` and `repo`** exit with a message on an unreadable, non-UTF-8 or corrupt input or an unwritable output, instead of a traceback. `run` and `compare` exit 2 on a bad spec or snapshot (a non-numeric summary value, or fewer documents than the manifest's `min_docs`) and exit 1 when an output cannot be written, as `arms` does. `repo report` and `repo compare` exit 1 on a result file without a usable `grade`. `tasks` error messages use the `Error:` prefix.

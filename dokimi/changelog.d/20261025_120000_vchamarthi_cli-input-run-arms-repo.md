### Fixed

- **`run`, `compare`, `arms`, `tasks`, `explore` and `repo`** exit with a message on an unreadable, non-UTF-8 or corrupt input or an unwritable output, instead of a traceback. `run` and `compare` exit 2 on a bad spec or snapshot.

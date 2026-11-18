### Fixed

- **`dokimi repo inspect` no longer ends in a `PermissionError` traceback on a git working tree with a tracked file in a directory that cannot be searched** — the file is left out of the listing, as the directory walk of a plain directory leaves it out, and the report covers the readable rest.

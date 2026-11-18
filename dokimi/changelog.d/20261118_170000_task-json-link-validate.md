### Fixed

- **`repo validate` reports a `task.json` that is a link to itself or to a directory instead of exiting 5 with a traceback** — `_check_task` caught only `json.JSONDecodeError` and `UnicodeDecodeError` around the read, so such a link raised `OSError` in a working tree and `SourceError` at `<dir>@rev`. Both are now an `error:` line `<task dir>/task.json: cannot read task.json: ...` and exit 1, as for a `task.json` that is not valid JSON. (#910)

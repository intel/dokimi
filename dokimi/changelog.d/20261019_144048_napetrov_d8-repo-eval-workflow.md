### Fixed

- **`repo validate` refuses task ids that contain whitespace** — a task directory whose name has a space or tab is now a `task.json` error instead of passing validation. The repo-eval workflow's `tasks`, `smoke-tasks` and `exclude-tasks` inputs are whitespace-separated lists of ids, so such a task could never be named there. Rename the task directory to a name without whitespace.

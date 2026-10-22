### Changed

- **`repo validate` refuses task ids that contain whitespace or a comma** — a task directory whose name has a space, tab or comma is now a `task.json` error instead of passing validation. The repo-eval workflow's `tasks`, `smoke-tasks` and `exclude-tasks` inputs are split on whitespace and commas, so such a task could never be named there. Rename the task directory to a name without whitespace or commas.

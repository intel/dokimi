### Fixed

- **Two `docker-*` cells of one task in one process no longer share a
  container name** — the name `dokimi-run-<task>-<pid>` gets a random suffix,
  so one cell's cleanup cannot remove the other's container.

### Fixed

- **`explore list` and `explore run --all` refuse a tasks root with no task, exit 2** — with no
  `--tasks-root` and an empty bundled `data/explore_tasks`, or with a `--tasks-root` that is missing
  or holds no YAML, `explore list` printed nothing and exited 0, and `explore run --all` exited 1
  with a message that did not name the flag. Both now print to stderr what was searched, name
  `--tasks-root`, and exit 2. A missing bundled root (an installed package) exited 1 and now exits 2.
  `explore run --tasks <id>` for an id no root holds, and `run` with no selection, also exit 2
  (they exited 1). A malformed, unreadable or duplicate-id task file exits 2 for `list` and `run`
  too (it exited 1). A bundled root that has tasks still lists them with no flag.

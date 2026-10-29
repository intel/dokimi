### Added

- **CLI route table** — the top-level `dokimi --help` lists the commands by
  section (The loop, Subjects, Evals, Analysis and data), and every command's
  help line ends in `[free]` or `[paid]`: `paid` when the command can make a
  model call in some flag combination. The table lives in `dokimi/cli_routes.py`;
  `dokimi data schema show cli_routes` prints it as JSON.
- **`dokimi data schema list|show KIND`** — the new `data` command group; `show`
  prints a schema kind as JSON. `cli_routes` is the only kind for now.
- **Tombstones** — the route table can name an old argv prefix that moved; running
  it prints `moved: dokimi <new command>` and exits 2 without running anything.
  No command has moved yet, so the table is empty.

### Changed

- `dokimi run` and `dokimi compare` have a one-line help in the top-level list.
  Their parsers have no help text, so the route table supplies it.

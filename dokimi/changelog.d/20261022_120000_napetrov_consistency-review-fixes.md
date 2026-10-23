### Fixed

- `context consistency`: shell control syntax (`for`/`do`/`done`, `if`, subshells, comments) is no longer a step; a shell edit matches an `edit` action, and a command whose output is saved to a file (`pytest > test.log`) does not; an empty arm is "no runs"; malformed `--outcomes`/`--contract` files end in a clean error; an outcome key shared by two traces is refused; a trace given twice to one arm (a repeated `--arm`, overlapping globs) counts once.
- `repo compare --path-stability`: an empty trace is unmeasured, not a steady path, and a deeply nested line no longer aborts the comparison.

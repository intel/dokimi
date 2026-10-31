### Added

- **`novelty routing --include-invalid`** keeps a catalog skill with a spec error (a description
  over 1024 characters, a name that is not its directory name) in the `--catalog` field. Off by
  default: such a skill is left out, with a warning on stderr naming the error.

### Changed

- **`catalog scan` reports a same-name skill with different content as a `name-conflict` warning**
  naming the other path. Before, each of the two skills was reported `ok`. It is a warning, not an
  error, so `--strict` does not fail on it.

### Fixed

- **`novelty routing --catalog` skips a name that two different skills share, with a warning,
  instead of aborting the whole run** (`catalog has X at A and B with different content`, exit 2).
  Both skills of that name leave the field, the rest of it stays, and stderr names every path.
- **`catalog scan` on a root with no `SKILL.md` exits 1 with `no SKILL.md found under <root>`**
  instead of exiting 0 silently. With `--kind all`, a tasks-only root only warns, and `--strict`
  makes that a failure.

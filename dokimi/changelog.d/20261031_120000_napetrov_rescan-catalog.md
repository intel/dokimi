### Added

- **`novelty routing --include-invalid`** keeps a catalog skill with a spec error in the `--catalog`
  field, and the saved result records the flag under `field`.

### Changed

- **`novelty routing --catalog` leaves out a skill with a spec error by default** (for example a
  description over 1024 characters, or a name that is not kebab-case or not its directory name), with
  a warning on stderr naming the error. Before, such a skill was in the field. `--include-invalid`
  keeps it.
- **`catalog scan` reports a same-name skill with different content as a `name-conflict` warning**
  naming the other path. Before, each of the two skills was reported `ok`. It is a warning, not an
  error, so `--strict` does not fail on it.

### Fixed

- **`novelty routing --catalog` skips a name that two different skills share, with a warning,
  instead of aborting the whole run** (`catalog has X at A and B with different content`, exit 2).
  Every copy of that name leaves the field, the rest of it stays, and stderr names every path.
- **`catalog scan` on a root with no `SKILL.md` exits 1 with `no SKILL.md found under <root>`**
  instead of exiting 0 silently. With `--kind all`, a tasks-only root only warns, and `--strict`
  makes that a failure.

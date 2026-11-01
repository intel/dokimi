### Added

- **`novelty routing --include-invalid`** keeps a catalog skill with a spec error in the `--catalog`
  field, and the saved result records the flag under `field`. A name that conflicts with another
  catalog skill still leaves the field.

### Changed

- **`novelty routing --catalog` leaves out a skill with a spec error by default** (for example a
  description over 1024 characters, or a name that is not kebab-case or not its directory name), with
  a warning on stderr naming the error. Before, such a skill was in the field. `--include-invalid`
  keeps it. A name with two different copies also changes
  the field, so a routing result saved before this change is not comparable with one saved now for the
  same `--catalog`: rerun it to compare. With `--include-disabled`, a copy that is
  `disable-model-invocation` counts as one of the copies.
- **`novelty routing --catalog` skips a name that two different skills share, with a warning, instead of
  refusing the run** (exit 2). Every copy of that name leaves the field, the rest of it stays, and stderr
  names every path.
- **`catalog scan` reports a same-name skill with different content as a `name-conflict` warning**
  naming the other path and that `novelty routing --catalog` leaves the name out of the field. Before,
  each of the two skills was reported `ok`. It is a warning, not an error, so `--strict` does not fail on it.

### Fixed

- **`catalog scan` on a root with no `SKILL.md` exits 1 with `no SKILL.md found under <root>`**
  instead of exiting 0 silently. With `--kind all`, a tasks-only root only warns, and `--strict`
  makes that a failure. A root with a `skills.yaml` that has no skill that can be scanned says
  `skills.yaml under <root> has no skill that can be scanned` instead.

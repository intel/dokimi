### Fixed

- **`novelty routing --catalog` counts a mirrored skill once** — a `SKILL.md` that is
  byte-identical under two paths (a plugin copy of the `skills/` tree) used to put the same
  name in the field twice, and the run was refused as naming a competitor more than once.
  The copy nearest the catalog root stands for the card; the others are named on stderr.

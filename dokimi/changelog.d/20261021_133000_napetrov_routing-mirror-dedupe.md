### Fixed

- **`novelty routing --catalog` with a `skills.yaml` counts a mirrored skill once** — a catalog
  that lists the same `SKILL.md` bytes under two paths (a plugin copy of the `skills/` tree)
  used to put the name in the field twice, and the run was refused as naming a competitor more
  than once. The copy nearest the catalog root stands for the card; the others are named on stderr.

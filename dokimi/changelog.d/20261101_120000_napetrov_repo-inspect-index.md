### Added

- **`dokimi repo inspect --index FILE`** — reads a SCIP code index of the tree
  and adds a `structure` section to `repo_environment.v1`: modules with their
  reference fan-in, cross-tree bridges, name ambiguity and whether the
  guidance names them, plus `info` findings for the hotspots. Tree files of
  the indexed kinds that the index lacks are counted, so an index cut short
  does not read as complete.

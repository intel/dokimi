### Changed

- **`eval judge-validity --context stored --sequential` exits 2 when an arm with
  verified material got no scored base** — the SPRT can decide on the other arms
  and stop first; the run now refuses, as fixed mode does, instead of passing
  without covering that arm.

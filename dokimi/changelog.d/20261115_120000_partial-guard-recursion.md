### Fixed

- **A routing result file nested too deep to parse is treated as no partial result, and a capped `novelty routing` run no longer raises `RecursionError` while saving** — the overwrite guard keeps such a file and writes the partial result to a sidecar beside it, as it does for any file it cannot read.

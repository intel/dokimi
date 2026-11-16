### Fixed

- **A routing result marked `"status": "partial"` without a `partial` block is a partial result to the overwrite guard** — a capped `novelty routing` run may replace it, and a finished result is kept as before. `novelty items` already refused such a probe file.

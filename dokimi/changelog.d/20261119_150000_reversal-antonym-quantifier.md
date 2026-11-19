### Fixed

- **`dokimi skill novelty` no longer drops a valid reversal wording for being an antonym or quantifier inversion, or a tag question** — the reversal check accepts `entirety`/`always` against a source that limits scope, and `skip`, `unless`, `unreliable`, `useless`, `unsuitable`, `unhelpful` and `wrong`; a trailing `..., doesn't it?` is question phrasing, not a negation. Each kind's `probes.by_kind.<kind>.rejected_examples` keeps the first 20 rejected wordings with their source and reasons, and the rejected-rate warning names it.

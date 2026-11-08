### Fixed

- **A Claude CLI alias is the model it runs** — `report` no longer warns that cells run with `sonnet` were answered by `claude-sonnet-…`, and `baseline compare` reports such a model as not verified (not a mismatch). Model identity now has one rule, the CLI-aware one, in `dokimi.utils.same_model`. The novelty default judge now also skips a dated snapshot or alias of the answerer's family.

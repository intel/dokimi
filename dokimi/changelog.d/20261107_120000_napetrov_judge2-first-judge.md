### Changed

- **`novelty --judge2` is refused when it is the first judge's model** — a second judge that is the same
  model as `--judge-model` (by the same model-identity rule as the answerer check, so an equivalent API
  id and a Bedrock id count, and a CLI alias counts on a CLI route) now exits 2 before any call, unless
  `--allow-self-judge` is passed. Its agreement with the first judge is not an independent second opinion.

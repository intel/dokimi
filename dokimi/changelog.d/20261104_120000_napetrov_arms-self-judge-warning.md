### Changed

- **Self-judging warning on judged arms and subjects runs** — `arms run --judge` and
  `subjects run --judge` now print the same evaluator-independence warning as `evaluate`,
  `benchmark` and `orchestrate` when the judge model is the answer model, for example
  `--judge-provider openai`, which resolves to the default answer model `gpt-6.1-sol`.
  `--judge-panel` is covered too, since every panel role runs on the judge model. Warning
  only: the run goes ahead and no defaults changed.

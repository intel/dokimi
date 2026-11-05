### Changed

- **Self-judging warning on judged arms and subjects runs** — `arms run --judge` and
  `subjects run --judge` now print the same evaluator-independence warning as `evaluate`,
  `benchmark` and `orchestrate` when the judge model is the answer model, for example
  `--judge-provider openai`, which resolves to the default answer model `gpt-6.1-sol`.
  `--judge-panel` is covered too, since every panel role runs on the judge model. Warning
  only: the run goes ahead and no defaults changed.
- **Self-judging warning on saved answers and generated questions** — `eval score pairwise`,
  `eval score checklist`, `eval judge wording` and `eval judge agreement --judges` warn for each
  judge (escalation judge included) that is the model the answers file records; `questions panel`
  and `questions analyze` warn when the reviewing model is the one the questions file records as
  its generator. Files without `model`/`provider` stay silent.

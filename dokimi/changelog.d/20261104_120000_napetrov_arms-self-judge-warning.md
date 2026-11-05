### Changed

- **Self-judging warning on more judge paths** — the evaluator-independence warning that
  `evaluate`, `benchmark` and `orchestrate` print now also fires on:
  - `arms run --judge` and `subjects run --judge` (and `--judge-panel`, whose roles all run on
    the judge model), when the judge model is the answer model, for example
    `--judge-provider openai`, which resolves to the default answer model `gpt-6.1-sol`;
  - `eval score pairwise` (escalation judge included), `eval score checklist`,
    `eval score reference`, `eval judge validate` (escalation judge included),
    `eval judge wording` and `eval judge agreement --judges`, for each judge that is the
    model the answers file records in its top-level `model`/`provider`;
  - `eval questions analyze` and `eval questions review`, when the reviewing model is the one
    the questions file records as its generator (the default for both, since they share the
    generator defaults); the message names the generator and reviewer.

  Files that record no `model`/`provider` stay silent. Warning only: every run goes ahead
  and no defaults changed. The helper is `dokimi.utils.warn_judge_independence`, with
  `warn_recorded_self_judge` for saved files.

### Changed

- **One default-judge rule on every surface** — `arms run`, `arms matrix-run`, `subjects run`,
  `benchmark`, `eval` and `evaluate` took the default judge from a rule of their own that ignored
  the answerer, and `novelty` took it from another that did not. They all take it from
  `judge_rule.default_judge` now: the default judge on the judge's provider, unless it is the model
  under measurement, then the default generator on that provider. Two visible effects:
  - **`--provider claude-cli` judges with an alias on the claude CLI.** With no `--judge-model`
    and no `--judge-provider`, the judge is the first of `opus`, `sonnet`, `haiku` of another family
    than the answerer, on provider `claude-cli` (was the full id `claude-sonnet-5-5` on `anthropic`);
    a gpt answerer gets `opus`. A library `ArmsRunConfig` with a claude-cli answerer does the same.
  - **A judging run does not judge its own answers.** A run whose answerer is the default judge on
    its provider (`--model claude-sonnet-5-5` with the default `--judge-provider anthropic`, or
    `--model gpt-6.1-sol --judge-provider openai`) now exits before any answer and asks for
    `--judge-model`, as `novelty` already did. A run that does not judge (`arms run` without
    `--judge`, `subjects run` without `--judge`, `benchmark preflight`) is not refused.
    A matrix (`arms matrix-run --matrix-cells`) has one judge for all its cells, resolved without
    a cell's model; a cell whose model is that judge is still warned about, not refused.

### Changed

- **One default-judge rule on every surface** — `arms run`, `arms matrix-run`, `subjects run`,
  `benchmark`, `eval` and `evaluate` took the default judge from a rule of their own that ignored
  the answerer, and `novelty` took it from another that did not. They all take it from
  `judge_rule.default_judge` now: the default judge on the judge's provider, unless it is the model
  under measurement, then the default generator on that provider. Two visible effects:
  - **Runs judged on `claude-cli` judge with an alias.** With no `--judge-model`, the judge is `opus`
    on provider `claude-cli` (`sonnet` when the answerer is `opus`), for `--provider claude-cli` with
    no `--judge-provider` and for an explicit `--judge-provider claude-cli` alike. Before, the first
    took `claude-sonnet-5-5` on `anthropic` and the second `claude-sonnet-5-5` on `claude-cli`.
    A library `ArmsRunConfig` with a claude-cli answerer does the same.
  - **A judging run does not judge its own answers.** A run whose answerer is the default judge on
    its provider (`--model claude-sonnet-5-5` with the default `--judge-provider anthropic`, or
    `--model gpt-6.1-sol --judge-provider openai`) now exits before any answer and asks for
    `--judge-model`, as `novelty` already did. `benchmark preflight` checks the plan of
    `benchmark run`, so it refuses the same flags. A run that does not judge (`arms run` without
    `--judge`, `subjects run` without `--judge`) is not refused.
    A matrix (`arms matrix-run --matrix-cells`) has one judge for all its cells, resolved without
    a cell's model; a cell whose model is that judge is still warned about, not refused.
  - **An explicit `--judge-model` also moves the judge provider.** With `--provider claude-cli` and
    `--judge --judge-model claude-opus-5-5` and no `--judge-provider`, the judge was the Anthropic API
    model before; it is now the same model on `claude-cli`, the subscription. A `--judge-model` from
    another family is refused: the message says the judge follows `--provider claude-cli` and asks for
    `--judge-provider`.
  - **Scores of runs judged on `claude-cli` change.** Their judge is the alias above, so their scores
    are not comparable with earlier runs. Re-baseline them, as with the earlier default changes.

### Fixed

- **"Same model" means one thing in `baseline compare`, the self-judge warning and `repo compare`** — they
  used two rules that disagreed. A dated id (`claude-sonnet-4-5-20250929`) and its undated alias, a Bedrock
  regional profile (`eu.anthropic.…`) and its bare id, and the same model behind a route
  (`anthropic/`, `openrouter/anthropic/`, `bedrock/`, `vertex_ai/`) or a `-v1:0` revision are now one model
  everywhere; two different dated snapshots are still two models, and case no longer matters. Effects: the
  self-judge warning fires when the answer and the judge are the same model on another provider too; `baseline
  compare` no longer reports or refuses (`--strict`) a judge or answer that only changed its route, region or
  snapshot alias; `repo compare` pairs arms by the same rule. Scores are not recomputed, but a comparison that
  used to refuse or warn about such a pair may now pass.
  `dokimi run` stores `run_metadata.evaluator_independence_warning` by this rule too, as `dokimi eval` does (it
  was an exact provider-and-model match, so the printed warning and the stored flag disagreed).

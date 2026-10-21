### Fixed

- **`repo run` tokens now cover the same agents as its cost.** `result.json`
  took `input_tokens`/`output_tokens` from Claude Code's parent-only `usage`
  while `cost_usd` covered subagents too; tokens are now summed over
  `modelUsage` when the harness reports it. The agent record gains
  `cache_read_tokens`, `cache_write_tokens`, `usage_scope` (`tree`, `main` or
  `null`) and `subagent_calls`, and an `events.jsonl` event a subagent made
  carries `agent`. Token totals of earlier batches where the agent delegated
  are lower than they should be; re-run those cells to compare them.

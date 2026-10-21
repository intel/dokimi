### Fixed

- **Harbor-backed cells are graded from Harbor's `result.json`** — `codex`,
  `claude-code` and `terminal-bench:<agent>` now read the trial's
  `<run-dir>/<job>/<trial>/result.json` instead of `harbor`'s exit code and
  stdout. Harbor exits 0 for a trial that raised, so an agent install or
  environment failure used to be recorded as a pass. Now a trial with
  `exception_info` (top-level or on a step of a multi-step task), a trial with
  no reward in [0, 1] in `verifier_result.rewards.reward`, or no trial file at
  all is an errored cell (`metrics.error`), out of `pass_rate` and the paired
  test. The exception is not an error when it is a `VerifierTimeoutError` and
  nothing else: that trial is a scored 0 (`metrics.verifier_timeout`) that stays
  in `pass_rate`, since a verifier that overran its timeout is a failure, not
  missing data. The reward otherwise comes from that file only, never from
  Harbor's printed `Reward` table. Pass rates of earlier Harbor-backed runs
  that had errored trials were too high; rerun to compare.

### Added

- **Harbor token and cost metrics** — a Harbor cell records `prompt_tokens`,
  `completion_tokens`, `cache_read_tokens`, `total_tokens` and `cost_usd` from
  the trial's `agent_result` (summed over the steps of a multi-step task), so
  `summary.per_harness.<h>.cost` covers Harbor arms, plus `metrics.n_trials`,
  `metrics.result_json`, and for a trial that raised `metrics.harbor_exception`
  and any `metrics.reward_after_exception`.
  `command:` templates, including ones that wrap `harbor`, are graded as before.

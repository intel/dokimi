### Fixed

- **A boolean task reward is not a score** — `summarize_task_results` no longer
  folds `reward: true` into `avg_reward` as 1.0, or `false` as 0.0. A bool is a
  flag, not a measurement, the same rule the cost total already uses. A numeric
  reward is unchanged.

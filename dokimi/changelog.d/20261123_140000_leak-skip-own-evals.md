### Fixed

- **`eval leak` no longer indexes the skill's own `evals/` directory as skill text** — a skill in the skill-creator layout keeps its evals in `evals/evals.json`, so questions imported from that file were compared with themselves and every item was flagged at a share of 1.00. An entry named `evals` directly under the skill (the directory, or a file of that name) is now left out of the text and of the `files` column, as the agent's plugin copy leaves it out; an `evals` directory deeper down (`references/evals/`) is still read. (#1052)

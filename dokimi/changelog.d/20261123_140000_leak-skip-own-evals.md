### Fixed

- **`eval leak` no longer indexes the skill's own `evals/` directory as skill text** — a skill in the skill-creator layout keeps its evals in `evals/evals.json`, so questions imported from that file were compared with themselves and every item was flagged at a share of 1.00. The `evals/` directory directly under the skill is now left out of the text and of the `files` column; an `evals` directory deeper down (`references/evals/`) and a file named `evals` at the root are still read. (#1052)

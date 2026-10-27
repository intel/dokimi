### Added

- **`dokimi-design-evals` skill (draft)** — `data/skills/dokimi-design-evals/`
  guides an agent through designing evals for a skill or repository: sources,
  a per-scenario template, outcome/process/cost layers, verifier self-test
  with the existing `repo validate`, `repo tasks check` and `docker-oracle` /
  `docker-probe` commands, manual mutation controls, coverage report and
  `draft → reviewed → holdout` statuses. Bundled skills are now covered by
  the docs command check and a new frontmatter/link test.

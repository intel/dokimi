### Fixed

- **repo-eval: a hung oracle leaves a verdict** — the Summary and Upload steps of
  `repo-eval.yml` run with `always()`, and a job cancelled by `timeout-minutes` before
  `check.json` exists records a `timeout` verdict for the task. The self-check
  (`repo-eval-self.yml`) now also runs on changes to `commands/repo*.py`,
  `schemas/repo_*.json`, `requirements.lock.txt` and `pyproject.toml`.

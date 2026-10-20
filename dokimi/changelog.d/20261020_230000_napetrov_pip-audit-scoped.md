### Changed

- **`pip-audit` is its own workflow, `.github/workflows/pip-audit.yml`**, run on a PR or
  push that changes a lock, a requirements file or `pyproject.toml`, and daily on `main`.
  A daily run that finds a new advisory opens (or comments on) one issue. Before, it was a
  step of `ci.yml`'s `static` job, so an advisory published against an existing pin failed
  every open PR, whatever it changed.

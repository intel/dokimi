### Fixed

- **`repo tasks check --warm --allow-unsupported` judges the host** — `--warm` skipped the `host` check, so the flag never let a task through: a task whose host the runner missed still failed as `broken` (null passes). With `--allow-unsupported`, `--warm` now probes the host and reports such a task `unsupported`; `--warm` alone is unchanged.

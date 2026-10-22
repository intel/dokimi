### Added

- **repo-eval workflow: `allow-unsupported` input** — passes `--allow-unsupported` to `repo tasks check`, so a task whose task.json `host` requirements the runner misses (a race that needs more CPUs, another vendor) passes its job instead of failing it. Broken tasks and failed host probes still fail. Off by default.

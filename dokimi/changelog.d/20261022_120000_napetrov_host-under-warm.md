### Fixed

- **`repo tasks check --warm` judges task `host` requirements** — before, `--warm` skipped the
  host verdict, so on a host that misses a task's requirements the untouched-tree null run
  could pass and the task was reported `broken` instead of `unsupported`, and
  `--allow-unsupported` could not pass it. The probe is the same container view (affinity,
  cgroup CPU quota) with or without network; `repo run` already judged it.

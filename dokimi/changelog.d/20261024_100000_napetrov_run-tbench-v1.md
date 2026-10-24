### Added

- **terminal-bench v1 tasks run natively** — a `task.yaml` task
  (laude-institute/terminal-bench `original-tasks/`) loads as a `TaskSpec` and
  runs under `docker-oracle` / `docker-claude` as terminal-bench runs it:
  `solution.sh` at `/oracle`, `run-tests.sh` and `tests/` at `/tests`, the
  image's user, `TEST_DIR` and the compose environment, and the pytest summary
  as the verdict. A task one `docker run` cannot reproduce (extra services,
  volumes, tmpfs, ports; `solution.yaml`; `run_tests_in_same_shell`) is refused
  with the reason. Harbor-backed harnesses and `benchflow*` run a Harbor copy
  written lazily into a content-keyed cache; its `test.sh` scores the same
  pytest summary. `tasks inspect` lists `docker-*`, `harbor` and `benchflow` as
  consumers. A v1 task gets 8 GB of memory
  (its compose `deploy.resources.limits.memory` when set; the default is
  `DOKIMI_TBENCH_V1_MEMORY_MB`, else 8192), where other formats default to 2 GB.

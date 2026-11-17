### Fixed

- **The `repo run`, `eval check` and `tasks generate` help and the repository-evaluation contract list a `--cache-dir` that is not a usable directory among the exit-2 cases**, as the commands have refused it since #828; the help texts of `repo run` and `eval check` are pinned by a test.
- **The refusal of a batch whose cells ran with two executions is pinned to start with the batch name the cells record, or with the batch directory when they record none**, by a test.

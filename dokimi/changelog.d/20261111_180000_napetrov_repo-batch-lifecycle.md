### Fixed

- **`repo run` refuses a plan that names one cell twice** — a repeated `--task` or `--model`, or two
  `--model` strings with one directory name (`a/b` and `a:b`), put two runs in one cell directory;
  with `--jobs 2` each removed the other's files and `result.json` sat beside the other attempt's
  `meta.json` and answer. The run now stops before any agent starts, naming the cell.
- **`repo report` and `repo compare` refuse a batch whose arm and model hold two executions** — a
  `--force` run with another execution that stopped part way (Ctrl-C, a crash, a failed cell) left
  some cells on the old execution and was pooled with them in one row. The refusal names both ids;
  a `--force` run that finishes the batch clears it.

### Added

- **`formal/repo_batch`: a TLA+ model of the `repo run` cell lifecycle** — retries, crashes,
  resume, `--force`, Ctrl-C and what the report counts, with TLC configs for the code, the fixes
  and two mutations; `run.sh` checks each against its expected result.

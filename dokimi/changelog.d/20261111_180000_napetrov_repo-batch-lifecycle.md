### Fixed

- **`repo run` refuses a plan that names one cell twice** — a repeated `--task` or `--model`, or two
  names that share one cell directory (`a/b` and `a:b`), put two runs in one cell directory; with
  `--jobs 2` each removed the other's files and `result.json` sat beside the other attempt's
  `meta.json` and answer. The run now stops before any agent starts, naming the cell. Names that
  differ only in case are refused on every system, since they share one directory on a
  case-insensitive volume.
- **`repo report` and `repo compare` refuse an arm and model whose cells hold two executions** — a
  `--force` run with another execution that stopped part way (Ctrl-C, a crash, a failed cell), or a
  harness binary replaced while the batch ran, left cells of two executions that were pooled in one
  row. A batch that reported before can now be refused; the message names both ids, and a `--force`
  run over every arm and model of the batch with the same `--task` and `--reps` clears it. `repo compare` checks only the rows it compares.

### Added

- **`formal/repo_batch`: a TLA+ model of the `repo run` cell lifecycle** — retries, crashes,
  resume, `--force`, Ctrl-C and what the report counts, with TLC configs for the code before the
  fixes, the fixed code, four mutations, a run without fairness and four vacuity witnesses;
  `run.sh` checks each against its expected result.

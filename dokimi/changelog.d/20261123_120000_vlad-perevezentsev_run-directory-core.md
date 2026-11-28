### Added

- **Run directories, the core** — `dokimi.runs.rundir` gives a run its own
  directory under the runs root (`results/<run_id or name>/`, or the run's
  `--out-dir`) with a `run.json` record (new kind `run.v1`): command line,
  start and end time, status and the artifacts by relative path. A run is
  never written into another run's directory. On SIGTERM or SIGHUP, Ctrl-C, the spend
  cap or a run-wide CLI failure `run.json` says `incomplete`, and a run with
  a plan still gets `result.v1.json`, with `not_run` on the cells left.
  `runs list` shows the directory as one run. No command writes one yet
  (#94 R2, `docs/run-directories.md`).

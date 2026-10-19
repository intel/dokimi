### Changed

- **`docker-oracle` stops an oracle that overruns its budget** — `solve.sh` now
  gets the task's agent budget (`agent.timeout_sec`, else 600 s), like an agent.
  An oracle that runs out is a failed cell (reward 0, rc 124), as BenchFlow scores
  it. Before, `docker-oracle` waited for it with no limit.
- **Every `docker-*` step has a budget** — the build, the solve and the verifier
  keep their own. Every other `docker` step (`cp`, `inspect`, the `exec`s that copy
  files in, the sidecar's `create`, which may pull its image) gets 600 s instead of
  none. A step that must succeed and runs out errors the cell. Critical cleanup steps
  that prevent verification (pre-verifier reap, credential-file removal, sidecar removal,
  recompile) abort the cell on timeout to prevent security issues and stale binaries
  from being scored. A killed step's partial output is now in `run.log`.
- **`docker-claude-host` is contained** — the host `claude` starts through the
  process runner. A timeout kills its whole process tree, so a tool process the
  agent started no longer outlives its cell. `claude`'s output is read as UTF-8,
  whatever the locale. The in-container agent, the in-container probe and the
  docker CLI go through the same runner, each with the environment it had before.
  `solver.jsonl` and `run.log` still keep the whole stream.
- **Library API** — `DockerSolverHarness` and `ContainerSandbox.open` take a
  `runner` (`create_harness(..., runner=)` now passes it to the `docker-*`
  harnesses too). `solver_stream.run_solver` takes keyword-only `timeout`, `env`,
  `cwd` and `runner`, and returns a `process.Completed`.

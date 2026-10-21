### Changed

- **`docker-oracle` stops an oracle that overruns its budget** — `solve.sh` now
  gets the task's agent budget (`agent.timeout_sec`, else 600 s), like an agent.
  An oracle that runs out is a failed cell (reward 0, rc 124) that stays in the
  pass-rate denominator. BenchFlow's `oracle` errors the same overrun
  (`metrics.error`, no verdict), so the two oracles count it differently.
  Before, `docker-oracle` waited for it with no limit.
- **Every `docker-*` step has a budget** — the build, the solve and the verifier
  keep their own. Every other `docker` step (`cp`, `inspect`, the `exec`s that copy
  files in, the sidecar's `create`, which may pull its image) gets 600 s instead of
  none. A step that must succeed and runs out errors the cell. A cleanup
  or collection step whose timeout would let a verdict be scored on unsafe or missing
  state ends the cell with reward 0 and rc 124 instead, and the verifier does not
  run: the pre-verifier reap, the plugin and credential-file removals, the sidecar's
  removal (retried, best-effort, at close), the recompile, and the copy of
  `reward.txt`. The agent's tree, the solve's stream and its cost are still saved.
  A *completed* failing compile still only fills `recompile_failed`. These
  are scored reward 0 (they stay in pass-rate denominators), not recorded as errors.
  A killed `docker` step's partial output is now in `run.log`.
- **`docker-claude-host` is contained** — the host `claude` starts through the
  process runner. A timeout kills its whole process tree, so a tool process the
  agent started no longer outlives its cell. `claude`'s output is read as UTF-8,
  whatever the locale. The in-container agent, the in-container probe and the
  docker CLI go through the same runner, each with the environment it had before.
  `solver.jsonl` keeps the whole stream.
- **Library API** — `DockerSolverHarness` and `ContainerSandbox.open` take a
  `runner` (`create_harness(..., runner=)` now passes it to the `docker-*`
  harnesses too). `solver_stream.run_solver` takes keyword-only `timeout`, `env`,
  `cwd` and `runner`, and returns a `process.Completed`.
- **`metrics.egress` is tri-state** — it carries `status`: `violated` (the proxy
  denied a request), `ok`, or `unknown` when the sidecar's log timed out or failed
  at teardown. An `unknown` cell keeps its verdict, but it is not a clean egress
  record: `scripts/check_smoke_runs.py` fails on it.

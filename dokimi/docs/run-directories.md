---
depends_on:
  - dokimi/dokimi/runs/rundir.py
  - dokimi/dokimi/schemas/run.v1.json
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Run directories

A run directory keeps one run's record, plan, result and artifacts together, and
it still holds a result when the run is stopped part way: a signal, Ctrl-C or
the spend cap ([#94](https://github.com/intel/dokimi/issues/94) §2.3, stage R2).
The module that writes it is `dokimi/runs/rundir.py`. No command writes
a run directory yet: the command families move to it one by one in later PRs
(arms first), each with its `--out-dir` and a run name.

## Layout

```text
results/20261123T101500Z-1a2b3c4d/
  run.json         the run's record (run.v1)
  plan.v1.json     the cells the run promises to measure, when it has a plan
  result.v1.json   one status per planned cell, written on a stop too
  arms.v1.json     the run's own artifacts, each <schema_version>.json
```

- **Where.** Under the runs root (`results/`, or `DOKIMI_RUNS_DIR`), in a
  directory named by the run's name when it has one, else by its `run_id`
  (`dokimi.artifacts.run_id`). An output directory given to the run
  (`--out-dir`, [#591](https://github.com/intel/dokimi/pull/591)) is the run
  directory itself, in place of either.
- **No clobbering.** A run never writes into another run's directory. The
  directory must not exist yet; an output directory may also be an empty
  directory. A name already taken under the root is refused, and so is a
  `run_id` directory that exists (two commands sharing one `DOKIMI_RUN_ID`).
  `run.json` is created exclusively, so of two runs that race for one
  directory only one gets it. A name together with an output directory is
  refused: a name is unique only under the root. So is a directory that cannot
  be made: a runs root or a parent of the output directory that is a file.
  When `run.json` itself cannot be written (a full disk), nothing is left
  behind, so the same name or output directory can be tried again.
- **Names** have the characters of a run id: 1-128 letters, digits, `.`, `_`
  or `-`, starting with a letter or a digit, so a name is one path segment.
- **Artifacts** are `<schema_version>.json` in the directory, one per kind
  (`RunDir.artifact_path`). They carry the run's envelope like any artifact,
  so `runs list` groups them, with `run.json`, as one run.

## `run.json`

`run.v1` (`dokimi/schemas/run.v1.json`), written when the directory is
made and again when the run ends:

| Key | What |
| --- | --- |
| `run_id`, `dokimi_version` | the run envelope, as on every artifact |
| `name` | the run's name, `null` when it has none |
| `argv` | the command line after the program name |
| `started_at`, `finished_at` | ISO 8601 UTC; `finished_at` is `null` while the run runs |
| `status` | `running`, `complete`, `incomplete` or `failed` |
| `stop` | `{reason, detail}` when the run ended on a stop or an error, else `null` |
| `artifacts` | `[{kind, path}]`, each `path` relative to the directory with `/` separators |

`artifacts` lists the files the run asked a path for and wrote, as the run
ends; while the run runs it is empty.

## How a run ends

| The run | `status` | `stop.reason` | `result.v1.json` |
| --- | --- | --- | --- |
| finishes with every planned cell measured | `complete` | - | every cell's status |
| finishes with cells left unmeasured | `incomplete` | - | the cells left are `not_run` |
| gets SIGTERM or SIGHUP | `incomplete` | `terminated` | the cells left are `not_run` |
| gets Ctrl-C (`KeyboardInterrupt`) | `incomplete` | `interrupted` | the cells left are `not_run` |
| hits its `--max-usd` cap, or a call with no price | `incomplete` | `spend_cap`, `no_price` | the cells left are `not_run` |
| stops on a run-wide CLI failure (a usage limit, no login) | `incomplete` | `run_stopped` | the cells left are `not_run` |
| raises any other error | `failed` | `error` | the cells left are `not_run` |

A run without a plan writes no `result.v1.json`: a result is a status per
planned cell, tied to its plan by `plan_digest`. A cell left `not_run` by a stop
says why in its `detail`. `result.v1` has no status of its own;
`runner.plan.completeness` reads a result with `not_run` cells as incomplete, and
a run is refused `complete` while a planned cell has no measured status.

A signal is recorded through the CLI's own handler for SIGTERM and SIGHUP
(`dokimi.process.install_signal_handlers`; a library caller installs it
itself). The handler first tells the run that the signal came, so an end any
thread writes from then on is `terminated`; then it kills the child process
groups and stops the run's block (`dokimi.process_hooks`); the record is written, and the signal is
delivered again, so the process exits as it did before (status 143 for SIGTERM
under the CLI). Only a run opened on the main thread is stopped this way, and a
signal the process ignores stays ignored. A run a signal stopped is recorded
`terminated` even when code in its block caught the stop, and even when the
block then calls `finish` itself. An end another thread is already writing when
the signal comes is kept: it was decided from cells measured before the signal.

## In code

```python
from dokimi.runs.rundir import open_run
from dokimi.runs.scan import runs_root

with open_run(runs_root().path, name="nightly-7") as run:
    run.write_plan(plan)                     # plan.v1, saved as plan.v1.json
    for cell in plan["cells"]:
        status = measure(cell)               # pass, fail, inconclusive, ...
        run.record_cell(cell["cell_id"], status)
    save_artifact("arms", arms, run.artifact_path("arms"))
# the block ended: run.json says complete, result.v1.json has every cell
```

A block that ends normally finishes the run unless it called
`run.finish(status)` itself: `complete` when every planned cell has a measured
status, else `incomplete`. A block that raises records the stop and lets the
exception go on. A run ends once: a second `finish`, or a `write_plan` or
`record_cell` after the end, is refused.

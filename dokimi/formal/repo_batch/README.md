<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# TLA+ model of the `repo run` cell lifecycle

`RepoBatch.tla` models one batch directory of `dokimi repo run` (`dokimi/repos/runner.py`,
`dokimi/repos/batch_ledger.py`) and what `repo report` / `repo compare` count from it
(`dokimi/repos/report.py`). It asks one question: can retries, crashes, resumes, `--force`
reruns, worker threads, Ctrl-C and late results change which cells the report counts, or how
often? TLC answers it for a small, explicit instance. It says nothing about whether a grader,
an LLM judge or a task measures the right thing.

## Run

```sh
# pinned tools: tla2tools.jar v1.8.0 (sha256 e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f),
# any Java 11+ (checked with Temurin JRE 21.0.5+11)
TLA2TOOLS_JAR=/path/to/tla2tools.jar JAVA=/path/to/java ./run.sh            # every cfg/*.cfg
TLA2TOOLS_JAR=... ./run.sh cfg/mut_accept_stale.cfg                         # one config
```

Each config carries an `\* EXPECT:` line (`pass` or `violation <Name>`); `run.sh` writes
`out/summary.tsv` and one log per config (with TLC's counterexample trace), and exits 1 when a
result differs from its expectation. `out/` is not committed.

## What exists in the code, and what does not

| Mechanism | In the code | In the model |
|---|---|---|
| Run = batch dir `--batch`; cell = `cell_name(task, arm, model, rep)` dir | yes | `Tasks` (one arm, model, rep) |
| Repetition | `--reps N`: cells `__r1..__rN`, independent samples | not needed: one rep per cell |
| Retry of a cell | only a later `repo run` on the batch (resume or `--force`), same cell name, replaces the dir | `Take` by a later invocation |
| In-process retry, redelivery, reassignment, leases, heartbeats | **none** | not modelled (would be an imagined architecture) |
| Workers | `ThreadPoolExecutor(jobs)` + `pool.map` | `Workers` threads |
| Timeout | agent killed, the cell is graded (an outcome, not a retry) | inside `Work` |
| Crash | process dies; no `result.json` → cell is *missing*, the next run fills it | `Crash` |
| Late result of an old attempt | impossible in-process: threads die with the process | `LateCommit`, mutation only |
| Cancel | Ctrl-C: `KeyboardInterrupt`; `pool.map` cancels queued futures, running cells finish | `Cancel`, `CancelGuard` |
| Exclusion of concurrent runs on one batch | `batch_lock` (flock on `_eval/lock`) | `Locked` |
| Execution pinning | `execution.id` in `meta.json`/`result.json`; `check_batch_executions` before any cell | `Configs`, `StartCheck` |
| Commit | `_write_json(rd / "result.json")`: mkstemp + `os.replace` (atomic) | `Commit` |
| Aggregation | `load_cells`: one `result.json` per dir; missing = planned − done | `Done`, `Counted`, `Missing` |
| Dataset/task, skill, evaluator pins | eval tree revision or digests, `_eval/skills.json`, grader sources in the batch ledger | folded into `Configs` |
| Caps (timeout, max turns, budget) pinned | **no**: recorded per cell in `agent.caps`, not in `execution.id` | not modelled (open question 3) |

## Actions → code

| Action | Code |
|---|---|
| `Start(g, f, p)` | `cmd_repo_run` → `batch_lock`, `run_batch` → `_refuse_shared_cells`, `check_batch_executions` (`--force` skips its own plan) |
| `Take(w, i)` | `pool.map` → `one` → `run_cell`: `reused_result` keeps a done cell unless `--force`; else `_rmtree` + mkdir of the cell dir |
| `Work(w)` | agent run, then `meta.json`, `events.jsonl`, `answer.txt`, `task.json`, grade, written by path |
| `Commit(w)` | `_write_json(rd / "result.json", record)` |
| `Crash(i)` | SIGKILL / OOM / reboot: flock released, threads gone |
| `Cancel(i)`, `Finish(i)` | Ctrl-C; pool join and the exit code of `cmd_repo_run` (1 on errors, interrupt, budget stop, probe failure) |
| `LateCommit(a)` | mutation `AcceptStale` only |

## Properties

| | Property (TLA+) | Meaning | Where the code holds it |
|---|---|---|---|
| P1 | `RetryNotRepetition` | a cell is one sample at most, however often it ran | `load_cells` reads one `result.json` per dir; the retry reuses the cell name |
| P2 | `OnlyValidAttempts` | a counted result is a finished attempt of that cell, beside that attempt's files only | `run_cell` rmtree's before an attempt; `result.json` written last; `_refuse_shared_cells` |
| P3 | `NoStaleOverwrite` | no `result.json` from an attempt that no longer owns the dir | `batch_lock`; threads die with the process; `_refuse_shared_cells` |
| P4 | `OneConfig` | the cells of one report row ran under one `execution.id` | `check_batch_executions`; `_refuse_mixed_executions` in `load_cells` |
| P5 | `PartialNotComplete` | exit 0 only with every planned cell done; the rest are *missing* | exit logic of `cmd_repo_run`; `record_plan` + `load_cells` |
| P6 | `CancelContract` | no attempt starts after Ctrl-C; the run does not exit 0 | `pool.map` cancels pending futures when the main thread raises |
| P7 | `InvocationEnds` (liveness) | every invocation ends | under `Fairness` only |

Assumptions (stated in the model, not proven):

- **Fairness**: weak fairness on `Work`, `Commit`, `Take`, `Finish` — a live thread keeps
  working, an agent run ends (its timeout), the pool joins. Nothing forces a resume after a crash,
  so "every planned cell eventually has a result" is *not* claimed: it needs a human or a
  scheduler to rerun the batch.
- `os.replace` is atomic and a dead process writes nothing; a container that outlives the
  process writes into the deleted dir's inode (`_rmtree` removed it), not into the new one.
- The batch dir is on a local file system where `flock` works.
- Not modelled: the batch budget (`--max-batch-usd`), unsupported tasks, multiple arms/models,
  report reads racing a writer (they read whole files written atomically).

Vacuity: `witness_*.cfg` each assert that a scenario never happens (no retry, no crash after the
artifacts, no Ctrl-C, no resume after a crash) and must be violated; they show `fixed.cfg` passes
with those scenarios reachable.

## Results

Instance: `Tasks = {t1, t2}`, `Workers = {w1, w2}`, `Configs = {cA, cB}`, `MaxInv = 3`;
tla2tools v1.8.0 (TLC2 Version 2026.11.06.222357, rev d8fb231), 4 TLC workers, Temurin 21.0.5+11,
224-core shared host; seconds are wall time.

| cfg | expect | got | ok | generated | distinct | depth | seconds |
|---|---|---|---|---|---|---|---|
| code_main_dup_plan | violation NoStaleOverwrite | violation NoStaleOverwrite | yes | 3746 | 3474 | 7 | 0.8 |
| code_main_dup_plan_files | violation OnlyValidAttempts | violation OnlyValidAttempts | yes | 3673 | 3407 | 7 | 0.8 |
| code_main_force_crash | violation OneConfig | violation OneConfig | yes | 344636 | 266056 | 11 | 2.4 |
| fixed | pass | pass | yes | 10304813 | 5750785 | 28 | 431.9 |
| mut_accept_stale | violation NoStaleOverwrite | violation NoStaleOverwrite | yes | 4686 | 4172 | 8 | 0.9 |
| mut_count_every_commit | violation RetryNotRepetition | violation RetryNotRepetition | yes | 49055 | 42396 | 10 | 1.2 |
| mut_no_cancel_guard | violation CancelContract | violation CancelContract | yes | 124 | 119 | 5 | 0.7 |
| mut_no_lock | violation NoStaleOverwrite | violation NoStaleOverwrite | yes | 53078 | 27928 | 7 | 1.4 |
| no_fairness | violation InvocationEnds | violation InvocationEnds | yes | 217120 | 173024 | 11 | 4.0 |
| witness_NoCancel | violation NoCancel | violation NoCancel | yes | 25 | 25 | 3 | 0.7 |
| witness_NoCrashAfterArtifacts | violation NoCrashAfterArtifacts | violation NoCrashAfterArtifacts | yes | 260 | 234 | 5 | 0.7 |
| witness_NoResumeAfterCrash | violation NoResumeAfterCrash | violation NoResumeAfterCrash | yes | 10889 | 9747 | 8 | 1.0 |
| witness_NoRetryHappens | violation NoRetryHappens | violation NoRetryHappens | yes | 55492 | 47963 | 10 | 1.2 |

- `code_main_*` model the code as it was before this change; their traces were replayed as tests in
  `tests/test_repo_batch_lifecycle.py` (red before, green after).
- `mut_*` are mutations: TLC must find each.

## Findings

1. **One cell planned twice** (`code_main_dup_plan*`): `--task a --task a`, `--model m --model m`,
   or `--model org/m --model org:m` (one `slug`) put two runs in one dir. With `--jobs 2` the second
   rmtree'd the first's dir while its agent ran, and `result.json` landed beside the other
   attempt's `meta.json`/answer. Fixed: `_refuse_shared_cells`.
2. **A part-way `--force` with another execution pools two executions** (`code_main_force_crash`,
   found by TLC on its first run of `fixed`): `--force` replaced r1, then the run stopped; r2 kept
   the old execution and `repo report` pooled both in one row. Fixed: `_refuse_mixed_executions`.
3. **Ctrl-C** (`mut_no_cancel_guard`): a probe with `_thread.interrupt_main()` started queued cells,
   but a real SIGINT wakes the main thread and `pool.map` cancels them. Not a bug; the test pins it.

Open questions (left as they are):

1. `--force` with `--max-batch-usd`: the budget stop skips only cells that are not done, so a forced
   rerun still reruns done cells after the budget is spent.
2. A grader with write access to the run dir could leave a `result.json` when the host fails to
   write its own (`CellCrash`); a resume would reuse it.
3. Caps (timeout, max turns, per-cell budget) are not in `execution.id`, so one row can mix caps.

## A formal-verification evaluator for agent tasks (proposal)

See [`EVALUATOR.md`](EVALUATOR.md).

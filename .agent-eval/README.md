<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# dokimi's own repository evals

The coding jobs agents are given on this repository, run and graded by dokimi's
repository mode ([`repo-evaluation-contract.md`](../dokimi/docs/repo-evaluation-contract.md)).
They measure how Dokimi's `AGENTS.md` and `REVIEW.md` affect an agent's work.
Grading is deterministic and offline. No grader calls an LLM.

## Tasks

| Task | Family | What the agent is asked | What the grade turns on |
|---|---|---|---|
| `fix-degrade-pre` | fix | `<pre>` blocks are mangled by the judge-validity degradations | hidden tests; regression |
| `fix-docker-reward` | fix | docker harnesses ignore `reward.json`; `1e-1` reads as 1.0 | hidden tests; regression |
| `feat-codex-profile` | feature | a built-in `codex` harness profile | the profile loads, `repo reach --harness codex --dry-run` probes AGENTS.md |
| `feat-static-metric` | feature | a `code_block_lang` static metric | wired everywhere `contributing-metric.md` says |
| `schema-required-field` | feature | a required `digest` on every `explore_runs` task entry | a new schema version, not an in-place edit (`schema_compat`) |
| `legacy-keys` | feature | show the answer conditions as treatment/control | stored `with_docs`/`without_docs` keys kept |
| `rename-cli-flag` | docs | rename `benchmark run --multi-run` to `--repeats` | docs renamed with the code (`test_docs_code_refs`) |
| `docs-new-page` | docs | a short `repo scan` getting-started page | in the `mkdocs.yml` nav and the docs index |
| `review-pr-conventions` | review | review a PR with five planted defects | structured findings, recall and reports on a decoy |
| `review-clean-diff` | review | review a documentation change using an existing CLI flag | an empty or nonblocking review passes |
| `use-repo-scan` | use | check a user's AGENTS.md with dokimi | a working scan manifest; both broken claims named |

Every code task also gets these metrics:
- `regress_pass`: the whole suite, except timing- and process-sensitive tests: all of
  `test_example_runner_processes.py` (4 tests, 7 cases) and 2 single tests (`REGRESSION` in `run.py`),
  and the real ragas import check, which needs the `[ragas]` extra the image does not install
- `ruff_ok`, `mypy_ok`, `config_ok`, `schema_ok`
- `compat_ok`: `schema_compat` against the start commit
- `diffcov_*`: CI's changed-line coverage gate

Process metrics are reported and never gated. They come from `events.jsonl` and git:
- `ran_pytest`, `ran_ci_local`
- `committed`, `signed_off`
- `artifacts_added`, `mypy_overrides_grew`
- `read_docs`

The review tasks report no process metrics (`read_docs`, `ran_pytest` and the rest above):
the built-in seeded-review grader writes only the seed metrics. It reads the final fenced
JSON array and measures recall, unmatched findings and decoy flags. Without a
readable findings array, both tasks report `pass: false` and `parse_error: true`,
and the seeded task scores zero recall. The clean task also fails when the
reviewer reports a blocker or major issue. Read credited findings before treating
recall as a correctness measure, because the grader also matches by proximity.

## Running

`eval check` and `repo run` need Docker. Run them from `dokimi/`:

```bash
# Validate the current checkout and run the null and oracle without an LLM.
../.venv/bin/python cli.py eval check .. --manifest .agent-eval/repo-eval.yaml -j 4

# Set MODEL to the model ID configured for your harness.
revision=$(git rev-parse HEAD)
../.venv/bin/python cli.py repo run "..@$revision" --manifest .agent-eval/repo-eval.yaml \
    --arm "guidance:none,guidance:rev:$revision" --model "$MODEL" --reps 3 --batch self \
    --agent-network bridge --max-usd 50 --out-dir results/repo-runs
../.venv/bin/python cli.py repo compare results/repo-runs/self --baseline guidance:none
```

The tasks pin historical code. `guidance:raw` retains the task base's
`CLAUDE.md`, which predates the root `AGENTS.md`. `guidance:none` removes
the historical file. `guidance:rev:` replaces the matched files with the
ones at the selected revision, keeping the task base fixed. To compare two
versions of the guide, pass both revisions as separate `guidance:rev:` arms
and choose one as the baseline.
A guidance-only `patch:` arm can isolate changes to an individual file.

The task image supplies Python 3.11 with the locked dependencies and starts
without a `.venv`. The pinned base's `make dev` defaults to Python 3.13, which
the offline image cannot download. Run the base's available tests with
`python -m pytest` from `dokimi/`. Some current guidance helpers are missing on
that base, including `dokimi/scripts/list_new_error_paths.py` and
`dokimi/scripts/check_duplicates.py`. The comparison measures current guidance
on this historical environment, where some of its checks are unavailable.

## Adding a task

A task is a directory under `tasks/` with these files:

| File | Required | What it holds |
|---|---|---|
| `task.json` | yes | contract §3; a code task needs a `gate` naming its metrics (`run.py` fails one without) |
| `setup.patch` | no | puts the tree in its pre-task state |
| `oracle.patch` | code tasks | the reference solution |
| `oracle_answer.md` | answer-based tasks | the repository grader's reference answer |
| `review.patch` | review tasks | the commit under review |
| `manifest.json` and `oracle.json` | seeded-review tasks | seed and decoy locations, plus the reference findings |
| `hidden/` | no | test files the agent never sees |
| `check.py` | no | task-specific metrics, read by [`run.py`](run.py) |

`run.py`'s docstring has the grader contract.

Write the prompt the way a maintainer would ask, and leave out the repository's
conventions: those are what the guidance is measured on.

A task is ready when `eval check` reports it `OK`.

# dokimi's own repository evals

The coding jobs agents are given on this repository, run and graded by dokimi's
repository mode ([`repo-evaluation-contract.md`](../dokimi/docs/repo-evaluation-contract.md)).
They measure whether the guidance in `AGENTS.md` / `CLAUDE.md` changes what an
agent does here, and catch the day it stops doing so. Grading is deterministic and
offline. No grader calls an LLM.

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
| `review-pr-conventions` | review | review a PR with five planted defects | recall of the defects (score-only) |
| `use-repo-scan` | use | check a user's AGENTS.md with dokimi | a working scan manifest; both broken claims named |

Every code task also gets these metrics:
- `regress_pass`: the whole suite, except timing- and process-sensitive tests: all of
  `test_example_runner_processes.py` (4 tests, 7 cases) and 2 single tests (`REGRESSION` in `run.py`)
- `ruff_ok`, `mypy_ok`, `config_ok`, `schema_ok`
- `compat_ok`: `schema_compat` against the start commit
- `diffcov_*`: CI's changed-line coverage gate

Process metrics are reported and never gated. They come from `events.jsonl` and git:
- `ran_pytest`, `ran_ci_local`
- `committed`, `signed_off`
- `artifacts_added`, `mypy_overrides_grew`
- `read_docs`

## Running

`repo tasks check` and `repo run` need Docker. Run them from `dokimi/`:

```bash
# self-check, no LLM: the untouched workspace fails each gate, the reference solution passes it
python cli.py repo tasks check .. --manifest .agent-eval/repo-eval.yaml -j 4

# agents: one cell per (task, arm, model, rep)
python cli.py repo run .. --manifest .agent-eval/repo-eval.yaml \
    --arm guidance:none,guidance:raw --model <model id> --reps 3 --batch <name> \
    --agent-network host --max-batch-usd 50
python cli.py repo report repo-runs/<name>
```

The task bases pin a commit of `main`. To measure a change to the guidance, run
the same tasks on a base that carries it:
1. copy each task with its `base` set to the commit with the new guidance;
2. run `guidance:raw` on both sets;
3. compare the cells by task.

## Adding a task

A task is a directory under `tasks/` with these files:

| File | Required | What it holds |
|---|---|---|
| `task.json` | yes | contract §3; a code task needs a `gate` naming its metrics (`run.py` fails one without) |
| `setup.patch` | no | puts the tree in its pre-task state |
| `oracle.patch` | yes | the reference solution |
| `oracle_answer.md` | review tasks only | the reference review |
| `hidden/` | no | test files the agent never sees |
| `check.py` | no | task-specific metrics, read by [`run.py`](run.py) |

`run.py`'s docstring has the grader contract.

Write the prompt the way a maintainer would ask, and leave out the repository's
conventions: those are what the guidance is measured on.

A task is ready when `repo tasks check` reports it `OK`.

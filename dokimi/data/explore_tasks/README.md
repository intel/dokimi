<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Exploration tasks (ExploreBench)

Each YAML file here is one **exploration-only** task for the ExploreBench track.
A task poses a localization question and declares the reference locations a good
explorer should cite. It scores *whether the agent can find the right evidence*
— separately from whether it can answer or solve.

## Schema

```yaml
id: short-stable-id
product: my-library              # product / repo the task is about
query: "Where is X implemented?"  # the localization question
repo_root: .                     # optional; see below
references:
  - path: docs/architecture.md
    lines: [120, 180]            # optional inclusive [start, end] (or [n])
  - path: src/pipeline.cpp       # no `lines` → a whole-file reference
```

- `lines` is inclusive `[start, end]` (or `[n]` for a single line). Omit it for a
  whole-file reference, which scores at **file** granularity only.
- `repo_root` is the tree the explorer searches and the base for reference
  paths. Precedence: the value declared here, else `explore run --repo-root
  <dir>`, else this checkout. Declare it, or pass `--repo-root`, whenever the
  task is about a tree other than `dokimi/` — a task that omits both
  scores against this repository.

## Running

```bash
python cli.py explore list
python cli.py explore run --all --arms oracle,empty --baseline-arm empty \
  --out-json results/explore-runs/explore_runs.json \
  --out-md   reports/exploration.md
```

`oracle` cites exactly the references (a ceiling); `empty` returns nothing (a
floor); `command:<template>` runs a real CLI explorer — for example:

```bash
python cli.py explore run --all \
  --arms 'empty,command:claude -p {query_quoted}' --baseline-arm empty
```

## About this seed set

Every task committed here **dogfoods the Dokimi repo itself**: its
references point at real files in this tree, so the set is runnable and
verifiable offline with no external checkout, and `tests/test_explore_track.py`
asserts that each reference still exists. They use file-level references because
line numbers in an evolving repo drift; line-level scoring is covered by unit
tests and by oracle arms.

Tasks about *another* codebase use the **same schema** but are evaluation
content, so they live in the content repository and are run from there:

```bash
python cli.py explore run --all --tasks-root <checkout>/explore_tasks \
  --repo-root <product-checkout> --arms oracle,empty --baseline-arm empty
```

Derive their `references` from the oracle solution's edited files/ranges, or
curate doc/example locations. Pair citation F1 with task
pass-rate and judge score; patch-derived locations under-credit supporting
evidence (tests, callers, neighbors).

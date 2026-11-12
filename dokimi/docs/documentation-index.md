<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Documentation index

Start with the top-level [README](../README.md) for the project overview and
CLI surface. The files in this directory go deeper. They are also published at
<https://intel.github.io/dokimi/>, grouped as below, together with a
[CLI reference](https://intel.github.io/dokimi/reference/cli/) and an
[API reference](https://intel.github.io/dokimi/reference/api/dokimi/) generated from the
code.

## Start here

- [Installation and route selection](start/README.md).
- [Skill authors and reviewers](start/skills.md).
- [Repository maintainers](start/repositories.md).
- [Evaluators and researchers](start/experiments.md).
- [Agents and CI](start/automation.md).
- [Read results](start/results.md).
- [Contributors](start/contributors.md).

## Feature catalog

The [feature tree](features/README.md) links to all 39 cards, grouped by the question they answer. Availability and limitations live in the owning cards.

## User journeys

- [user-journeys/README.md](user-journeys/README.md) — what users come to
  Dokimi to get done: fifteen journeys (J1–J15) from "check this" to "invoke
  one capability" and "turn a solved chat into a skill", who they are for, and where each one enters.
- [user-journeys/journeys.md](user-journeys/journeys.md) — the catalog: each
  journey's use cases and acceptance criteria, today's commands, and what is
  still proposed.
- [user-journeys/flow-map.md](user-journeys/flow-map.md) — how the journeys
  connect, current and proposed transitions told apart.
- [user-journeys/traceability.md](user-journeys/traceability.md) — how a
  criterion links to commands and tests, evidence kinds and stubs, and the
  coverage report `pytest --outcomes` generates.

## Using Dokimi

- [external-content.md](external-content.md) — **start here if you have no
  evaluation content yet**: the content this framework measures lives in a
  separate repository, and every location is an explicit flag
  (`--questions-root`, `--tasks-root`, `--repo-root`, `--registry`). Includes a
  pre-flight `catalog check` and what each command does when a root is missing.
- [quickstart.md](quickstart.md) — end-to-end LLM evaluation pipeline:
  personas → questions → answers → judge → report. Includes troubleshooting
  and a cost estimate.
- [runs.md](runs.md) — what the runs have written: `cli.py runs list` lists
  every artifact under `results/` (or `DOKIMI_RUNS_DIR`), one run's together,
  and counts what it skipped, `cli.py runs show` checks one against its schema
  or lists one run. No LLM call.
- [user-flow.md](user-flow.md) — the canonical end-to-end path: pick a target,
  preflight, run, then pin the question set so runs stay comparable.
- [benchmarking-and-comparison.md](benchmarking-and-comparison.md) — how to
  run static benchmarks, LLM context benchmarks, fair multi-model comparisons,
  treatment-arm comparisons, dashboards, baselines, and comparison
  scripts.
- [adding-doc-source.md](adding-doc-source.md) — point the pipeline at local
  files, a single URL, a real MCP server, or a custom client.
- [evaluating-treatments.md](evaluating-treatments.md) — compare
  context-augmentation arms (docs, MCP, skills, agent persona prompts) with
  `cli.py arms run`.
- [tool-preference.md](tool-preference.md) — two equal tools per request: how
  much a name, wording or list position moves a model's choice
  (`cli.py tools preference`).
- [coding-harnesses.md](coding-harnesses.md) — run executable coding tasks
  through Codex, Claude Code, or terminal-bench/Harbor harnesses and compare
  verifier pass rate against a baseline.
- [answer-formats.md](answer-formats.md) — run Inspect AI, OpenAI evals,
  skill-creator and promptfoo answer-only sets on the answers track
- [subscription-backends.md](subscription-backends.md) — run the Q&A track through a
  logged-in coding-agent CLI (`--provider claude-cli`) instead of an API key
- [context-window.md](context-window.md) — read a run's context window from
  its Claude Code trace with `cli.py trace inspect`: start-up load, growth,
  compactions, tool-result weight. No LLM call.
- [session-prefilter.md](session-prefilter.md) — which AGENTS.md or CLAUDE.md rules
  the Claude Code session logs triggered, acted on and were corrected on, with
  `cli.py trace prefilter`: candidates for ablation, not verdicts. No LLM call.
- [run-consistency.md](run-consistency.md) — repeated runs of one task with
  `cli.py trace consistency`: result stability (pass^k), path stability
  (tool mix, order, actions, strategies), process-contract conformance.

- [what-it-measures.md](what-it-measures.md) — the three things Dokimi measures:
  enabled vs disabled arms, novel vs known content, and repositories.
- [reviewing.md](reviewing.md) — review a skill or a repository's agent setup,
  cheapest step first, with costs and how to read the results.

## Content novelty

- [skill-novelty.md](skill-novelty.md) — what a skill *adds* to a given model
  rather than what it scores: the added/redundant/degraded/still-missing 2×2 per
  knowledge atom, span/topic/term attribution with colour legend, and
  five-axis shortening. Authoring tool, usable standalone.
- [skill-novelty-walkthrough.md](skill-novelty-walkthrough.md) — the authoring path in
  order, one artifact and one model: can it be selected, what will measuring cost, triage,
  extract once and probe per model, read the span view, shorten, re-measure. Says at each
  step what the number refuses to claim.
- [skill-novelty-evidence.md](skill-novelty-evidence.md) — the 33-skill study
  the novelty numbers come from: method, K0 per model, the 2×2, need-graph
  statistics, content categories, shortening with measured behaviour, and the
  caveats each result carries.

## Repository evaluation

- [repo-evaluation-contract.md](repo-evaluation-contract.md) — implemented core with scoped extensions: what a repository provides to be evaluated as a whole:
  `repo-eval.yaml`, `task.json`, the `grade` entry point and `grade.json`,
  harness profiles, and the guidance reach probe; and how to run its task
  self-check, agent runs, report and arm comparison (`eval check`,
  `repo run`, `repo report`, `repo compare`). Tasks and graders stay in the scanned repository.
- [reach-matrix.md](reach-matrix.md) — the reach conformance suite: fixture
  repositories, what each harness's vendor documentation says they load, the
  `scripts/reach_conformance.py` run and merge that produce the agent × version
  matrix, and the nightly workflow that publishes it.
- [repo-evaluation-mergeability.md](repo-evaluation-mergeability.md) —
  *(proposed)* grading whether a maintainer would merge a test-passing change:
  `rubric.json` (blockers, non-blockers, scope), deterministic scope and
  test-integrity checks, the rubric judge, and the calibration gate a task
  must pass before it is admitted.
- [repo-evaluation-agent-modes.md](repo-evaluation-agent-modes.md) —
  *(proposed)* how a repository task states its agent mode, effort, perf
  protocol and team rules, split from the mergeability proposal (#67).
- [preparing-a-repository.md](preparing-a-repository.md) — what to put in a
  repository and its environment so coding agents build, test and review it
  well (runnable pinned commands, tool lines in `AGENTS.md`, true guidance, a
  complete compile database, review rules), and how to measure each change.
- [preparing-a-repository-evidence.md](preparing-a-repository-evidence.md) —
  the runs behind each figure on that page: task set, arms, model, cell count
  and raw count.
- [rule-ledger.md](rule-ledger.md) — one row per guidance rule: its
  hypothesis, the replay or executable cases of the failures it prevents, and
  the review date; `repo compare --ledger` gives each rule a keep, review or
  insufficient-evidence verdict. The cut decision stays with a person.

## Extending Dokimi

- [architecture.md](architecture.md) — module map and data flow across the
  static, LLM, and executable-task tracks.
- [contributing-metric.md](contributing-metric.md) — add a new static
  documentation metric (module + spec + tests + docs).
- [contributing-terminal-bench-task.md](contributing-terminal-bench-task.md)
  — the executable task format (directory layout, `task.toml` fields, verifier
  and oracle contract) that tasks in the external content repository follow.
- [releasing.md](releasing.md) — version numbers, changelog fragments, cutting
  a release, and the PyPI and conda-forge path.

## Methodology

- [methodology.md](methodology.md) — operational methodology: model roles,
  question types, difficulty levels, answer generation, judging, trust checks,
  and executable agent tasks.
- [difficulty-rubric.md](difficulty-rubric.md) — the difficulty/level rubric
  used to grade performance-skill questions and tasks.
- [example-execution.md](example-execution.md) — how the `example_pass_rate`
  metric extracts and runs fenced code examples in isolation.
- [ingestion-docling.md](ingestion-docling.md) — ingesting non-Markdown
  documents (PDF, HTML, …) via Docling, installed with the optional `ocr` extra.
- [research-register.md](research-register.md) — the register of Dokimi's own
  ideas, experiments (negative ones included), findings and decisions.

## Development and known limitations

- [Feature directions](development/README.md).
- [CLI and artifact migrations](development/interfaces.md).
- [Known limitations](development/limitations.md).
- [Complete open-work index](development/work-index.md).

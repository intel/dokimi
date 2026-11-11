<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Compare context or agent setups

For evaluators, researchers, documentation and MCP authors. Choose the track that matches the outcome you want to measure.

| Outcome | Inputs you need | Execution guide |
|---|---|---|
| Better answers from a skill, docs, MCP source or profile | Fixed question set, context variants, answer model and judge | [Treatment comparisons](../evaluating-treatments.md) |
| Better executable work across models or harnesses | Tasks, environment, reference solution and verifier | [Coding harnesses](../coding-harnesses.md) |
| Better work in one repository | Repository manifest, tasks and guidance variants | [Repository evaluation](../repo-evaluation-contract.md) |
| What the content teaches a model | Artifact and probe/judge setup | [Novelty walkthrough](../skill-novelty-walkthrough.md) |

## Before running

1. [Install Dokimi](README.md) and validate your [external evaluation content](../external-content.md). Dokimi does not ship your workload's question set.
2. State the hypothesis and change one experimental factor where possible. An *arm* is one tested configuration; the baseline is its control.
3. Freeze tasks and input revisions. Select models, harness versions and graders explicitly; check their supported capabilities.
4. Validate the grader with negative and reference controls. Set the budget using the chosen command's supported controls; there is no universal spending cap across all tracks.

## Run and inspect

Use the linked execution guide for the exact input schema and command. The [Q&A pipeline tutorial](../quickstart.md) covers the longer target-to-report flow; it is not a prerequisite for running a single treatment comparison.

**Output:** recorded outcomes and reports for the selected track. Inspect missing cells, errors and per-task differences before aggregates. Compare quality with cost and latency; preserve the task set, model, harness and grader configuration with the conclusion.

## Decide what the experiment supports

Read [results and next steps](results.md) and [comparison statistics](../benchmarking-and-comparison.md#reading-the-significance-tables). An inconclusive result needs more evidence or a better experiment, not an automatic keep/cut decision. A knowledge gain is not a demonstrated task gain.

**Done when:** another evaluator can identify the tested inputs and conditions, understand the uncertainty and limitations, and reproduce the procedure without relying on the illustration.

[Experiment features](../features/experiments.md) · [Grader checks](../features/judging.md) · [Methodology](../methodology.md)

<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Read results and choose the next step

Start with what was actually measured. Keep the subject revision, tasks, model, harness and grader attached to every conclusion.

| Evidence | What it establishes | Next action |
|---|---|---|
| Static findings | Structural issues in the checked content | Inspect each finding, fix it and rerun |
| Loading or routing evidence | What the tested agent loaded or chose | Correct discovery before testing content value |
| Novelty probes | Added, redundant, degraded or missing knowledge under the probe setup | Form an edit hypothesis and test task outcomes |
| Paired task/answer outcomes | The observed difference on the evaluated workload | Inspect uncertainty, failures, costs and representativeness |
| Trace evidence | Recorded actions and context use | Investigate mechanisms; do not infer causality from a trace alone |
| Baseline comparison | A change relative to the configured reference and thresholds | Review evidence before adopting or rejecting the change |

## Separate failure from missing evidence

- **Input or environment error:** repair the setup and rerun the affected work. Do not score it as model inability.
- **Agent task failure:** inspect the verifier and trace; establish whether the grader itself is valid.
- **Partial or budget-limited run:** identify the missing cases; do not silently compare a different task population.
- **Inconclusive comparison:** retain the uncertainty and choose the next experiment. It does not prove no effect.
- **Improved score:** check the held-fixed conditions and workload coverage before generalizing.

## Find the report

Start with the output location printed by your command. [Runs list/show](../runs.md) explains supported artifact discovery and validation; not every JSON file is a registered run artifact. [Comparison statistics](../benchmarking-and-comparison.md#reading-the-significance-tables) explains the reports that expose uncertainty and multiplicity corrections.

## Report the conclusion

State: what was tested, against what baseline, under which conditions, which outcomes changed, what remains unknown, and what you will do next. Use [recorded studies](../skill-novelty-evidence.md) for empirical examples. Feature illustrations are explanatory and supply no additional measured evidence.

[Skill workflow](skills.md) · [Repository workflow](repositories.md) · [Experiment workflow](experiments.md) · [Automation](automation.md)

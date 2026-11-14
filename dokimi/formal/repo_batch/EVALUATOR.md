<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Proposal: a model-checking grader for agent tasks

Status: proposal, not implemented. It would be a `dokimi:` grader (like the built-in ones in
`dokimi/repos/`) for tasks whose answer is a change to a system with a fixed formal spec.

## Shape

- **Input**: the task's fixed spec (a `.tla` module with the properties, plus one or more `.cfg`
  files and their `EXPECT` lines), pinned by the task; and the agent's change — either the model
  of the implementation the agent edited, or the code that a task-owned extractor maps to the
  model's constants.
- **Output**, one of four, in `grade.json`:
  - `pass` — TLC finished every config and each matched its `EXPECT`;
  - `counterexample` — a property was violated: the property name and the trace;
  - `timeout` — the state space was not exhausted within the task's limit;
  - `tool_error` — TLC or the parser failed (syntax error, unknown operator, Java missing).

## Rules

1. **The agent cannot weaken the spec.** The grader reads the spec, configs and `EXPECT` lines from
   the task's eval tree (pinned in the batch ledger like other grader sources), never from the
   agent's workspace. A change to those paths in the workspace is reported and ignored.
2. **The agent cannot swap the checker.** `tla2tools.jar` and the JRE are pinned by SHA-256 in the
   grader and mounted read-only; TLC runs outside the agent's container. The command line
   (`-workers`, `-deadlock`, `-config`) is the grader's, not the task's or the agent's.
3. **A timeout is not a proof.** `timeout` and `tool_error` score as not passed and are reported
   apart from `counterexample`, so a slow model is not read as a correct one, and a broken tool is
   not read as an agent failure.
4. **Vacuity.** Each task ships witness configs that must be violated; if a witness passes after the
   agent's change (the change made the scenario unreachable), the grade is `counterexample` with
   the witness named.
5. **A pass is not a quality score.** It means: in this bounded instance, under the stated fairness
   assumptions, the listed properties hold. The report shows it as its own measure next to tests and
   any judge, never folded into one number, and names the instance bounds.

## Limits

- Bounded: only the constants in the configs are checked.
- The model-to-code link is as good as the extractor or the human mapping (the README's action →
  code table); a correct model of wrong code passes.
- Liveness holds only under the fairness the spec assumes.

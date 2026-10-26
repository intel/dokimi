<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# User journeys

Dokimi helps people and agents make coding agents do their actual work better:
assess, diagnose, improve and re-measure, then keep the result from regressing.
This section is the product contract for that: what a user is trying to get
done (a journey), the concrete situations inside it (use cases), what has to be
true for the user to get the outcome (acceptance criteria), which part of today's
CLI serves it, and which tests show it. It describes intent and current
coverage. It is not a roadmap: plans, progress and design decisions are tracked
in issues and pull requests.

- [journeys.md](journeys.md): the catalog. All fifteen journeys, their use cases
  and acceptance criteria, each with a stable id.
- [flow-map.md](flow-map.md): how the journeys connect, with today's and
  proposed transitions told apart.
- [traceability.md](traceability.md): how criteria link to commands and tests,
  what the evidence levels mean, and how coverage is generated and checked.

## Who it is for

| Role | Who | What they need from Dokimi |
|---|---|---|
| Primary users | Skill authors; repository maintainers | Evidence that a skill, guidance file or set of evals helps agents on real work, and where it does not |
| Primary operator | A coding agent driving the Dokimi CLI and Dokimi skills | Commands with explicit inputs and costs, structured output, and errors it can act on |
| Also first-class | People at a terminal; CI | The same commands, with configurable gates and reports a reviewer can read |
| Advanced users, researchers | Anyone who wants one narrow operation | Direct access to a single measurement without a full workflow (J14) |

Users often do not know which checks exist. A journey starts from the user's
intent ("improve my skill"), not from a command name.

## What is measured, and on what

The subject is first an individual skill or a repository; skill collections,
documentation sets and MCP servers follow. A comparison is between execution
configurations, not only between files: variants can differ in skills,
guidance, documentation, tools, models, harnesses, subagents, separate sessions,
platform, OS, dependencies or other environment conditions, and there can be more
than two of them. Adaptive (bandit) allocation is one possible future study
strategy for many variants; it is not what "multi-arm" means.

What a measurement can report: quality, task completion, constraint and process
compliance, cost, latency, tokens, actions, how reproducible outcomes and paths
are, whether instructions reached the agent, and the effect on later work and
context. These are separate quantities. None of them is replaced by a single
aggregate score, and novelty is one diagnostic among them, not a definition of
usefulness. Two cautions hold throughout: a scripted step is not proof that the
whole run is deterministic, and a large context is not by itself proof that the
context harms later work; that needs a continuation task and a control.

Evals live with their subject: in the skill's or repository's own tree, or in a
content repository (see [external content](../external-content.md)). Dokimi
executes, validates and analyses them. An experiment states which versions,
tasks, variants, repetitions and budget to use, and the settings it resolved and
the results it produced are recorded with the run. Nothing here assumes a hosted
service, and no external repository has to adopt these journeys or any
governance to be measured.

Budgets cut across every journey: an estimate with its uncertainty before a run,
actual spend during and after it, a cap per run or per case, and what happens to
a run the cap stopped. A budget-limited or inconclusive result is reported as
such. It never becomes a pass.

## Catalog

The ids are stable: a journey keeps its number when others are added or retired.
Use cases are `UC-Jnn-nnn`, acceptance criteria `AC-Jnn-nnn`, so a journey id is
never confused with a use-case or job id.

| Id | User intent | Required outcome |
|---|---|---|
| [J1](journeys.md#j1--start-an-assessment) | Check this and explain the problems and the next step | Initial evidence, unknowns, scope, and a fitting next evaluation plan |
| [J2](journeys.md#j2--improve-my-skill) | Improve my skill | Actionable hypotheses and changes, and a measured effect or a justified rejection of the change |
| [J3](journeys.md#j3--enable-my-repository-for-agents) | Enable my repository for agents | Obstacles found, guidance and evals prepared, representative real tasks verified |
| [J4](journeys.md#j4--check-agentsmd-other-instructions-or-installed-skills) | Check AGENTS.md, other instructions or installed skills | Correctness, loading and reach, adherence, and effect, each established on its own |
| [J5](journeys.md#j5--assess-a-third-party-skill) | Assess a third-party skill | Evidence for the claimed capabilities, problems, applicability, and the limits of the assessment |
| [J6](journeys.md#j6--does-this-skill-work-in-my-use-case) | Does this skill work in my use case? | Evals adapted or added for the user's own tasks, compared numerically |
| [J7](journeys.md#j7--create-or-improve-evals) | Create or improve evals | Relevant tasks, defensible graders, negative controls, explicit coverage gaps |
| [J8](journeys.md#j8--which-models-or-harnesses-can-handle-this) | Which models or harnesses can handle this task or repository? | Feasibility and reliability under stated requirements and conditions |
| [J9](journeys.md#j9--cheapest-suitable-model-or-setup) | What is the cheapest suitable model or setup? | The cost of reaching acceptable outcomes, retries and failures included, under quality constraints |
| [J10](journeys.md#j10--what-effect-does-this-change-have) | What effect does this change have? | Comparable effects, regressions, costs, uncertainty and confounds for any declared change |
| [J11](journeys.md#j11--explain-results-or-recover-a-run) | Explain results, or recover from a failed or inconclusive run | Task failure, evaluation failure, missing evidence and incomparable data told apart; a recovery or the next discriminating experiment |
| [J12](journeys.md#j12--prevent-regressions-in-pr-and-ci) | Prevent regressions in PR/CI | Repeatable, budgeted checks and configurable policy decisions, with evidence |
| [J13](journeys.md#j13--reassess-after-a-change-of-time-model-or-environment) | Reassess after time, model or environment changes | Whether earlier conclusions still hold, and whether assistance should be kept, adapted, shortened or removed |
| [J14](journeys.md#j14--invoke-one-capability-directly) | Invoke a specific capability in a narrow scope | Explicit inputs, settings and costs; reproducible structured output; stated limits; no mandatory full workflow |
| [J15](journeys.md#j15--turn-a-solved-problem-into-a-skill) | Turn a problem solved in chat into a skill | Tasks from the solved problem with the source kept apart, the draft's novelty, and the skill measured against a skill-creator's and against none |

Novelty ("what does this skill add over what the model already knows") is not a
journey of its own. It is a J14 use case when asked for directly, and a
diagnostic step inside J2, J5, J13 and J15.

## Entry points

The journeys are not a wizard to step through. A user, or the agent working for
them, can start from whatever they already have:

- a skill, a repository or a guidance file, with no evals yet (J1, J3, J4, J5);
- existing evals, a question set or a task tree (J6, J7, J10);
- a finished or half-finished run, a report or a trace (J11, J14);
- a prepared experiment: versions, tasks, variants, budget (J8, J9, J10);
- one specific operation (J14);
- a problem just solved in a chat, and no skill yet (J15).

Journeys hand over to each other: J5 often continues as J6; J7 supports most
journeys; J11 can interrupt and resume any measurement; J12 and J13 keep earlier
decisions current. The [flow map](flow-map.md) draws these.

## The CLI these pages name

Commands are named only where they exist in the tree, as
`python cli.py <command>`; the [CLI reference](https://intel.github.io/dokimi/reference/cli/) is
generated from the parser. The current command layout is not a commitment:
regrouping the CLI is under discussion and no target layout has been accepted.
A journey names what the user gets, so it survives a rename; the trace registry
points each criterion at today's command, and a test fails when that command
stops existing. Capabilities with no command yet are marked **proposed**.

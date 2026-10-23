---
name: dokimi-design-evals
description: Design, review or repair evals (tasks, verifiers, negative controls, coverage report) for a skill or repository before a dokimi run. Use when asked to write evals, add tasks for a subject, check whether existing evals or graders can be trusted, or state what a suite does not cover. Not for running a benchmark or comparing arms.
---
# Design evals that measure the user's work

An eval set makes four separate claims. Keep them apart in everything you write:

1. **Valid**: it parses against its schema.
2. **Correctly graded**: the verifier fails an untouched workspace, passes the
   reference solution, and catches the mistakes it is meant to catch.
3. **Relevant**: its tasks are the work users actually do.
4. **Covering**: what it does not test is listed.

A passing self-check proves claim 2 at most. Never report it as evidence of 3 or 4.

## Workflow

### 1. Collect sources (before writing any task)

In this order of weight:

- real user tasks and requests (traces, support threads, examples users wrote);
- issues and PRs, especially **fixed failures**: each one is a ready regression
  scenario with a known base commit and fix;
- the supported workflows the docs promise, and the constraints they state
  (platforms, versions, forbidden actions);
- negative cases: requests the subject should decline, or should not change.

The skill or guidance text under test is a **secondary** source. Tasks written
only from it test whether the agent repeats the skill, not whether it helps.
Record the source of every scenario. A scenario you inferred from code or docs
is a **hypothesis** for the user to confirm, not confirmed user intent.

`python cli.py repo inspect <source>` lists a tree's instruction files, build
and check entry points and risks without a model call; use it to find the
workflows a repository offers.

### 2. Write each scenario

Fill every field of [references/scenario-template.md](references/scenario-template.md):
initial state, request, expected outcome, allowed actions, forbidden
consequences, verifier, how to reproduce, source, status. A worked example is
in [references/example-scenario.md](references/example-scenario.md).

### 3. Check three layers, in this priority

- **Outcome**: is the end state correct? This is the primary verdict.
- **Process**: only steps that are truly mandatory (the user forbade a tool,
  a test must be run before a claim, a file must not be touched). Do not
  require a fixed tool sequence when it is only one valid way to solve the task.
- **Cost**: tokens, wall time, tool calls, retries. Report; gate only when the
  user declared a budget up front.

### 4. Choose the verifier

Prefer deterministic checks: tests, file and diff assertions, exit codes,
parsed output. Use an LLM judge only for qualities no check can capture; then
use a written rubric, calibrate it
(`python cli.py eval judge-validity --answers <answers.json> --sequential --strict`),
and keep a human review of a sample.
A judge that cannot discriminate degraded answers is not evidence.

### 5. Self-test every verifier before any paid run

Run the checks in [references/verifier-self-test.md](references/verifier-self-test.md):
schema validation, oracle passes, untouched workspace fails, isolation, then
the task-appropriate mutations (skipped step, false test claim, deleted tests,
weakened criterion, wrong tool). Pick only the mutations that fit the task; a
task where doing nothing is a correct answer must not be forced to fail on
inaction. Mutation controls have no command yet: they are a manual step, and
the reference says how.

### 6. Multi-step scenarios

Give each step a checkpoint verifier and keep a final-state check. Use
Harbor's multi-step `task.toml` (`steps`). The steps of one session are **one**
task in every count, pass rate and sample size; never report them as
independent tasks.

### 7. Write the coverage report

Use [references/coverage-report.md](references/coverage-report.md): happy path,
branches, recovery, ambiguity, constraints, negative tasks, neighbouring tasks,
each with the scenario ids that cover it or `none`. Put the gaps in the PR
that adds the suite.

### 8. Set the status

`draft` (written, self-test not passed or not reviewed) → `reviewed` (self-test
passed, a person confirmed relevance and the hypotheses) → `holdout` (kept out
of development; used only to confirm a change). Never tune a skill or prompt
against a `holdout` scenario; once it was used for tuning, it is `reviewed`
again. dokimi does not read this status yet; record it next to the task (see
the template).

## Rules

- Do not weaken a criterion to make a verifier pass. Fix the task or the oracle.
- Do not change a grader and the subject in one comparison.
- Keep answers out of the agent's reach: no solution, hidden test or expected
  output inside the workspace or the image the agent sees.
- An egress or host requirement the run cannot meet is `unsupported`, not a pass.
- Report what you did not check, by name.

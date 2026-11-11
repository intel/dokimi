<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Does the context help?

[Feature tree](README.md) · [Detailed guide](../evaluating-treatments.md)

Each card describes one capability. Images use fictional tasks and simplified document excerpts; they are explanations, not captured product screens, runnable configurations or benchmark evidence. Availability applies to the working path described below, not everything depicted in a shared scene.

<a id="01-does-the-context-help"></a>

## Does the context help?

![Does the context help?: Measure the change, with uncertainty.](../assets/features/01-does-the-context-help.webp)

**Availability:** Available on main.

Run the same tasks, model and grader with and without one context change. Inspect paired outcomes and uncertainty before deciding whether to adopt it.

**Boundary:** A/B and N-way arms; controlled context experiments.

[How to use it](../evaluating-treatments.md) · [Implementation](../../dokimi/commands/arms.py) · [Back to feature tree](README.md)

<a id="02-bring-your-agent-context"></a>

## Bring your agent context

![Bring your agent context: Compare the inputs your agent actually uses.](../assets/features/02-bring-your-agent-context.webp)

**Availability:** Available on main.

Choose injected docs, a callable source, a skill, a catalog or a profile according to what the agent will actually receive. Injection and agent-selected loading answer different questions.

**Boundary:** Local docs, Context7, persona/system prompts; support varies by track.

[How to use it](../adding-doc-source.md) · [Implementation](../../dokimi/commands/arms.py) · [Back to feature tree](README.md)

<a id="03-test-combinations"></a>

## Test combinations

![Test combinations: Find whether two treatments help together.](../assets/features/03-test-combinations.webp)

**Availability:** Available on main.

Compare each treatment alone and together against the same baseline. Inspect interaction contrasts where the chosen report supports them.

**Boundary:** Combined treatments; do not assume all tracks support every combination.

[How to use it](../evaluating-treatments.md) · [Implementation](../../dokimi/commands/arms.py) · [Back to feature tree](README.md)

<a id="04-compare-models-harnesses"></a>

## Compare models & harnesses

![Compare models & harnesses: Keep the task fixed while changing the runner.](../assets/features/04-compare-models-harnesses.webp)

**Availability:** Available on main.

Hold the task and grading contract fixed, then compare supported models and harnesses. Record unavailable tools and unsupported environments rather than scoring them as failures.

**Boundary:** Codex, Claude Code, Harbor, BenchFlow: capabilities differ.

[How to use it](../coding-harnesses.md) · [Implementation](../../dokimi/commands/tasks.py) · [Back to feature tree](README.md)

<a id="05-run-real-coding-tasks"></a>

## Run real coding tasks

![Run real coding tasks: Evaluate the patch against executable checks.](../assets/features/05-run-real-coding-tasks.webp)

**Availability:** Available on main.

Supply an executable task with an environment, reference solution and verifier. The agent edits the repository; tests grade the resulting work.

**Boundary:** Container-backed tasks; grader quality still matters.

[How to use it](../coding-harnesses.md) · [Implementation](../../dokimi/commands/tasks.py) · [Back to feature tree](README.md)

<a id="06-make-cost-visible"></a>

## Make cost visible

![Make cost visible: Compare quality with the resources it takes.](../assets/features/06-make-cost-visible.webp)

**Availability:** Available on main.

Inspect recorded usage and estimated spend before committing to a batch. Use the budget controls exposed by the selected command; pricing and usage availability depend on the provider.

**Boundary:** Cost controls and measurements are track-dependent.

[How to use it](../quickstart.md) · [Implementation](../../dokimi/commands/novelty_budget.py) · [Back to feature tree](README.md)

<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Will the agent use it?

[Feature tree](README.md) · [Detailed guide](../reviewing.md)

Each card describes one capability. Images use fictional tasks and simplified document excerpts; they are explanations, not captured product screens, runnable configurations or benchmark evidence. Availability applies to the working path described below, not everything depicted in a shared scene.

<a id="13-will-the-agent-discover-it"></a>

## Will the agent discover it?

![Will the agent discover it?: Separate a present file from a discoverable skill.](../assets/features/13-will-the-agent-discover-it.webp)

**Availability:** Available on main.

Probe skill placements under the intended harness. A file on disk is not necessarily listed to the agent, and a listed skill is not necessarily selected.

**Boundary:** repo skill-reach; discovery depends on the harness.

[How to use it](../what-it-measures.md) · [Implementation](../../dokimi/commands/repo_skill_reach.py) · [Back to feature tree](README.md)

<a id="14-will-it-choose-the-right-skill"></a>

## Will it choose the right skill?

![Will it choose the right skill?: Probe routing, triggering and missed selections.](../assets/features/14-will-it-choose-the-right-skill.webp)

**Availability:** Available + proposed extensions.

Test requests that should select a skill and requests that should not. Compare competing descriptions and inspect actual selection, not only keyword overlap.

**Boundary:** Existing routing; CLI relocation in PR [#595](https://github.com/intel/dokimi/issues/595). Need relevance also matters.

[How to use it](../skill-novelty-walkthrough.md) · [Implementation](../../dokimi/commands/novelty.py) · [Back to feature tree](README.md)

<a id="15-why-did-it-choose-that-tool"></a>

## Why did it choose that tool?

![Why did it choose that tool?: Expose sensitivity to tool names and descriptions.](../assets/features/15-why-did-it-choose-that-tool.webp)

**Availability:** Available on main.

Change tool wording or position while holding the request fixed. Repeat the trials to distinguish preference sensitivity from one random choice.

**Boundary:** Tool preference experiments; control presentation changes.

[How to use it](../tool-preference.md) · [Implementation](../../dokimi/commands/tools.py) · [Back to feature tree](README.md)

<a id="16-what-instructions-loaded"></a>

## What instructions loaded?

![What instructions loaded?: Inspect effective instructions, not just files on disk.](../assets/features/16-what-instructions-loaded.webp)

**Availability:** Available on main.

Use reach probes and effective-instruction evidence to distinguish loaded, truncated, fetched, not-loaded and error outcomes. Keep the harness/version and scenario with the receipt.

**Boundary:** Reach probes, receipts and matrices; expected vs observed loading.

[How to use it](../reach-matrix.md) · [Implementation](../../dokimi/commands/repo.py) · [Back to feature tree](README.md)

<a id="17-budget-the-context"></a>

## Budget the context

![Budget the context: See where instruction volume enters the session.](../assets/features/17-budget-the-context.webp)

**Availability:** Available on main.

Inspect startup, lazy and on-demand guidance volume before a paid run. The repository budget counts characters; it is not a token invoice or a complete runtime context trace.

**Boundary:** Repository budget is measured in characters, not tokens.

[How to use it](../repo-evaluation-contract.md) · [Implementation](../../dokimi/commands/repo.py) · [Back to feature tree](README.md)

<a id="18-split-or-merge-skills"></a>

## Split or merge skills?

![Split or merge skills?: Compare granularity against a wording-only control.](../assets/features/18-split-or-merge-skills.webp)

**Availability:** Research.

Compare a regrouped catalog against both the original and a wording-only control. This is a research direction: routing, cross-skill coverage and load cost must be measured together.

**Boundary:** Issue [#424](https://github.com/intel/dokimi/issues/424); design / experiments, not a shipped optimizer.

[How to use it](../skill-novelty-walkthrough.md) · [Back to feature tree](README.md)

<a id="47-match-the-real-harness"></a>

## Match the real harness

![Match the real harness: Record where the experiment differs from deployment.](../assets/features/47-match-the-real-harness.webp)

**Availability:** Partial.

Check the harness support matrix before treating an experiment as deployment evidence. Unsupported MCP, sidecars or verifier arrangements should remain explicit limitations.

**Boundary:** Issues [#354](https://github.com/intel/dokimi/issues/354)/[#355](https://github.com/intel/dokimi/issues/355): harness fidelity, MCP / sidecars / verifier support gaps.

[How to use it](../coding-harnesses.md) · [Back to feature tree](README.md)

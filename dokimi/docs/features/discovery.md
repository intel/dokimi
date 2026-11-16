<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Will the agent use it?

[Feature tree](README.md) · [Detailed guide](../reviewing.md)

Each card describes one capability. Images use fictional tasks and simplified document excerpts; they are explanations, not captured product screens, runnable configurations or benchmark evidence. Each image shows its own card; Open PR and Research images carry a badge saying so.

<a id="13-will-the-agent-discover-it"></a>
<a id="16-what-instructions-loaded"></a>

## Will the agent discover and load it?

![Will the agent discover and load it?: Separate a present file from a discoverable skill.](../assets/features/13-will-the-agent-discover-it.webp)

**Availability:** Available on main.

A file on disk is not necessarily listed to the agent, and a listed skill or instruction file is not necessarily loaded or selected. Probe skill placements under the intended harness, and use reach probes and effective-instruction receipts to distinguish loaded, truncated, fetched, not-loaded and error outcomes. Keep the harness/version and scenario with each receipt.

**Input:** A supported harness profile/version/model, a fixture catalogue of skill placements, and your repository's guidance files and loading scenarios.

**Scope and limits:** `repo skill-reach` measures listing across fixture placements; reach probes and matrices compare expected and observed loading. Use the routing card below to test selection of your actual skill against real requests.

**Result:** Discovery and loading evidence under the tested harness scenarios.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Command reference](https://intel.github.io/dokimi/reference/cli/repo/) · [Implementation](../../dokimi/commands/repo_skill_reach.py) · [Reach matrix guide](../reach-matrix.md) · [Reach implementation](../../dokimi/commands/repo.py) · [Back to feature tree](README.md)

<a id="14-will-it-choose-the-right-skill"></a>

## Will it choose the right skill?

![Will it choose the right skill?: Probe routing, triggering and missed selections.](../assets/features/14-will-it-choose-the-right-skill.webp)

**Availability:** Partial — implemented core with proposed extensions.

`skill routing` (all-vs-all: `catalog routing`) scores only requests that should select the skill: by lexical overlap without `--model`, and with `--model` by the model's pick from the cards, an upper bound on what a harness selects. Test requests that should not select it, and selection in real Claude Code runs, with [`catalog trigger-probe`](../evaluating-treatments.md#skill-selection-does-the-agent-load-the-right-skill).

**Input:** The skill or catalog, actual competing skills and representative selection/non-selection requests.

**Scope and limits:** Existing routing, under `skill routing` and `catalog routing`. Need relevance also matters.

**Result:** Routing outcomes against actual competing skills and user queries.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../skill-novelty-walkthrough.md) · [Implementation](../../dokimi/commands/novelty.py) · [Back to feature tree](README.md)

<a id="15-why-did-it-choose-that-tool"></a>

## Why did it choose that tool?

![Why did it choose that tool?: Expose sensitivity to tool names and descriptions.](../assets/features/15-why-did-it-choose-that-tool.webp)

**Availability:** Available on main.

Change tool wording or position while holding the request fixed. Repeat the trials to distinguish preference sensitivity from one random choice.

**Input:** Two comparable tools, request cases and controlled names, descriptions or orderings.

**Scope and limits:** Tool preference experiments; control presentation changes.

**Result:** Tool-choice distributions under the tested naming/order conditions.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../tool-preference.md) · [Implementation](../../dokimi/commands/tools.py) · [Back to feature tree](README.md)

<a id="18-split-or-merge-skills"></a>

## Split or merge skills?

![Split or merge skills?: Compare granularity against a wording-only control.](../assets/features/18-split-or-merge-skills.webp)

**Availability:** Partial — free hints on main; the experiment is research.

`catalog check` prints free merge and split hints in its `# granularity` section, a shortlist rather than a decision. Comparing a regrouped catalog against both the original and a wording-only control is a research direction: routing, cross-skill coverage and load cost must be measured together.

**Input:** Candidate skill partitions, matched descriptions and held-out single-skill/cross-skill requests for a proposed experiment.

**Scope and limits:** Issue [#424](https://github.com/intel/dokimi/issues/424); design / experiments, not a shipped optimizer.

**Result:** A research question about skill boundaries, not an automatic restructuring decision.

**Next step:** Read the linked proposal for progress and the related guide for existing components. This card is not a runnable end-to-end interface.

[Usage guide](../external-content.md#1-scan-the-checkout-before-you-spend-anything) · [Back to feature tree](README.md)


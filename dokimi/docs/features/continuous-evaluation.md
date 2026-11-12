<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Will it keep working?

[Feature tree](README.md) · [Detailed guide](../runs.md)

Each card describes one capability. Images use fictional tasks and simplified document excerpts; they are explanations, not captured product screens, runnable configurations or benchmark evidence. Availability applies to the working path described below, not everything depicted in a shared scene.

<a id="39-keep-results-usable"></a>

## Keep results usable

![Keep results usable: Review and reuse the evidence after a run.](../assets/features/39-keep-results-usable.webp)

**Availability:** Partial — implemented core with proposed extensions.

Inspect persisted run artifacts, reports and dashboards without rerunning the model. Resume and re-render only where the selected track supports them; retain source and configuration provenance.

**Input:** Stored run artifacts and the selected report reader's supported schema.

**Scope and limits:** Support varies by track; unified JSON envelope PR [#590](https://github.com/intel/dokimi/pull/590).

**Result:** Inspectable stored artifacts and supported reports.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../runs.md) · [Implementation](../../dokimi/commands/runs.py) · [Back to feature tree](README.md)

<a id="40-catch-the-next-regression"></a>

## Catch the next regression

![Catch the next regression: Recheck known tasks when context changes.](../assets/features/40-catch-the-next-regression.webp)

**Availability:** Partial.

Compare a change against saved baselines and explicit thresholds in existing CI workflows. The broader record/revalidate lifecycle is not one complete shipped workflow.

**Input:** A saved baseline, comparable candidate results and explicit regression thresholds.

**Scope and limits:** Baseline gates / reusable CI in main; full record/revalidate [#318](https://github.com/intel/dokimi/issues/318)/[#121](https://github.com/intel/dokimi/issues/121)/[#138](https://github.com/intel/dokimi/issues/138).

**Result:** Baseline comparison findings under configured thresholds.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../benchmarking-and-comparison.md) · [Implementation](../../dokimi/commands/baseline_regression.py) · [Back to feature tree](README.md)

<a id="41-compare-unsafe-behaviour"></a>

## Compare unsafe behaviour

![Compare unsafe behaviour: Surface unsafe actions introduced by a context change.](../assets/features/41-compare-unsafe-behaviour.webp)

**Availability:** Open PR.

The proposed security comparison surfaces unsafe behaviour per arm relative to a baseline. Container isolation is separate from evidence about the actions the agent takes; trap generation remains broader work.

**Input:** Recorded repository-run traces per arm and baseline; additional canary/exec/egress evidence where available.

**Scope and limits:** PR [#588](https://github.com/intel/dokimi/pull/588) / [#360](https://github.com/intel/dokimi/issues/360); broader traps and instrumentation remain incomplete.

**Result:** Proposed per-arm unsafe-behaviour evidence; isolation alone does not establish safety.

**Next step:** Read the linked proposal for progress and the related guide for existing components. This card is not a runnable end-to-end interface.

[Related existing workflow](../coding-harnesses.md) · [Back to feature tree](README.md)

<a id="42-close-the-improvement-loop"></a>

## Close the improvement loop

![Close the improvement loop: Join knowledge diagnostics to measured task effects.](../assets/features/42-close-the-improvement-loop.webp)

**Availability:** Partial — existing components; broader workflow is research.

Use existing diagnostics and remeasurement to review an edit. A general optimizer and automatic joined knowledge/task evidence are unfinished; do not assume an autonomous improvement-and-merge loop.

**Input:** The original skill, diagnostic evidence, proposed edit and a fixed task evaluation set.

**Scope and limits:** Components in main; optimizer [#220](https://github.com/intel/dokimi/issues/220) and joined evidence [#98](https://github.com/intel/dokimi/issues/98) unfinished.

**Result:** Diagnostic and remeasurement evidence from existing components; the general optimizer remains unfinished.

**Next step:** Read the linked proposal for progress and the related guide for existing components. This card is not a runnable end-to-end interface.

[Related existing workflow](../skill-novelty-walkthrough.md) · [Back to feature tree](README.md)

<a id="48-publish-evidence-with-a-skill"></a>

## Publish evidence with a skill

![Publish evidence with a skill: Carry evaluation evidence into catalog maintenance.](../assets/features/48-publish-evidence-with-a-skill.webp)

**Availability:** Partial — evidence artifacts exist; publishing lifecycle is planned.

Keep the tested skill revision attached to its evidence and review the evidence after changes. Catalog promotion and the full evidence lifecycle are development directions rather than a universal publishing command.

**Input:** A skill revision and its evaluation artifacts, with the catalog's evidence/promotion policy where implemented.

**Scope and limits:** Issue [#138](https://github.com/intel/dokimi/issues/138) and unified lifecycle [#121](https://github.com/intel/dokimi/issues/121); not a complete shipped workflow.

**Result:** Existing evidence artifacts tied to a revision; universal catalog promotion remains unfinished.

**Next step:** Read the linked proposal for progress and the related guide for existing components. This card is not a runnable end-to-end interface.

[Related existing workflow](../runs.md) · [Back to feature tree](README.md)

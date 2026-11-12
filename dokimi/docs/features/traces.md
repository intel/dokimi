<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# What did the agent do?

[Feature tree](README.md) · [Detailed guide](../context-window.md)

Each card describes one capability. Images use fictional tasks and simplified document excerpts; they are explanations, not captured product screens, runnable configurations or benchmark evidence. Availability applies to the working path described below, not everything depicted in a shared scene.

<a id="35-inspect-the-context-window"></a>

## Inspect the context window

![Inspect the context window: See how the session context evolves.](../assets/features/35-inspect-the-context-window.webp)

**Availability:** Available on main.

Inspect supported Claude Code traces for startup load, growth, compaction and tool-result weight. A repository budget predicts loading volume; the trace describes one actual session.

**Input:** Supported Claude Code session traces containing context and compaction observations.

**Scope and limits:** Context inspection; compaction is a trace observation.

**Result:** Context growth, loading and compaction evidence available in the trace.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../context-window.md) · [Implementation](../../dokimi/commands/context.py) · [Back to feature tree](README.md)

<a id="36-compare-execution-paths"></a>

## Compare execution paths

![Compare execution paths: Distinguish a stable answer from a stable process.](../assets/features/36-compare-execution-paths.webp)

**Availability:** Available on main.

Repeat one task and compare outcome stability, tool paths and declared process contracts. A stable final answer can hide unstable or wasteful execution paths.

**Input:** Repeated runs of the same task and their traces, plus any declared process contracts.

**Scope and limits:** Trace consistency; process contracts are not universal adherence.

**Result:** Outcome and execution-path consistency measures across repeated runs.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../run-consistency.md) · [Implementation](../../dokimi/commands/context.py) · [Back to feature tree](README.md)

<a id="37-find-candidates-for-ablation"></a>

## Find candidates for ablation

![Find candidates for ablation: Use traces to choose what to test next.](../assets/features/37-find-candidates-for-ablation.webp)

**Availability:** Available on main.

Use session observations to select instructions for an ablation experiment. Referenced, acted-on and corrected rules are candidates to investigate, not established causes of success.

**Input:** Session logs and the guidance rules whose observed use you want to investigate.

**Scope and limits:** Session prefilter suggests candidates; it does not establish causality.

**Result:** Candidate instructions for controlled ablation, not automatic deletion decisions.

**Next step:** Use the linked workflow to configure this check, then [interpret the result](../start/results.md).

[Usage guide](../session-prefilter.md) · [Implementation](../../dokimi/commands/context.py) · [Back to feature tree](README.md)

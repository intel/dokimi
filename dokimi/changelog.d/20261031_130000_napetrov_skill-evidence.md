<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`subjects evidence SCORECARD`: a subject scorecard as a `skill_evidence.v1` document** — the file a publisher puts next to a skill so a client can read what was measured: one result per (matrix cell, non-baseline arm) with model, provider, harness, effort, and the paired test of the arm against the cell's recorded baseline arm on `avg_aggregate` (`n` pairs, delta, confidence interval, corrected p-value) and a verdict (`improved` or `regressed` only on a delta that is significant after correction, `inconclusive` otherwise). It is keyed by `subject.digest`, the scorecard's `distribution_digest`, which is what a `.well-known/agent-skills` index entry carries for the skill. Free: it reads the saved scorecard and makes no model call. Every value comes from the scorecard; one it does not hold, such as `effort` before runs record it, is `null`. Writes `skill-evidence.json` next to the scorecard unless `--out` names a file; exits 2 when the scorecard has nothing to publish or `--out` is the scorecard itself. The schema is experimental until the agentskills evidence proposal settles.
- **Matrix rollup and subject scorecard cells record `baseline_arm`**, so a reader no longer has to guess the baseline from the arm that has no delta (an arm whose every answer failed has none either).

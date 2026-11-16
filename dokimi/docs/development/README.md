<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Features in development

This guide describes the user outcomes Dokimi is working toward, including open implementations and longer-term proposals. It complements the [feature cards](../features/README.md): a feature can have a working core and unfinished extensions.

Reviewed on **11 November 2026** against the documented source revision [ae08a920](https://github.com/intel/dokimi/tree/ae08a92063d4bd180f8e03515ab3068f0c71c291) and all open issues and PRs. Pull requests marked merged were checked against main on 14 November 2026. This is a snapshot, not a release schedule. An open PR is under review, not a released capability; it may contain code, documentation or a study. Old umbrella issues can still describe merged components as missing; the current usage guides describe the working paths.

| Your goal | Development direction |
|---|---|
| Improve or assess a skill | [Bundle checks](#skill-bundles-and-catalogs), [guided improvement](#guided-improvement) |
| Build convincing evals | [Coverage and relevance](#coverage-and-relevance), [grading evidence](#grading-evidence) |
| Make agent instructions effective | [Instruction delivery and adherence](#instruction-delivery-and-adherence) |
| Automate evaluation safely | [Repeatable CI and evidence](#repeatable-ci-and-evidence), [CLI migration](interfaces.md) |
| Compare real agent setups | [Harness fidelity and security](#harness-fidelity-and-security) |
| Know where results can mislead | [Known limitations and fixes](limitations.md) |

The [work index](work-index.md) accounts for every open item, including reliability work and internal maintenance that is not a new user feature.

## Skill bundles and catalogs

**Working now:** catalog validation, per-skill checks, static catalog receipts, routing diagnostics and skill analysis. See [the skill route](../start/skills.md).

**Implementations (merged PRs are marked):**

- [#455](https://github.com/intel/dokimi/pull/455) (merged) checks bundled script line endings, Python parsing and supported shell syntax without running/importing the scripts. Optional ruff/shellcheck findings depend on tools being present. A skipped tool is not evidence that the script passed that check.
- [#459](https://github.com/intel/dokimi/pull/459) (merged) adds opt-in network checks for missing packages and dead links. Timeouts, access denials and untested targets remain distinct from confirmed missing targets; network availability is not package trustworthiness.
- [#556](https://github.com/intel/dokimi/pull/556) (merged) identifies missing bundle paths in inline code; [#559](https://github.com/intel/dokimi/pull/559) finds bundled files the skill and referenced documents never name. These are scoped heuristics, not proof a file is unnecessary.
- [#558](https://github.com/intel/dokimi/pull/558) separates plain Markdown without skill frontmatter from other invalid skills and suggests a starting frontmatter block.
- [#557](https://github.com/intel/dokimi/pull/557) (merged) warns about unrestricted Bash grants and combines them with risky-command findings for the proposed strict policy. This is static screening, not behavioural proof.
- [#454](https://github.com/intel/dokimi/pull/454) (merged) improves recognition of selection cues in multilingual descriptions.

**Further work:** corpus-scale quality and performance gaps [#413](https://github.com/intel/dokimi/issues/413), streaming catalog bodies [#434](https://github.com/intel/dokimi/issues/434), and skill partition experiments [#424](https://github.com/intel/dokimi/issues/424). Free granularity hints already suggest candidates; a measured split/merge decision must compare matched descriptions, held-out requests, multi-skill coverage and context cost.

**What success would look like:** an author can distinguish a broken bundle, an unselected skill and a skill that loads but does not help, without treating heuristic warnings as measured task failure.

## Coverage and relevance

**Working now:** task generation from supported PR/replay/code-index inputs, grader controls, headroom diagnostics and coverage reporting against a supplied frozen need graph. These do not prove that generated tasks represent real user needs.

**Implementations (merged PRs are marked):** [#594](https://github.com/intel/dokimi/pull/594) (merged) builds a frozen need graph from a skill's name and description, with hypothesis labels and provenance; [#441](https://github.com/intel/dokimi/pull/441) (merged) maps skill requirements to existing checks and proposes uncovered candidate checks. The latter is model-estimated mapping, not verified coverage.

**Further work:** [#331](https://github.com/intel/dokimi/issues/331) includes confirmation/interview of hypothesized needs, repeatable mappings and relevance measures; [#97](https://github.com/intel/dokimi/issues/97) tracks broader task drafting and explicit coverage gaps. Existing generation is narrower than the full journey. [#356](https://github.com/intel/dokimi/issues/356), [#370](https://github.com/intel/dokimi/issues/370) (closed) and [#596](https://github.com/intel/dokimi/pull/596) (merged) address changed question content and cached repeats that could make headroom look stronger than the independent evidence supports.

**What success would look like:** a report names the declared problem space, confirmed versus hypothesized needs, uncovered areas, mapping evidence and the independent trials behind a conclusion. See [eval-design cards](../features/eval-design.md).

## Grading evidence

**Working now:** multiple grading mechanisms, judge checks and deterministic controls; support differs by evaluation track. Agreement between judges is not ground truth.

**Implementations (merged PRs are marked):** novelty judge panels [#489](https://github.com/intel/dokimi/pull/489), supported by the study in [#482](https://github.com/intel/dokimi/pull/482), add per-judge outcomes, aggregation policies and sensitivity reporting. That does not by itself validate every panel or provide panels on every scoring path.

[#472](https://github.com/intel/dokimi/pull/472) (merged) introduces programmatic checks with veto/sufficient/evidence roles, identifier checks and help-based grounding for novelty. Its execution/source checks and broader source-drift model remain proposals. Negative or version-specific claims need appropriate interpretation; a missing identifier is not automatically a false statement.

**Further work:** [#407](https://github.com/intel/dokimi/issues/407) connects panel quorum, role/model identity, references, deterministic evidence and per-claim support. [#48](https://github.com/intel/dokimi/issues/48) concerns validity under the actual material shown to the judge. [#621](https://github.com/intel/dokimi/pull/621) (merged) consolidates default-judge selection; [#309](https://github.com/intel/dokimi/issues/309) tracks model identity aliases that can hide self-judging. Recomputed study evidence [#585](https://github.com/intel/dokimi/issues/585) must use matched rows.

**What success would look like:** a user can see which check decided a claim, what evidence it used, which judges failed or disagreed, and when there is insufficient evidence. See [judge cards](../features/judging.md).

## Instruction delivery and adherence

**Working now:** repository scans, reach probes and receipts, rule ledgers, context/trace diagnostics and guidance comparisons. They separate correctness, loading and measured task outcomes.

**Open and planned extensions:** [#593](https://github.com/intel/dokimi/pull/593) (merged) broadens manifest-free discovery to rules/imports used by Claude Code. [#350](https://github.com/intel/dokimi/issues/350) pins the harness profile behind expected-loading receipts. [#626](https://github.com/intel/dokimi/issues/626) proposes reporting searches of guidance separately from whole-file reads; the existing read counter misses grep/rg-based delivery.

[#96](https://github.com/intel/dokimi/issues/96) tracks instruction-specific adherence evidence and task impact. Existing process contracts cover declared behaviours, not arbitrary natural-language instructions. [#342](https://github.com/intel/dokimi/issues/342) coordinates configuration-effectiveness work, including telemetry integration and evidence attached to instructions; many of its original building blocks already exist.

**What success would look like:** the report distinguishes expected, loaded, searched, followed and outcome-relevant instructions, with the harness/profile revision attached. See [repository cards](../features/repositories.md) and [trace cards](../features/traces.md).

## Harness fidelity and security

**Working now:** supported coding harnesses, container-backed tasks, process metrics and a Claude Code fidelity golden. This does not establish every harness feature or every execution mode.

**Implementation (merged PRs are marked):** [#588](https://github.com/intel/dokimi/pull/588) (merged) compares trace-based unsafe-behaviour signals per arm against a baseline. Its report distinguishes unmeasured cells and missing instrumentation. Broader canary planting, execution/egress capture and trap tasks remain in [#360](https://github.com/intel/dokimi/issues/360). Container isolation and observed safe behaviour are different claims.

**Further work:** [#354](https://github.com/intel/dokimi/issues/354) expands fidelity coverage against real harness payloads; [#355](https://github.com/intel/dokimi/issues/355) records unsupported Harbor MCP servers, compose sidecars and separate verifier environments. [#148](https://github.com/intel/dokimi/issues/148) addresses fork-context skills bypassing the intended no-delegation condition.

**What success would look like:** unsupported setups are explicit, the actual model context matches the declared arm, and security reports identify both observed findings and unmeasured channels.

## Guided improvement

**Working now:** diagnose content, propose/compare shortening candidates, run task comparisons and retain evidence through separate tools. Novelty and task impact must still be interpreted separately.

**Planned:** [#98](https://github.com/intel/dokimi/issues/98) joins a diagnosis to the effect of an edit. [#220](https://github.com/intel/dokimi/issues/220) separates measurement gates from candidate optimization: first validate headroom, graders, leakage and held-out evaluation, then explore an improvement command. A candidate must be compared with both the original and no-skill baseline. An optimizer that writes/merges changes autonomously is not the promised initial outcome.

[#121](https://github.com/intel/dokimi/issues/121) connects these pieces into guided skill/repository/eval workflows. Some contracts and a design-evals skill already exist; the entire proposed kit is not one complete installed workflow.

**What success would look like:** a proposed edit comes with its provenance, development versus held-out evidence, cost, and a justified keep/reject/inconclusive decision. See [continuous evaluation](../features/continuous-evaluation.md).

## Repeatable CI and evidence

**Working now:** static catalog plan/result receipts, stored run inspection, baseline comparisons and reusable repository evaluation checks.

**Planned:** [#318](https://github.com/intel/dokimi/issues/318) joins PR checks, evidence recording and scheduled revalidation; [#138](https://github.com/intel/dokimi/issues/138) connects this to catalog evidence and promotion. The intended workflow selects affected subjects, compares pinned conditions, preserves trusted evidence and rechecks it after relevant changes. The baseline storage and promotion policy must be explicit; a static pass does not qualify a skill as validated.

[#99](https://github.com/intel/dokimi/issues/99) separates inconclusive/budget-stopped results from pass/fail policy. [#121](https://github.com/intel/dokimi/issues/121) owns the shared engine contracts. Existing schemas or reusable checks do not mean the whole proposed lifecycle is complete.

**What success would look like:** local, agent and CI runs share comparable inputs and verdict semantics, partial runs cannot silently pass, and evidence remains attached to the exact subject revision. See [automation today](../start/automation.md) and [interface changes](interfaces.md).

## How this guide stays accurate

Update the owning feature card and this direction when implementation scope changes. Replace proposal language only after checking the merged source and its limits; record release availability separately. Do not turn an old unchecked umbrella checkbox into a claim that working functionality is missing. The work index is a dated inventory, while linked GitHub items hold live discussion and state.

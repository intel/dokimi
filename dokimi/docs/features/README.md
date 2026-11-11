<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Feature tree

Start with the question you want to answer. Each leaf below links to one feature card with an illustration, working path, availability and limits. The detailed guides remain authoritative for command syntax and schemas.

**Availability:** “Available on main” means implemented in the reviewed source, not verified in every published release. “Partial” names the working portion and the remaining boundary. “Open PR”, “Research” and “Planned” are not shipped interfaces. An open umbrella issue may contain already implemented pieces.

Reviewed against [ae08a920](https://github.com/intel/dokimi/tree/ae08a92063d4bd180f8e03515ab3068f0c71c291) and open feature PRs on 11 November 2026. This is a capability guide, not a release guarantee. Check your installed version and the linked PR before using a development feature.

## Browse by question

- **[Does the context help?](experiments.md)**
    - [Does the context help?](experiments.md#01-does-the-context-help) — Available on main
    - [Bring your agent context](experiments.md#02-bring-your-agent-context) — Available on main
    - [Test combinations](experiments.md#03-test-combinations) — Available on main
    - [Compare models & harnesses](experiments.md#04-compare-models-harnesses) — Available on main
    - [Run real coding tasks](experiments.md#05-run-real-coding-tasks) — Available on main
    - [Make cost visible](experiments.md#06-make-cost-visible) — Available on main
- **[Which parts of a skill matter?](skills.md)**
    - [What did the skill teach?](skills.md#07-what-did-the-skill-teach) — Available on main
    - [Find the useful passages](skills.md#08-find-the-useful-passages) — Available on main
    - [Read the whole bundle](skills.md#09-read-the-whole-bundle) — Available on main
    - [Shorten without losing value](skills.md#10-shorten-without-losing-value) — Available on main
    - [Scan a skill collection](skills.md#11-scan-a-skill-collection) — Available on main
    - [Check skill structure](skills.md#12-check-skill-structure) — Available + proposed extensions
    - [Check bundled scripts](skills.md#43-check-bundled-scripts) — Open PR
    - [Check external references](skills.md#44-check-external-references) — Open PR
    - [Find bundle packaging gaps](skills.md#45-find-bundle-packaging-gaps) — Open PR
    - [Relate skills to user needs](skills.md#46-relate-skills-to-user-needs) — Available on main
- **[Will the agent use it?](discovery.md)**
    - [Will the agent discover it?](discovery.md#13-will-the-agent-discover-it) — Available on main
    - [Will it choose the right skill?](discovery.md#14-will-it-choose-the-right-skill) — Available + proposed extensions
    - [Why did it choose that tool?](discovery.md#15-why-did-it-choose-that-tool) — Available on main
    - [What instructions loaded?](discovery.md#16-what-instructions-loaded) — Available on main
    - [Budget the context](discovery.md#17-budget-the-context) — Available on main
    - [Split or merge skills?](discovery.md#18-split-or-merge-skills) — Research
    - [Match the real harness](discovery.md#47-match-the-real-harness) — Partial
- **[Does repository guidance work?](repositories.md)**
    - [Check repository claims](repositories.md#19-check-repository-claims) — Available + proposed extensions
    - [Understand repository scope](repositories.md#20-understand-repository-scope) — Available on main
    - [Own a repeatable repo eval](repositories.md#21-own-a-repeatable-repo-eval) — Available on main
    - [Compare guidance changes](repositories.md#22-compare-guidance-changes) — Available on main
    - [Keep a rule ledger](repositories.md#23-keep-a-rule-ledger) — Available on main
    - [Review code with a grader](repositories.md#24-review-code-with-a-grader) — Available on main
- **[Do the tests cover the work?](eval-design.md)**
    - [Build tasks from real work](eval-design.md#25-build-tasks-from-real-work) — Available on main
    - [Test repository exploration](eval-design.md#26-test-repository-exploration) — Available on main
    - [Generate evaluation questions](eval-design.md#27-generate-evaluation-questions) — Available on main
    - [Which needs are untested?](eval-design.md#28-which-needs-are-untested) — Partial + proposed extension
    - [Which skill rules lack checks?](eval-design.md#29-which-skill-rules-lack-checks) — Open PR
    - [Can the grader be trusted?](eval-design.md#30-can-the-grader-be-trusted) — Available on main
    - [Is there room to improve?](eval-design.md#31-is-there-room-to-improve) — Available + proposed extensions
    - [Assess documentation](eval-design.md#38-assess-documentation) — Available on main
- **[Can the grades be trusted?](judging.md)**
    - [Choose the grading method](judging.md#32-choose-the-grading-method) — Available on main
    - [Check the judges](judging.md#33-check-the-judges) — Available + proposed extensions
    - [Ground novelty checks](judging.md#34-ground-novelty-checks) — Open PR
- **[What did the agent do?](traces.md)**
    - [Inspect the context window](traces.md#35-inspect-the-context-window) — Available on main
    - [Compare execution paths](traces.md#36-compare-execution-paths) — Available on main
    - [Find candidates for ablation](traces.md#37-find-candidates-for-ablation) — Available on main
- **[Will it keep working?](continuous-evaluation.md)**
    - [Keep results usable](continuous-evaluation.md#39-keep-results-usable) — Available + proposed extensions
    - [Catch the next regression](continuous-evaluation.md#40-catch-the-next-regression) — Partial
    - [Compare unsafe behaviour](continuous-evaluation.md#41-compare-unsafe-behaviour) — Open PR
    - [Close the improvement loop](continuous-evaluation.md#42-close-the-improvement-loop) — Partial / research
    - [Publish evidence with a skill](continuous-evaluation.md#48-publish-evidence-with-a-skill) — Planned / partial

## Read a card

The illustration explains one question using concrete documents, task outcomes or trace evidence. Related features can share a scene, but every leaf has a separate card and scope. The text is the accessible equivalent and supplies the limits that cannot fit into the artwork. All examples are fictional; outcome marks illustrate a comparison, not a measured improvement in Dokimi.

Use [reviewing](../reviewing.md) for a cheapest-first workflow, [what Dokimi measures](../what-it-measures.md) for the measurement model, and [user journeys](../user-journeys/README.md) for acceptance criteria. The tree is the capability navigation; those pages serve different purposes.

## Maintaining the cards

When a feature changes, update its availability and working boundary in the owning card, then the matching leaf here. Verify code and release availability separately. Keep open-PR and research links beside the unfinished capability. Do not embed volatile statuses into the illustrations. Preserve card anchors when renaming a title, and link a new topic page from both the docs home and site navigation.

The artwork was generated for this documentation and exported as individual scene crops. It is not a screenshot of a Dokimi GUI. Source-like fragments are visual explanations; use the linked guides for executable commands, manifests and schemas. Illustrations use the repository's navy, blue and cyan palette, with green for positive evidence and orange for findings. Text labels carry the meaning as well as colour.

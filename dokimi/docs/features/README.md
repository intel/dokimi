<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Feature tree

Start with the question you want to answer. Each leaf below links to one feature card with an illustration, working path, availability and limits. The detailed guides remain authoritative for command syntax and schemas.

**Availability lives in each card:** “Available on main” means implemented in the reviewed source, not verified in every published release. “Partial” names the implemented portion and its unfinished extension. “Open PR” and “Research” describe development work, not shipped interfaces. An open umbrella issue may contain already implemented pieces.

Reviewed against [ae08a920](https://github.com/intel/dokimi/tree/ae08a92063d4bd180f8e03515ab3068f0c71c291) and open feature PRs on 11 November 2026. This is a capability guide, not a release guarantee. Check your installed version and the linked PR before using a development feature.

## Browse by question

- **[Does the context help?](experiments.md)**
    - [Does the context help?](experiments.md#01-does-the-context-help)
    - [Bring your agent context](experiments.md#02-bring-your-agent-context)
    - [Test combinations](experiments.md#03-test-combinations)
    - [Compare models & harnesses](experiments.md#04-compare-models-harnesses)
    - [Run real coding tasks](experiments.md#05-run-real-coding-tasks)
    - [Make cost visible](experiments.md#06-make-cost-visible)
- **[Which parts of a skill matter?](skills.md)**
    - [What did the skill teach?](skills.md#07-what-did-the-skill-teach)
    - [Find the useful passages](skills.md#08-find-the-useful-passages)
    - [Read the whole bundle](skills.md#09-read-the-whole-bundle)
    - [Shorten without losing value](skills.md#10-shorten-without-losing-value)
    - [Scan a skill collection](skills.md#11-scan-a-skill-collection)
    - [Check skill structure](skills.md#12-check-skill-structure)
    - [Check bundled scripts](skills.md#43-check-bundled-scripts)
    - [Check external references](skills.md#44-check-external-references)
    - [Find bundle packaging gaps](skills.md#45-find-bundle-packaging-gaps)
    - [Relate skills to user needs](skills.md#46-relate-skills-to-user-needs)
- **[Will the agent use it?](discovery.md)**
    - [Will the agent discover it?](discovery.md#13-will-the-agent-discover-it)
    - [Will it choose the right skill?](discovery.md#14-will-it-choose-the-right-skill)
    - [Why did it choose that tool?](discovery.md#15-why-did-it-choose-that-tool)
    - [What instructions loaded?](discovery.md#16-what-instructions-loaded)
    - [Budget the context](discovery.md#17-budget-the-context)
    - [Split or merge skills?](discovery.md#18-split-or-merge-skills)
    - [Match the real harness](discovery.md#47-match-the-real-harness)
- **[Does repository guidance work?](repositories.md)**
    - [Check repository claims](repositories.md#19-check-repository-claims)
    - [Understand repository scope](repositories.md#20-understand-repository-scope)
    - [Own a repeatable repo eval](repositories.md#21-own-a-repeatable-repo-eval)
    - [Compare guidance changes](repositories.md#22-compare-guidance-changes)
    - [Keep a rule ledger](repositories.md#23-keep-a-rule-ledger)
    - [Review code with a grader](repositories.md#24-review-code-with-a-grader)
- **[Do the tests cover the work?](eval-design.md)**
    - [Build tasks from real work](eval-design.md#25-build-tasks-from-real-work)
    - [Test repository exploration](eval-design.md#26-test-repository-exploration)
    - [Generate evaluation questions](eval-design.md#27-generate-evaluation-questions)
    - [Which needs are untested?](eval-design.md#28-which-needs-are-untested)
    - [Which skill rules lack checks?](eval-design.md#29-which-skill-rules-lack-checks)
    - [Can the grader be trusted?](eval-design.md#30-can-the-grader-be-trusted)
    - [Is there room to improve?](eval-design.md#31-is-there-room-to-improve)
    - [Assess documentation](eval-design.md#38-assess-documentation)
- **[Can the grades be trusted?](judging.md)**
    - [Choose the grading method](judging.md#32-choose-the-grading-method)
    - [Check the judges](judging.md#33-check-the-judges)
    - [Ground novelty checks](judging.md#34-ground-novelty-checks)
- **[What did the agent do?](traces.md)**
    - [Inspect the context window](traces.md#35-inspect-the-context-window)
    - [Compare execution paths](traces.md#36-compare-execution-paths)
    - [Find candidates for ablation](traces.md#37-find-candidates-for-ablation)
- **[Will it keep working?](continuous-evaluation.md)**
    - [Keep results usable](continuous-evaluation.md#39-keep-results-usable)
    - [Catch the next regression](continuous-evaluation.md#40-catch-the-next-regression)
    - [Compare unsafe behaviour](continuous-evaluation.md#41-compare-unsafe-behaviour)
    - [Close the improvement loop](continuous-evaluation.md#42-close-the-improvement-loop)
    - [Publish evidence with a skill](continuous-evaluation.md#48-publish-evidence-with-a-skill)

## Read a card

The illustration explains one question using concrete documents, task outcomes or trace evidence. Related features can share a scene, but every leaf has a separate card and scope. The text is the accessible equivalent and supplies the limits that cannot fit into the artwork. All examples are fictional; outcome marks illustrate a comparison, not a measured improvement in Dokimi.

Use [reviewing](../reviewing.md) for a cheapest-first workflow, [what Dokimi measures](../what-it-measures.md) for the measurement model, and [user journeys](../user-journeys/README.md) for acceptance criteria. The tree is the capability navigation; those pages serve different purposes.

## Maintaining the cards

When a feature changes, update its availability and working boundary in the owning card. The tree links to that card and deliberately does not duplicate its status. Verify code and release availability separately. Keep open-PR and research links beside the unfinished capability. Do not embed volatile statuses into the illustrations. Preserve card anchors when renaming a title, and link a new topic page from both the docs home and site navigation.

The artwork was generated for this documentation and exported as individual scene crops. It is not a screenshot of a Dokimi GUI. Source-like fragments are visual explanations; use the linked guides for executable commands, manifests and schemas. Illustrations use the repository's navy, blue and cyan palette, with green for positive evidence and orange for findings. Text labels carry the meaning as well as colour.

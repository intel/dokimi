<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Does repository guidance work?

[Feature tree](README.md) · [Detailed guide](../repo-evaluation-contract.md)

Each card describes one capability. Images use fictional tasks and simplified document excerpts; they are explanations, not captured product screens, runnable configurations or benchmark evidence. Availability applies to the working path described below, not everything depicted in a shared scene.

<a id="19-check-repository-claims"></a>

## Check repository claims

![Check repository claims: Find instructions that no longer match the repo.](../assets/features/19-check-repository-claims.webp)

**Availability:** Available + proposed extensions.

Scan guidance claims against repository files, symbols, commands and build labels. Review a finding in its source context; a missing path is different from a syntax error.

**Boundary:** Static scans / fence checks; Claude rules and imports in PR [#593](https://github.com/intel/dokimi/issues/593).

[How to use it](../repo-evaluation-contract.md) · [Implementation](../../dokimi/commands/repo.py) · [Back to feature tree](README.md)

<a id="20-understand-repository-scope"></a>

## Understand repository scope

![Understand repository scope: Connect instructions to the code they govern.](../assets/features/20-understand-repository-scope.webp)

**Availability:** Available on main.

Inspect instruction scopes, capabilities, checks, hooks and scripts. An optional SCIP index adds code structure; references and derives are not necessarily runtime calls.

**Boundary:** repo inspect; symbol references are not necessarily call edges.

[How to use it](../repo-evaluation-contract.md) · [Implementation](../../dokimi/commands/repo.py) · [Back to feature tree](README.md)

<a id="21-own-a-repeatable-repo-eval"></a>

## Own a repeatable repo eval

![Own a repeatable repo eval: Keep tasks and evaluation setup with the repository.](../assets/features/21-own-a-repeatable-repo-eval.webp)

**Availability:** Available on main.

Keep the manifest, tasks and graders with the repository. Validate it, run the chosen arms, and compare reports while preserving the task and source revisions.

**Boundary:** Repository manifest workflow and executable review tasks.

[How to use it](../repo-evaluation-contract.md) · [Implementation](../../dokimi/commands/repo_run.py) · [Back to feature tree](README.md)

<a id="22-compare-guidance-changes"></a>

## Compare guidance changes

![Compare guidance changes: Measure instruction edits and code-context patches.](../assets/features/22-compare-guidance-changes.webp)

**Availability:** Available on main.

Compare supported guidance revisions, removal, bridge, format or overlay variants and patches. Check the contract for the exact arm grammar and compatible harness.

**Boundary:** Guidance variants, format/as-skill and overlays vary by harness.

[How to use it](../what-it-measures.md) · [Implementation](../../dokimi/repos/arms.py) · [Back to feature tree](README.md)

<a id="23-keep-a-rule-ledger"></a>

## Keep a rule ledger

![Keep a rule ledger: Retain the evidence behind each recommendation.](../assets/features/23-keep-a-rule-ledger.webp)

**Availability:** Available on main.

Record a rule hypothesis and the cases it is intended to help. Read keep/review/insufficient-evidence verdicts in light of whole-arm effects; isolate a rule to attribute causality.

**Boundary:** repo compare --ledger; isolate rules before making causal claims.

[How to use it](../rule-ledger.md) · [Implementation](../../dokimi/commands/repo_run.py) · [Back to feature tree](README.md)

<a id="24-review-code-with-a-grader"></a>

## Review code with a grader

![Review code with a grader: Evaluate whether the review finds the target problem.](../assets/features/24-review-code-with-a-grader.webp)

**Availability:** Available on main.

Use the built-in seeded-review or replay grader to evaluate review findings against the task evidence. Read recall, decoys and matching limits rather than assuming a passing review is complete.

**Boundary:** Built-in review_seed and review_replay graders.

[How to use it](../repo-evaluation-contract.md) · [Implementation](../../dokimi/commands/tasks_generate.py) · [Back to feature tree](README.md)

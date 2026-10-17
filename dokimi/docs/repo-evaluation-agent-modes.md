<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Repository evaluation: agent modes, effort, performance and team rules

> **Status: PROPOSED.** This document extends the
> [repository evaluation contract](repo-evaluation-contract.md) and the
> mergeability proposal (#67, `repo-evaluation-mergeability.md`). It covers
> what a repository task measures beyond test pass and mergeability: which
> way of using an agent it models, the reasoning effort of the harness, a
> performance check, and the team's own review rules. Nothing here is
> implemented in the framework yet. Measurements from oneDAL are recorded in
> the pull request that adds this document, not here.

## 1. Effort

The harness's reasoning effort is a dimension of the cell, like the model.
Effort moves `strict_pass`, `mergeable` and cost per run together, so a
comparison that pools effort levels compares harness settings, not arms.

- The harness profile carries the effort.
- Reports never pool cells that differ in it.

Measured on oneDAL; see the PR description.

## 2. Agent modes

An eval measures one way of using an agent. A task declares one with
`mode`; gates, prompts and the guidance that matters differ per mode, so arms
are compared within a mode and there is no score across modes.

| `mode` | Prompt | Success | Where difficulty comes from |
|---|---|---|---|
| `interactive` | a user's feature or fix request, often specific | `strict_pass` plus `mergeable`; small scope drift is tolerable, since a user reviews | breadth of the change, cross-layer and cross-backend work |
| `autonomous` | an issue as filed, `prompt_style: issue` | `mergeable` with no human in the loop, plus `own_tests_fail_on_base`; the scope and performance blockers hold | inferring intent, localizing, not breaking neighbouring code |
| `review` | a diff under review | recall of the human review threads, no blocking finding on a clean diff, decoys not flagged | the team's conventions (§4) |

The same repository can serve all three. Mode-specific guidance (how to
build, how to reproduce and test, what reviewers block on) is an arm in its
own right.

## 3. Performance checks

A change that passes the tests but is much slower is a blocker for a
performance library. An LLM judge can flag a cost problem from the diff;
a measurement confirms it.

> **Open (review on #67):** this section gives `perf` two shapes, an object
> and a command string, and the mergeability proposal had `grade` reporting a
> `perf_ratio` metric. One owner and one shape still have to be chosen; see
> the PR description.

```json
"perf": {
  "target": "//dev/agent_evals/bench:kmeans_csr_host",
  "metric": "objective_ratio",
  "baseline": "oracle",
  "tolerance": 1.5,
  "repeats": 5,
  "rounds": 3,
  "threads": 8,
  "sizes": [{"p": 20000}, {"p": 50000}]
}
```

- **Baseline.** The baseline is the oracle build, run on the same machine in
  the same session, in shuffled order. The oracle is also run twice per round
  as a control, and a control outside its own noise band invalidates the
  round.
- **Metric.** Use a ratio taken within each repetition, not a wall time. On a
  loaded host, wall time of the reference against itself is too noisy to
  grade; a ratio of two timings taken back to back is not.
- **Verdict.** The check fails if the median ratio against the baseline exceeds
  `tolerance` and lies outside the measured noise. With `sizes`, a
  ratio that grows with input size flags a complexity regression.
- **Not a replacement.** The buggy base passes a speed check, so this sits
  alongside the correctness tests.
- **Limits:**
    - Absolute times do not transfer across hardware.
    - Shared CI runners can only grade large effects (2× or more).
    - Each task needs a hand-written benchmark whose inputs expose the cost.
    - Memory blow-ups need a separate measurement.
    - A one-line change to a widely included header rebuilds the library once
      per variant, which is expensive on large C++ repositories.

**Existing benchmark tools.** The framework does not reimplement benchmark
tools; it owns the protocol around them:

- baseline = oracle;
- interleaved order and the control run;
- the noise band and the verdict.

The repository wraps whatever tool fits behind one entry point,
`perf: "<command> --variant {variant} --out {out}"`. The command writes
`repo_perf.v1` (metric name, per-repetition values, unit). Candidates for
that command:

- a Google Benchmark or plain Bazel target;
- `asv` or `pyperf` for Python repositories;
- for scikit-learn-intelex, a scikit-learn_bench (`sklbench`) config
  restricted to the affected estimator.

Release-level suites stay outside the eval: they need a full build per
variant and measure many things a single task does not change.

## 4. Team review rules

A rubric written by a generic "senior maintainer" misses what this team blocks
on. A repository keeps its own rules in `<eval_dir>/review_rules.json`:

```json
{"schema": "repo_review_rules.v1", "cut_date": "2026-03-29",
 "rules": [{"id": "R13", "rule": "revert incidental edits to unrelated files",
            "paths": ["**"], "prs": 21, "reviewers": 7, "written_in": null,
            "status": "confirmed", "evidence": ["<review comment URL>"]}]}
```

- **Source.** Rules are mined from merged-PR review comments and confirmed by
  a maintainer. `written_in: null` marks a rule that is not yet in the
  guidance and belongs in AGENTS.md.
- **Use in rubrics.** A blocker cites a rule as `house:R13`, and `validate`
  checks that the rule exists and is `confirmed`. Only rules seen on at least 3
  PRs from at least 2 reviewers may be blockers.
- **Use in judging and review.** The judge, or a reviewer agent, gets only the
  cited rules and those whose `paths` match the diff, never the whole list.
  The whole list helps on threads a rule covers and hurts on threads none
  covers.
- **Leak guard.** Mining uses only PRs merged before `cut_date`, never a
  replayed or task-source PR. Rules may score only tasks whose base is on or
  after `cut_date`.
- **Refresh.** Re-mine quarterly, keep rule ids stable, and have a maintainer
  review what was added or retired.

**Where the knowledge goes.** The rules are the team's knowledge. They live in
the repository: confirmed rules in `review_rules.json` and, once confirmed,
in AGENTS.md or CONTRIBUTING.md for every agent and person. Dokimi
holds only the method:

- the miner;
- the schema;
- the checks below.

Findings from a given repository go to that repository's PRs and to issues
here, not to documents in this tree.

**Process, as part of preparing a repository for evaluation:**

1. **Mine** review comments with a time cut, excluding task-source and replayed
   PRs.
2. **Triage.** A maintainer marks each rule `confirmed`, `rejected` or
   `candidate`, and adds the `paths` it applies to.
3. **Promote** confirmed rules that are not yet written into the guidance.
   L0 then checks them like any other guidance claim, and the reach probe
   checks that the harness loads the file.
4. **Cite** them from rubric blockers (`house:R13`) and give them to review
   tasks.
5. **Measure** before and after on the same tasks: review recall per class,
   `mergeable`, and the blocker histogram.

**Evaluating the rules themselves:**

- **Predictive coverage.** Use the share of maintainer comments made after
  `cut_date` that some rule matches.
- **Precision.** Measure the maintainer-confirmed share of a sampled set of
  rules, and the share of rule matches on new PRs a maintainer agrees with.
- **Stability.** A rule seen from one reviewer only is that reviewer's
  preference. It stays a candidate until a second reviewer confirms it.
- **Effect and cost.** Record the A/B change in review recall and in
  `mergeable`. Also record the loss on threads no rule covers.

## 5. Open questions

- **Comment verbosity.** Agents add explanatory comments where the
  maintainers' fixes add none, and current guidance does not mention
  comments. A mined rule asks for comments on non-obvious code, so the
  measure is comments that narrate the change instead of explaining the
  code, not comment volume. A `comment_lines` metric relative to the
  reference, plus a judge non-blocker, is the proposed measure; an A/B with a
  written comment policy has not been run.

<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Rule ledger: past failures as replay cases

A guidance rule is a bet: the author expects an agent that reads it to make a
specific mistake less often. The ledger keeps one row per rule with that bet
written down, the cases that stand for the failures the rule was written to
prevent, and the date a person last confirmed it still earns its place.
`repo compare --ledger` then checks the bet on those cases only and gives a
verdict. It does not cut anything. This follows the held-out ablation proposal
in [agentsmd/agents.md#213](https://github.com/agentsmd/agents.md/issues/213).

## What exists

| Piece | Where | What it does |
|---|---|---|
| Replay tasks from review threads | `dokimi/repos/replay/`, `repo tasks replay` ([contract §4.6](repo-evaluation-contract.md#46-replay-tasks-from-review-threads)) | One `dokimi:review_replay` task per merged PR, with the change as reviewed and the reviewers' threads as gold. No GitHub call. |
| Deterministic review grader | `dokimi/repos/graders/review.py` | Matches the agent's findings JSON to gold threads by file and a line window (`window`, default 5). No LLM. |
| Executable tasks and self-check | `repo tasks check` (`dokimi/repos/selfcheck.py`, [contract §4.3](repo-evaluation-contract.md#43-self-check-tasks-check-no-llm)) | A task must fail on the untouched workspace and pass after its oracle, so a case with a broken check cannot reach a paid run. |
| Arms | `dokimi/repos/arms.py` | `guidance:none`, `guidance:raw`, `guidance:rev:`, `patch:` and the rest. |
| Repetitions | `repo run --reps` | Several reps per (task, arm, model). |
| Paired statistics | `repo compare` (`dokimi/repos/compare.py`) | Per-task deltas, sign-flip p, Holm over the gate and score rows of one invocation. |
| Rule vocabulary | [repo-evaluation-agent-modes.md §4](repo-evaluation-agent-modes.md#4-team-review-rules) | Mined `review_rules.json` entries (`R13`, `cut_date`). Method only: no code reads that file yet. |

Before this change nothing tied a rule to the cases it was written from, and
`repo compare` had no notion of a rule.

## From an incident to a held-out case

An incident is a failure a rule should prevent. Three sources are in use or
planned:

1. **A review comment on a merged PR.** Run `repo tasks replay` on the PR's
   threads. The case is `review_replay_pr<N>` and already exists. Its grader is
   recall of the gold threads, so the check is deterministic.
2. **A failed agent run, or a bug a fix PR corrected.** Write an executable task
   in the content repository: a test that fails on the base commit and passes
   with the oracle. `repo tasks check` enforces both halves. No scaffold command
   exists yet; the task is written by hand.
3. **A transcript slice.** A transcript shows what the agent did, not what
   should have happened. It is evidence for case 1 or 2, not a case: a case
   needs a check that does not read the transcript.

Then, per rule:

1. Record the incident as the rule's `source` (a file and anchor, or the
   comment URL) and state the `hypothesis`: what the rule should change in
   an answer on its cases.
2. Put the case ids in `cases`. Use cases the rule was not written from: a
   rule checked on the failure it was mined from only shows it can repeat
   itself.
3. Run `repo tasks check` on the cases, then the rule's arm and its baseline
   with `--reps 3` to `5`.
4. Compare with `repo compare --ledger`.

## Ledger format (`repo_ledger.v1`)

`dokimi/schemas/repo_ledger.v1.json` is the schema. `rules` must not be
empty, and ids must be unique in the file (the loader checks this, since JSON
Schema cannot).

```json
{
  "schema_version": "repo_ledger.v1",
  "policy": {"alpha": 0.05, "review_within_days": 180},
  "rules": [
    {
      "id": "R13",
      "rule": "Revert edits to files the change does not need.",
      "source": "AGENTS.md#commits-and-pull-requests",
      "hypothesis": "With the rule, agents stop touching unrelated files on the replayed PRs.",
      "arm": "guidance:rules",
      "baseline": "guidance:none",
      "cases": ["review_replay_pr3580", "review_replay_pr3659", "fix_df_extratrees_reg"],
      "reviewed": "2026-10-01"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | Stable across edits, `[A-Za-z0-9][A-Za-z0-9._-]*`. |
| `rule`, `source` | Optional text and anchor, printed for the reader. |
| `hypothesis` | Required. The claim the verdict tests, in words. |
| `arm` | The treatment arm that carries the rule, `ARM` or `BATCH/ARM`. |
| `baseline` | Optional. Overrides the `--baseline` of the compare for this rule. |
| `cases` | Task ids, at least one, unique. Ids with no finished cell are listed as missing. |
| `reviewed` | `YYYY-MM-DD`, the last date a person confirmed the rule. |
| `policy.alpha` | Default 0.05. |
| `policy.review_within_days` | Default 180. An older `reviewed` gets a warning. |

## Verdicts

For each rule and model, `repo compare --ledger` runs the ordinary paired
comparison of the rule's arm against its baseline over the rule's linked cases
(`repo compare` itself, so the pairing, headroom and provenance warnings are
the same). The p values of all rule results are Holm-corrected together. The
rules' family is separate from the family of the comparison's own rows.

| Verdict | When | Meaning |
|---|---|---|
| `keep` | Mean delta above zero and Holm p below alpha | The arm beats its baseline on the linked cases. |
| `review` | Mean delta below zero and Holm p below alpha | The baseline beats the arm there. A person decides whether to fix or cut the rule. |
| `insufficient-evidence` | Anything else | Not measured, mixed kinds, every case tied, no significant difference, or too few changed cases for any outcome to reach alpha. The reason says which. |

A review older than `review_within_days` adds a warning and leaves the verdict
alone. "No significant difference" is not a verdict to cut: the rule is not
shown to help, and whether that is enough to cut it is a decision for a person.

## CLI

```bash
python cli.py repo compare repo-runs/replay --baseline guidance:none --ledger repo-ledger.json
python cli.py repo compare repo-runs/replay --baseline guidance:none --ledger repo-ledger.json --json
```

The text output keeps the ordinary comparison and adds a `rule ledger` section.
A ledger that does not validate, or a default `--baseline` the batches do not have,
exits 2 with a `✗` line naming the file (and the rule, where the error is the rule's).
A rule whose own `baseline` or `arm` has no cell among its cases is `not measured` for
that rule, and the command still exits 0. The
ordinary comparison's flags (`--arm`, `--task`, `--metric`, ...) do not change the
ledger, which uses the rules' own cases and arms.

## Limits

- A rule's arm carries every rule that is in it. A verdict on one rule compares
  the whole arm with its baseline, so it confounds the rule with its neighbours.
  A per-rule claim needs an arm that differs by that rule alone (a `patch:`
  arm that removes one rule, say). The ledger does not build those arms.
- Few cases give few verdicts. With fewer than six cases whose delta is not zero,
  no outcome reaches p below 0.05, and the reason says so.
- Gate and score cases do not mix in one rule.

## Not built

- A scaffold that turns a failed run directory, or a description and a failing
  check, into a task skeleton. The executable-task format needs an oracle and a
  verifier, so a skeleton would still need hand work.
- A check that a rule's cases are held out from the PRs it was mined from. The
  [mining leak guard](repo-evaluation-agent-modes.md#4-team-review-rules)
  (`cut_date`) is the method; nothing enforces it for a ledger yet.
- Reading the mined `review_rules.json` into the ledger.

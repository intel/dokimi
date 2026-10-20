<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Flow map

How the [journeys](journeys.md) connect. Solid arrows are transitions a user can
make with today's commands; dashed arrows labelled *proposed* need a capability
that does not exist yet. Nodes name what the user gets, not a command: the
commands for each step are in the journey's own section.

None of these is a fixed sequence. A user can enter at any node that matches
what they already hold.

## Assessment and improvement

```mermaid
flowchart LR
    start([Subject: skill, repo, guidance]) --> J1[J1 assess: static checks, unknowns]
    J1 -. proposed: costed next-step plan .-> plan[Next evaluation plan]
    J1 --> J4[J4 guidance: correctness, reach]
    J1 --> J2[J2 diagnose skill: routing, novelty]
    J1 --> J3[J3 repository: scan, tasks check, runs]
    J2 --> change[Change the subject]
    J4 --> change
    J3 --> change
    change --> remeasure[Re-measure on same probes or tasks]
    remeasure --> decide{Effect?}
    decide -->|improved| keep[Keep, protect in J12]
    decide -->|none or worse| reject[Reject with numbers: a valid outcome]
    decide -->|unclear| J11[J11 explain, next experiment]
    J2 -. proposed: join novelty to task effect .-> remeasure
    J4 -. proposed: adherence .-> change
```

## Eval authoring

```mermaid
flowchart LR
    need([A journey needs tasks]) --> J7[J7 evals]
    J7 --> validate[Validate manifest]
    validate --> selfcheck[Self-check: null fails, oracle passes, isolation]
    selfcheck -->|broken grader| fix[Fix grader] --> selfcheck
    selfcheck -->|ok| ready[Tasks ready for agent runs]
    J7 --> judge[Judge validity for judged evals]
    J7 -. proposed: draft tasks from code, docs, issues, traces .-> drafts[Task hypotheses to confirm]
    drafts -.-> selfcheck
    ready -. proposed: negative controls, coverage gaps .-> gaps[Coverage report]
    J5([J5 third-party skill]) --> J6[J6 my use case]
    J6 --> J7
```

## Model and cost selection

```mermaid
flowchart LR
    tasks([Task or question set, pinned]) --> J8[J8 run across models]
    J8 --> compare[Compare with intervals]
    compare -->|set mismatch| warn[Warn, common subset only]
    compare --> feasible{Meets requirements?}
    feasible -. proposed: threshold verdict .-> list[Feasible models]
    list --> J9[J9 cost per acceptable outcome]
    J9 -. proposed: retries and failures priced .-> pick[Cheapest suitable setup]
```

## Recurring checks

```mermaid
flowchart LR
    pr([PR touches subject]) --> J12[J12 budgeted check]
    J12 --> base[Compare with saved baseline]
    base -->|regression| fail[Fail with evidence]
    base -->|no regression| pass[Pass with evidence]
    base -->|budget stop or inconclusive| inc[Inconclusive: policy decides, never pass]
    J12 -. proposed: configurable policy across check kinds .-> policy[Policy]
    time([New model, harness, time]) --> J13[J13 reassess]
    J13 --> rerun[Re-run earlier experiment, one condition changed]
    J13 -. proposed: replay from recorded settings .-> rerun
    rerun --> verdict{Still helps?}
    verdict --> keepit[Keep / adapt / shorten / remove]
```

## Direct entry

```mermaid
flowchart LR
    ask([One specific question]) --> J14[J14 one operation]
    J14 --> estimate[Estimate cost]
    estimate --> run[Run with explicit inputs and cap]
    run --> out[Structured output, settings, spend, limits]
    out --> rerender[Re-render saved output, no model calls]
    run -->|cap reached| stop[Stopped at cap, no result written]
    J14 --> novelty[Novelty]
    J14 --> reach[Reach]
    J14 --> routing[Routing]
    J14 --> context[Context trace]
    J14 --> judge[Judge calibration]
    out -.-> J2[Feeds J2, J5, J13]
```

## From a solved chat to a skill

```mermaid
flowchart LR
    chat([Problem solved in a chat, no skill]) --> J15[J15 make a skill]
    J15 -. proposed: tasks from the transcript, source task kept apart .-> tasks[Source task + held-out tasks]
    tasks --> J7[J7 self-check graders]
    J15 -. proposed: draft the skill from the transcript .-> draft[Draft skill]
    draft --> novelty[Novelty and routing of the draft]
    novelty -->|adds nothing| stop[No skill: a valid outcome]
    novelty --> arms[Arms: no skill, user's skill, skill-creator's skill]
    J7 --> arms
    arms --> pick{Which helps?}
    pick -->|user's or merged| J2[J2 improve, then J12 protect]
    pick -->|generated as good| gen[Keep the generated skill]
    pick -->|neither| stop
```

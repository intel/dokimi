<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Difficulty & Level Rubric (Performance Skills)

This rubric makes difficulty a **measured, reproducible axis** rather than an
intuition tag, and separates it from two further orthogonal axes — **level**
(how deep into the performance workflow the question reaches) and **trigger**
(what kind of input starts the work). All three apply to awareness questions
(`<questions-root>/*_golden.json`) and to executable tasks (the task directories
under `--tasks-root`, via `[metadata] difficulty` / `level` in `task.toml`).

Why multiple axes: a question can be *deep in the workflow but easy* ("produce a
report" when the evidence is unambiguous) or *shallow but hard* (a single
counter reading that is a trap). It can also be *counter-first* (a profile is
already in hand) or *source-first* (the agent is reading or writing code and a
pattern is the trigger, before any profile exists). Collapsing these into one
`easy/medium/hard` tag hides where a skill actually helps. Scoring lift on the
full `level × difficulty × trigger` space is the goal.

## What the skill is actually for (two use cases)

The rubric was written against a performance-patterns skill — evaluation content,
kept in a content repository and passed in as `skill:<path>` — whose own text
mandates two modes of use. Read the skill you are measuring the same way, and
cover both modes, not just incident-response triage:

1. **Fast-from-the-start (proactive).** When an agent writes new performance-
   sensitive code, the skill applies optimized patterns *without being asked*.
   The skill that motivated this rubric states its suggestions "apply even when
   optimization is not explicitly requested," and that when "generating new code
   … always use one of the optimized patterns."
2. **Code-first (code drives the change).** For a code-based performance issue,
   the code is the primary evidence; high-level perf data serves the code, not
   the reverse. The same skill says: "When reviewing code, flag the simple
   pattern as a high-priority optimization note" — a source pattern is a
   first-class trigger, no profile required to act.

A skill that mandates neither mode needs neither set of items; the axes below are
general, the two use cases are a property of the artifact under test.

## Axis 1 — `level` (workflow depth)

| Level | The agent must… | Output shape | Does NOT include |
| --- | --- | --- | --- |
| `triage` | Interpret the available evidence — counters/symptoms **or source patterns** — and **route**: either to the next evidence-collection step, or to a known pattern when source already shows it. | A regime/pattern call + the next workflow/command. | Proposing a concrete code fix. |
| `diagnosis` | From evidence, **classify the specific pattern**, propose a fix *shape*, and state how to verify it. | Pattern name + fix shape + verification expectation, scoped to one bottleneck. | Rebuilding, rerunning, or producing a full deliverable. |
| `end_to_end` | Orchestrate the **full lifecycle or produce a complete deliverable**: baseline → change → rebuild → rerun → compare → report; multi-step iteration loops; new production code with dispatch/fallback/tests; or evaluation/meta design. | A plan or artifact spanning generate → build → verify → report. | (terminal level) |

Note on `triage`: the entry point is not only a `perf` excerpt. An agent that is
reading or writing code can triage from **source** — a serial accumulator in a
hot loop, a scalar elementwise op on an AVX2 host, an SSE-only path with no AVX
sibling — and route straight to the matching pattern. See the trigger axis below.

## Axis 0 — `trigger` (what starts the work)

Orthogonal to level and difficulty. Tag each item with the kind of input that
begins the task, recorded as `metadata.trigger`.

Not to be confused with *trigger sets* (`catalog trigger-set`, see
[Skill selection](evaluating-treatments.md#skill-selection-does-the-agent-load-the-right-skill)).
Those label which skill a message should load and measure selection. This axis tags
what input starts the work once the skill is in play.

| Trigger | Input the agent starts from | Correct first move |
| --- | --- | --- |
| `counter` | A `perf stat`/`report`/`c2c` excerpt or symptom; profile already collected. | Interpret signals; route to the next workflow or pattern. |
| `source` | Source code the agent is reading/reviewing; no profile yet. | Recognize the source pattern; act now if the skill flags it high-priority, else state what profile evidence to gather first. |
| `new_code` | A request to write new performance-sensitive code. | Generate using the optimized pattern from the start (dispatch/fallback/tests as the bar requires). |

**Ordering principle (hard rule).** Code drives the change. A code edit must be
justified by source the agent can read and modify — aggregate perf data alone
(a counter table or `perf report` excerpt with no source/build access) is
**not** sufficient license to rewrite code. When only perf data is in hand and
source is unavailable, the correct answer is to obtain source/build access
first and to bound the claims to what the data supports. Profiling serves the
code; it does not substitute for it.

## Axis 2 — `difficulty` (cognitive load)

| Tier | Definition | Discriminator |
| --- | --- | --- |
| `easy` | One clear signal maps to one canonical next step. | A correct answer needs no ruling-out of alternatives. |
| `medium` | Competing or ambiguous signals; the agent must rule out plausible distractors before committing. | At least one tempting wrong answer must be rejected with evidence. |
| `hard` | Multi-step evidence chains, ABI/semantic/FP-order risk, heterogeneous-hardware constraints, iteration/unmasking, or a **negative** case where the right answer is "do not optimize yet / gather more evidence." | Correctness depends on safety reasoning or on *not* acting, not just pattern recall. |

`difficulty` is independent of `level`: assign each separately, then place the
item in the grid.

## Coverage grid (target ≥4–6 items per cell)

A minimum-useful set (60–80 questions) should fill all nine `level × difficulty`
cells rather than pile onto the `easy⇒triage … hard⇒end_to_end` diagonal. The two
cells that go missing first are the off-diagonal corners — `triage × hard` (a
shallow question that is a trap) and `end_to_end × easy` (a full deliverable from
unambiguous evidence).

Question sets are evaluation content and are not committed here, so count the grid
of the set you are about to run:

```bash
python - <<'PY'
import collections, json
qs = json.load(open("<questions-root>/onetbb_golden.json"))["questions"]
grid = collections.Counter((q.get("level"), q.get("difficulty")) for q in qs)
for cell, n in sorted(grid.items(), key=lambda kv: str(kv[0])):
    print(cell, n)
PY
```

A balanced `level × difficulty` grid still leaves the **trigger** axis skewed: sets
grown from profiling transcripts are mostly `counter`-triggered (a profile is
already in hand), under-representing the two use cases above. Target `source` and
`new_code` triggers as the set grows:

- `source × triage`: the agent is reading code with no profile; route to a
  pattern the skill flags high-priority (e.g. serial accumulator) vs. state what
  to profile first.
- `source/new_code × end_to_end (easy/medium)`: apply an optimized pattern
  inline while writing or reviewing, with a cheap before/after check — the
  proactive, fast-from-the-start case.
- `counter × negative_case`: perf data with no source access; the correct
  answer withholds a code change until source is obtained (ordering principle).

## Negative & adversarial cases

A `metadata.negative_case: true` flag marks items whose correct answer is to
**withhold a fix** (insufficient evidence, unsafe ABI/FP change, non-editable
artifact, wrong layer). These are the highest-signal items for distinguishing a
reasoning skill from keyword matching, and should appear across `medium` and
`hard` at every level.

## Applying the rubric

1. Assign `level`, `difficulty`, and `trigger` independently using the tables above.
2. Record `level` as a top-level question field; keep `difficulty` as today;
   record `trigger` under `metadata.trigger`.
3. For tasks, mirror the tags in `task.toml` `[metadata]` (`difficulty` already
   exists; add `level` and `trigger`).
4. When generating new items, target under-filled `trigger` cells (`source`,
   `new_code`) and tag `negative_case` where applicable — including the
   perf-data-without-source case the ordering principle covers.
5. Report evaluation lift sliced by `level × difficulty` and, where it
   discriminates use cases, by `trigger`; never blend the awareness grid with
   executable task pass rate.

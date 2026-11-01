---
depends_on:
  - dokimi/dokimi/novelty/**
  - dokimi/dokimi/metrics/bundle_coverage.py
  - dokimi/dokimi/commands/novelty.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Measuring a skill you are writing: the whole path

One sitting, one artifact, one model. Each step says what it costs and what it refuses to
claim. [skill-novelty.md](skill-novelty.md) is the reference for *why* each number means
what it means; this is the order to run them in.

The three free steps come first on purpose. Two of them can tell you not to spend anything
at all.

| step | what it answers | cost |
|---|---|---|
| 1. routing | will it ever be picked? | free |
| 2. estimate | what will measuring cost? | free |
| 3. triage | is there anything here? | ~100 calls |
| 4. extract once | the answer key, reusable | ~1 call/atom (125 at the 120 cap) |
| 5. probe per model | what does *this* model lack? | ~4 calls/probe |
| 6. read the view | which sentence to edit | free |
| 7. shorten | what can go | 0–1 call per atom per candidate |
| 8. re-measure | did the edit keep the knowledge? | step 5 again |

## 1. Can it be selected at all? (free)

If the artifact is never picked, content work is unrecoverable spend. Write 5–10 requests
**in the words a user would use**, in a file, without looking at the skill's own headings:

```bash
python cli.py novelty routing --artifact skill:skills/my-skill \
    --catalog skills/ \
    --queries queries.json
```

The competitors are the skills a harness lists next to this one: `--catalog <dir>` takes
every other skill the catalog lists (a `template/` copy and vendored or VCS directories
such as `.git` are left out, and a byte-identical copy under another path counts once), and
`--competitors` adds or names them one by one. With no real siblings at hand, skip this step and say so; a field of
competitors made up for the run measures nothing.

Read `recall@1` against the competitors, not alone. Measured on the study corpus, every
`dpnp-*` skill scored 0.0 against its siblings, and `linux-perf` scored 0.0 recall@1 on
five independently written requests — losing three of them to an `xpu-*` skill phrased the
way a user phrases the problem. If that is your result, **fix the description before anything
else**; `novelty analyze` holds the frontmatter fixed and will not see this.

Add `--model <id>` for the model arm. Treat it as an upper bound: a harness whose own skill
listing truncates descriptions selects on less text than the command shows.

## 2. What will the measurement cost? (free)

```bash
python cli.py novelty analyze --artifact skill:skills/my-skill --tier study --estimate-only \
    --include-resources
```

Pass the same `--include-resources` (and `--tier`) you will run with: the bill covers only
the files the flags select, and a thin `SKILL.md` over `references/` is priced many times
lower without it. It prints the projected model-call bill and exits before building a client. The
bill is `probes × arms × (answer + grade)` plus the retention oracle, which escalates only its
ambiguous band. The output carries `expected_calls`, the point figure, with `by_role` splitting
it into extractor, answerer and judge calls; `total_calls` and
`total_calls_if_every_pair_escalates` are the two ends. `expected_usd` prices `by_role` at list
prices, and is `null` when a model has no price. The probe term was validated to within 1–3% of
counted calls at nine (skill, tier) points, on content that did not escalate.

`expected_usd` is a projection, not a ceiling (held-out runs ran up to 33% low): a call's price
follows the artifact's length and the models. Pass `--max-usd <n>` to every step that calls a
model: the run stops once its calls across all roles have cost that much (calls already in
flight can overshoot it), and a call with no known price stops it too,
since the cap cannot see that spend. One measured pair, a 19k-token skill: triage 103 calls,
about $1.80; `study` 1,901 calls, about $14.50.

## 3. Triage: is there anything in here? (~100 calls)

```bash
python cli.py novelty analyze --artifact skill:skills/my-skill \
    --tier triage --model <model under measurement> --judge-model <a different model>
```

Read in this order: `harmful_conflicting` (the only category that can make an artifact
worse than nothing — unmeasured at `triage`, so a clean triage result does not clear it),
then `added`, then the span view. The tier's forfeits are printed before the run and
written into the artifact; a triage `added` is a **sign**, not a figure, and is not
comparable with another artifact's.

If the skill is a thin `SKILL.md` over a `references/` tree, add `--include-resources`.
Without it the run measures the entry point and prints the share it is skipping — in the
study corpus that share was 0.72 of all words. At `triage` the 25-atom cap probes atoms spread
over every selected file in file order, so `references/` gets its share by atom count; 25 atoms
over several files leave each one few, so for a figure per file use `--tier standard` or higher.

## 4–5. Extract once, probe per model

Atoms are model-independent, so the expensive extraction amortizes:

```bash
python cli.py novelty atoms --artifact skill:skills/my-skill \
    --extract-model <not the model under measurement> --include-resources --out atoms.json

python cli.py novelty probe --artifact skill:skills/my-skill --atoms atoms.json \
    --model model-a --judge-model <different> --out model-a.json
python cli.py novelty probe --artifact skill:skills/my-skill --atoms atoms.json \
    --model model-b --judge-model <different> --out model-b.json
```

The extractor must not be the model under measurement, or the model writes its own answer
key — `probe` warns when it detects that. Without `--extract-model`/`--model`, `atoms` (and
the `shorten` adjudicator) use the default judge, `anthropic/claude-sonnet-5-5`, the same
model `analyze` extracts with; an extractor from another provider needs `--provider`. `probe`
and `analyze` measure `openai/gpt-6.1-sol` unless given `--model`. `atoms.json` records which
files it came from and `probe` rebuilds exactly that set, refusing if the artifact's files changed since.

With three or more models, check which probes measured anything:

```bash
python cli.py novelty items --probes model-a.json model-b.json model-c.json
```

An atom every model knows separates nothing (true, but not worth probe budget); a
**negative** discrimination is the defect to look at.

## 6. Read the view, not the number

`analyze` writes `annotated.html`: the artifact's own text, coloured per span — green
carries `added`, red carries harmful or degraded, hatched was never read by extraction (not
dead weight), amber is protected (negation, version gate, ordering, threshold), dim carries
no atom at all, grey carries `redundant` knowledge or atoms judged inert, blue carries
knowledge the model got wrong with and without the artifact (`still_missing`), and plain
carries atoms with nothing measured or judged against them (`unscored`). The full rules are
in [skill-novelty.md](skill-novelty.md) ("Span attribution"). Two derived
lists are where edits start: **dead weight** (dim spans by token cost) and **sole carriers**
(spans that are the only source of something).

With `--include-resources`, `bundle.by_source` says which *file* carried the knowledge. A
reference whose atoms are all `redundant` is a file the model already knows; one full of
`added` may belong where the agent will actually read it; one marked `not read` was lost to
extraction, not empty.

## 7. Shorten, with the protection axis pinned

```bash
python cli.py novelty shorten --artifact skill:skills/my-skill --atoms atoms.json \
    --budgets 0.75 0.5 --lexical-only --out short/SKILL.md
```

`--lexical-only` runs the retention oracle without an LLM adjudicator: cheap, reproducible,
and blind to contradictions, so `knowledge` is a floor. Drop it (and pass `--model`) when
you want the adjudicated figure, and expect up to one call per atom per candidate.

A candidate that drops a protected span is reported but never offered: it is not smaller,
it is different. Shortening rewrites one file, so with a bundle-derived atom set it scores
the entry point only and says so.

## 8. Re-measure the edit

Re-run step 5 against the shortened artifact with the **same** `atoms.json` — same answer
key, so the two runs are comparable — and compare `added` between the two `probe` outputs.

One run per side is not enough to read that difference. Paraphrase and reversal wordings
are generated afresh on every `probe` run (`--cache-dir` caches only retention
adjudication), so two identical runs differ too: on one skill, with the same atoms and the
same models, `added` came out 0.9333 in one run and 0.8667 in the other, and the fidelity
check dropped 3 of 15 reversals in one and 14 of 15 in the other. That spread was the same
size as the change the shortening edit produced on the same skill. Run each side at least
twice, and read the edit as real only when the gap between the sides is larger than the
spread within each side. `--no-paraphrase --no-reversal` removes the wording redraw as a
source of spread (answers still vary), at the cost of the surface-wording and inversion
controls.

The protected-span count is not in `probe` output: it lives in `shorten`'s table and in
`analyze`'s span block, so re-run `analyze` if you want to compare that too.
If the atoms came from a bundle, put the shortened `SKILL.md` into a copy of the skill
directory rather than probing `short/` on its own: `probe` refuses a file set that differs
from the one recorded in `atoms.json`.
Two cells compare only when their `run_config` blocks match, which includes the tier, the
probe kinds, `include_resources` and the prompt digest.

## What this path does not tell you

- Whether the agent *loads* the bundled file it would need: that is the agentic track
  (`skill-agent:`, `tasks run`), not this one.
- Whether the artifact helps on real work. `added` is knowledge delivery; ranking 13
  artifacts inside one model against the measured task delta gave **−0.446**, with
  per-model correlations disagreeing in sign. Use
  [evaluating-treatments.md](evaluating-treatments.md) for the outcome question.
- Anything comparable across artifacts. Every number here is a property of one
  `(artifact, model)` pair at one probe budget.

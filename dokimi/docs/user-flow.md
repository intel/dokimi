<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Benchmark user flow

Canonical flow: one benchmark set, one target, two arms.

The benchmark is not split into separate user-facing benchmark types. Static checks, LLM answer scoring, treatment arms, and executable tasks are different evidence layers over the same question/task set. Start with the unified flow below; use low-level commands only when debugging a layer.

## 1. Pick target

A target is a registered product/library key from `products.yaml`. The compatibility flag is `--library`, but `--target` is accepted for the same key.

```bash
python cli.py library list
python cli.py library show onetbb
```

## 2. Preflight

Preflight resolves config and fails before expensive API calls.

```bash
python cli.py benchmark preflight --target onetbb
```

It checks:

- answer and judge providers/models
- API key env vars
- resolved doc/context source
- writable output directory
- judge/answer independence warning
- optional `--questions-from` path and JSON shape

It also reports the resolved fixed retrieval policy: semantic-only, `top_k=3`.

## 3. Run canonical benchmark

Question sets live in a separate evaluation-content checkout, so name its
`questions/` directory. The run picks up `<target>_golden.json` from there:

```bash
python cli.py benchmark run --target onetbb \
  --questions-root <content-checkout>/questions
```

Without `--questions-root` (or `--custom-questions <file>`) the run **generates**
questions instead of reading a curated set, and logs a warning saying so. That is
fine for a first look, but two runs then answer different questions and cannot be
compared. See [external-content.md](external-content.md).

Defaults:

- output: `results/<target>_final/`
- answer model: `openai/gpt-6.1-sol`
- judge model: `anthropic/claude-sonnet-5-5`
  (changed 2026-10-13: not comparable with older baselines; re-baseline, see
  [Default models](benchmarking-and-comparison.md#default-models))
- retrieval: semantic-only, `top_k=3`
- comparison: `without_docs` vs `with_docs`

Artifacts:

```text
results/<target>_final/
  personas/<target>.json
  questions/<target>.json
  answers/<target>.json
  eval/<target>.json
  reports/<target>.md
```

## 4. Reuse same question set for fair comparison

```bash
python cli.py benchmark run --target onetbb --questions-root <content-checkout>/questions --output-dir results/onetbb_seed
python cli.py benchmark run --target onetbb --questions-from results/onetbb_seed --model gpt-6-astra
python cli.py benchmark run --target onetbb --questions-from results/onetbb_seed --model claude-opus-5-5 --provider anthropic
```

Never compare models on different generated questions. `--questions-from` reuses
the set an earlier run wrote; `--questions-root` reads a curated set that lives in
the content checkout. Either one pins the questions — a bare `benchmark run` does
not.

## 5. Add more evidence layers only when needed

Use same target/question/task set, then add:

- static documentation checks for structure/readability/example gates
- treatment arms for skills/MCP/profile comparisons
- executable tasks for compile/run/performance proof
- dashboard aggregation for cross-target tracking

This keeps one benchmark set and prevents false confidence from mixing unrelated question sets.

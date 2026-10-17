<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# LLM evaluation pipeline — quickstart

End-to-end recipe for the canonical benchmark flow: target → preflight → fixed question/task set → `without_docs` vs `with_docs` answer generation → judge scoring → report.

For the static documentation benchmark (`coverage`, `freshness`, `readability`,
`example_pass_rate`), see the top-level [README](../README.md#static-benchmark-metrics).


## Canonical flow

Use this path first. Manual persona/question/answer/eval commands below are advanced debugging tools.

```bash
python cli.py benchmark preflight --target onetbb
python cli.py benchmark run --target onetbb
```

Defaults: `results/<target>_final/`, `openai/gpt-6.1-sol` answer model, `anthropic/claude-sonnet-5-5` judge, semantic-only retrieval with `top_k=3`, and `without_docs` vs `with_docs` arms.

For fair model/context comparisons, seed once and reuse questions:

```bash
python cli.py benchmark run --target onetbb --output-dir results/onetbb_seed
python cli.py benchmark run --target onetbb --questions-from results/onetbb_seed --model gpt-6-astra
```

See [user-flow.md](user-flow.md) for target/generalization rules.

## Prerequisites

### 1. API keys

```bash
# OpenAI (question generation, answering)
export OPENAI_API_KEY="sk-..."

# Anthropic (judging — separate provider to reduce self-evaluation bias)
export ANTHROPIC_API_KEY="sk-ant-..."

# GitHub (optional — raises persona-discovery rate limit from 60 to 5000/hour)
export GITHUB_TOKEN="ghp_..."
```

### 2. Dependencies

```bash
pip install -e ".[dev]"          # package + dev tooling (recommended)
# or, dependencies only:
pip install -r requirements.txt
```

This installs the `dokimi` console entry point. The examples below use
`python cli.py …`, which remains supported; `dokimi …` and
`python -m dokimi …` are equivalent.

The pipeline degrades gracefully if optional deps are missing — see
[Troubleshooting](#troubleshooting).

## Pipeline

All commands run from the repo root and use `python cli.py …`. Use
`--help` on any subcommand for the full option list.

### Step 1 — Discover personas

```bash
python cli.py personas discover \
  --product oneTBB \
  --repo uxlfoundation/oneTBB \
  --count 5 \
  --save-analysis
```

Reads the GitHub repo (README, issues, API patterns), generates 5–8 user
personas via LLM, and writes `personas/oneTBB.json`. Each persona has `id`,
`name`, `description`, `skill_level`, `concerns`, and `typical_questions`.

Review and approve:

```bash
$EDITOR personas/oneTBB.json
python cli.py personas approve --file personas/oneTBB.json
```

### Step 2 — Generate questions

```bash
python cli.py questions generate \
  --product oneTBB \
  --personas personas/oneTBB.json \
  --count 2 \
  --validate \
  --model gpt-6.1-sol
```

Extracts seed topics from the docs, generates questions per persona × topic,
LLM-validates them (threshold 60/100), and deduplicates via embeddings
(similarity > 0.85). Expected output: ~30–50 unique questions in
`questions/oneTBB.json`.

### Step 3 — Generate context-arm and baseline answers

```bash
python cli.py answers generate \
  --product oneTBB \
  --questions questions/oneTBB.json \
  --output answers/oneTBB.json \
  --model gpt-6.1-sol \
  --provider openai \
  --top-k 3 \
  --rerank-threshold 0.3 \
  --debug-retrieval
```

For each question, two answers are produced: context arm (retrieved via the
configured `--doc-source`, default Context7) and baseline (model knowledge
only). Use `--doc-source local:<path>` or `--doc-source url:<url>` for
alternative sources — see [adding-doc-source.md](adding-doc-source.md).

### Step 4 — Score answers

```bash
python cli.py eval score \
  --product oneTBB \
  --answers answers/oneTBB.json \
  --output eval/oneTBB.json \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic
```

LLM-as-judge scores each answer on five 0–100 dimensions
(correctness, completeness, specificity, code_quality, actionability), then
computes an aggregate and the context-arm minus baseline delta.

Multi-judge panel:

```bash
python cli.py eval panel-score \
  --product oneTBB \
  --answers answers/oneTBB.json \
  --output eval/oneTBB.panel.json
```

### Step 5 — Generate the analysis report

```bash
python cli.py report generate \
  --product oneTBB \
  --eval eval/oneTBB.json \
  --questions questions/oneTBB.json \
  --output reports/oneTBB.md \
  --format markdown
```

`reports/oneTBB.md` contains summary stats (min/max/avg context-arm/baseline/delta),
top-N best and worst answers, top improvements and degradations, topic and
persona clustering, and recommendations. The `reports/` directory is
git-ignored.

### Step 6 — Dashboard (optional)

```bash
python cli.py dashboard generate
```

Aggregates per-library results from `eval/*.json` into `DASHBOARD.md` /
`dashboard.json` for cross-library comparison.

## Full run (single command)

```bash
PRODUCT=oneTBB
REPO=uxlfoundation/oneTBB

python cli.py personas discover --product $PRODUCT --repo $REPO --count 5 && \
python cli.py personas approve --file personas/$PRODUCT.json && \
python cli.py questions generate --product $PRODUCT --personas personas/$PRODUCT.json --validate && \
python cli.py answers generate --product $PRODUCT --questions questions/$PRODUCT.json --model gpt-6.1-sol && \
python cli.py eval score --product $PRODUCT --answers answers/$PRODUCT.json --judge-model claude-sonnet-5-5 --judge-provider anthropic && \
python cli.py report generate --product $PRODUCT --eval eval/$PRODUCT.json --questions questions/$PRODUCT.json --output reports/$PRODUCT.md
```

Advanced manual pipeline wrapper:

```bash
python cli.py evaluate --product $PRODUCT --repo $REPO
```

## Output layout

| File | Content |
| --- | --- |
| `personas/<product>.json` | 5–8 user personas |
| `questions/<product>.json` | Validated, deduped questions |
| `answers/<product>.json` | Context-arm/baseline answer pairs + retrieval metadata + token counts |
| `eval/<product>.json` | Per-question scores (5 dimensions) + aggregates + deltas |
| `reports/<product>.md` | Human-readable analysis (git-ignored) |
| `DASHBOARD.md` | Cross-library summary |

Curated *inputs* live under `data/` (`data/skills/fastcontext/`,
`data/agent_profiles/`, `data/explore_tasks/`) — the fixtures the framework's own
examples and tests need. Question sets, product skills and personas are
evaluation content, kept in a separate content repository and passed in by path
(`--questions <path>`, `--questions-root <dir>`, `skill:<path>`) —
[external-content.md](external-content.md) walks through pointing the framework
at such a checkout.
Everything the pipeline produces — answers,
eval scores, benchmark snapshots, reports — stays under the git-ignored
`answers/`, `eval/`, `baselines/`, `reports/` and `results/` directories. See
[`data/README.md`](../data/README.md).

## Cost estimate

For 35 questions on the defaults, `gpt-6.1-sol` ($2/$10 per MTok input/output)
answering and generating and `claude-sonnet-5-5` ($2/$10) judging:

| Step | Calls | Approx. cost |
| --- | --- | --- |
| Persona discovery | a few | $0.20 |
| Question generation | ~35 | $0.20 |
| Answer generation | ~70 (context arm + baseline) | $2 |
| Judge scoring | ~70 | $5 |
| **Total** | | **~$7** |

These are the earlier measured figures (on `gpt-5.5` + `claude-sonnet-4-5`, ~$13)
scaled by list price, not a new measurement. The defaults changed on 2026-10-13,
so results from before are not comparable with older baselines; re-baseline — see
[Default models](benchmarking-and-comparison.md#default-models).

Costs scale roughly linearly with question count. Token counts are recorded
per answer in `answers/<product>.json`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| LLM calls fail / wrong provider | All providers go through LiteLLM (`dokimi/llm.py`); set the matching `*_API_KEY` and pass `--provider`/`--model`. LangChain is used only by `eval ragas`, which wraps its judge in `ChatOpenAI`/`ChatAnthropic` (`langchain-openai` comes with ragas; `langchain-anthropic` is a direct dependency). |
| `No module named numpy` | Pipeline falls back to exact-text dedup; install numpy for embedding-based dedup |
| `GitHub client not available` | Pipeline uses `gh` CLI fallback; `pip install PyGithub` to silence |
| Context7 timeout | Raise `context7.timeout` in `dokimi/config/products.yaml` (or in a config/products.yaml of your own in the working directory) |
| Rate limiting | OpenAI 10K TPM default, Anthropic 10K TPM default; lower `--concurrency` |

## Where to look next

- [adding-doc-source.md](adding-doc-source.md) — point the pipeline at local
  files, a single URL, or a custom MCP-style client.
- [architecture.md](architecture.md) — high-level data flow and module
  boundaries.
- [contributing-metric.md](contributing-metric.md) — add a new static
  documentation metric.
- [contributing-terminal-bench-task.md](contributing-terminal-bench-task.md)
  — add an executable oneTBB / oneAPI task.
- [methodology.md](methodology.md) — model roles, question types, judging, and
  trust checks.

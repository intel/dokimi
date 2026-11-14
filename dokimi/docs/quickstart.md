<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Q&A pipeline: from target to report

End-to-end recipe for the canonical benchmark flow: target → preflight → fixed question/task set → `without_docs` vs `with_docs` answer generation → judge scoring → report.

For the static documentation benchmark (`coverage`, `freshness`, `readability`,
`example_pass_rate`), see the top-level [README](../README.md#static-benchmark-metrics).


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
pip install dokimi               # released package
# or, from a source checkout:
pip install -e ".[dev]"          # package + dev tooling
# or, dependencies only:
pip install -r requirements.txt
```

The package install provides the `dokimi` console entry point used below.
`python -m dokimi …` is equivalent. The dependencies-only route installs no `dokimi` command: run `python -m dokimi …` from the inner `dokimi/` package directory in its place. Source-install and requirements-file commands run from the inner `dokimi/` package directory; see [installation](start/README.md#install).

The pipeline degrades gracefully if optional deps are missing — see
[Troubleshooting](#troubleshooting).

## Content setup

`onetbb` ships in the package's product registry, and `benchmark run` discovers personas and generates its own questions, so the canonical flow below needs only the credentials above. For a fixed comparison, pass `--questions-root <dir>` with an `onetbb_golden.json` in it; that set then replaces the generated questions. [External content](external-content.md) covers your own targets and question sets. For a first skill or repository check, use [Start here](start/README.md).

## Canonical flow

Use this path after installation, credentials and content setup. Manual persona/question/answer/eval commands below are advanced debugging tools.

```bash
dokimi benchmark preflight --target onetbb
dokimi benchmark run --target onetbb
```

Defaults: `results/<target>_final/`, `openai/gpt-6.1-sol` answer model, `anthropic/claude-sonnet-5-5` judge, semantic-only retrieval with `top_k=3`, and `without_docs` vs `with_docs` arms.

For fair model/context comparisons, seed once and reuse questions:

```bash
dokimi benchmark run --target onetbb --out-dir results/onetbb_seed
dokimi benchmark run --target onetbb --questions-from results/onetbb_seed --model gpt-6-astra
```

On the 0.2.0 release this flag is `--output-dir`; `--out-dir` needs a source checkout of main.

See [user-flow.md](user-flow.md) for target/generalization rules.

## Pipeline

Commands use the installed CLI. For source-relative paths, run from the inner `dokimi/` package directory, not the outer checkout root. Use
`--help` on any subcommand for the full option list.

### Step 1 — Discover personas

```bash
dokimi eval personas discover \
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
dokimi eval personas validate --file personas/oneTBB.json
```

### Step 2 — Generate questions

```bash
dokimi eval questions generate \
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
dokimi answers generate \
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
dokimi eval score pointwise \
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
dokimi eval score panel \
  --product oneTBB \
  --answers answers/oneTBB.json \
  --output eval/panel/oneTBB.json
```

### Step 5 — Generate the analysis report

```bash
dokimi report generate \
  --product oneTBB \
  --eval eval/oneTBB.json \
  --questions questions/oneTBB.json \
  --output reports/oneTBB.md \
  --format markdown
```

`reports/oneTBB.md` contains summary stats (min/max/avg context-arm/baseline/delta),
top-N best and worst answers, top improvements and degradations, topic and
persona clustering, and recommendations. The summary stats cover only the questions
scored in both arms; questions scored in one arm are counted and left out of them, and
`report eval` computes the same numbers from the same file. The `reports/` directory is
git-ignored.

### Step 6 — Dashboard (optional)

```bash
dokimi dashboard generate --results-dir .
```

Aggregates per-library results into `DASHBOARD.md` / `dashboard.json` for cross-library
comparison. It reads every `eval/*.json`, `eval_*.json` and `evaluations/*.json` under
`--results-dir` (default `results`), so `.` picks up step 4's `eval/oneTBB.json`; the panel
file sits in `eval/panel/` so it does not show as a second oneTBB row. Like `report generate`, it averages both
arms and the delta, per library and per question source, over the questions scored in
both arms; a run that scored one arm only shows that arm's mean.

## Full run (single command)

```bash
PRODUCT=oneTBB
REPO=uxlfoundation/oneTBB

dokimi eval personas discover --product $PRODUCT --repo $REPO --count 5 && \
dokimi eval personas validate --file personas/$PRODUCT.json && \
dokimi eval questions generate --product $PRODUCT --personas personas/$PRODUCT.json --validate && \
dokimi answers generate --product $PRODUCT --questions questions/$PRODUCT.json --model gpt-6.1-sol && \
dokimi eval score pointwise --product $PRODUCT --answers answers/$PRODUCT.json --judge-model claude-sonnet-5-5 --judge-provider anthropic && \
dokimi report generate --product $PRODUCT --eval eval/$PRODUCT.json --questions questions/$PRODUCT.json --output reports/$PRODUCT.md
```

Advanced manual pipeline wrapper:

```bash
dokimi evaluate --product $PRODUCT --repo $REPO
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
[`data/README.md`](../data/README.md). `dokimi runs list` lists the
artifacts under `results/` ([runs.md](runs.md)).

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

## Exit codes

`dokimi` commands use these codes, so a script can tell a bad invocation from a
failed check.

| Code | Meaning | Examples |
|------|---------|----------|
| 0 | The command ran and, if it checks something, the check passed. | |
| 1 | A verdict or a gate that ran and failed, or a runtime failure (a model call, a run, a clone or fetch). | `baseline compare --strict` over a changed model, `repo reach` with a harness binary that cannot run, `eval triggers generate --strict` over a lint finding |
| 2 | A bad invocation: a missing, unreadable or malformed input file, a device given as one (`<flag> <path>: not a file`), a flag combination the command refuses, an output file that cannot be written, a missing provider credential, a source string that is malformed, unpinned or not a repository root. | `baseline save` on a bad eval file, `benchmark preflight` with no golden set, `repo scan --manifest ../x.yaml` |
| 3 | The run stopped short because a budget ran out (`--max-cost-usd`, or a call with no price) and kept the partial output; the summary covers the rest. `tools preference` today; other commands still exit 1 on a budget stop. | `tools preference --max-cost-usd 5` with calls not run |
| 5 | A crash in a leaf that prints the `cli_output.v1` document with `--json`: `backends`, `runs list\|show` and `trace inspect\|consistency\|prefilter` today; an `infra` document is printed on stdout first. Other commands still exit 1 on a crash. | `backends --json` when `detect` raises |

A command that validates its input and then runs exits 2 for the first and 1
for the second. Not every command follows this yet: `repo run` and
`eval check` exit 2 for a source that does not parse or a pinned local path inside a repository,
but still exit 1 for other input refusals (a missing or malformed manifest, a bad arm; the `✗` lines;
a `--manifest` path that leaves an unpinned local source tree as typed is exit 2, as a source refusal; a link in the tree that leads out of it is exit 1; on a pinned source it is a missing manifest, exit 1), and
`tools preference` exits 1 when every call failed and 3 when `--max-cost-usd`
or an unpriced model stopped it short. `repo run --max-batch-usd`,
`repo hits judge --max-usd` and the novelty commands' `--max-usd` still exit 1
when the budget or an unpriced call stops them.
`eval judge agreement --judges` and `eval score pairwise` exit 1, not 2, when
every judge call fails, a missing provider credential included.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| LLM calls fail / wrong provider | All providers go through LiteLLM (`dokimi/llm.py`); set the matching `*_API_KEY` and pass `--provider`/`--model`. LangChain is used only by `eval score ragas`, which wraps its judge in `ChatOpenAI`/`ChatAnthropic`; it and ragas come with the `ragas` extra (`pip install 'dokimi[ragas]'`). |
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
